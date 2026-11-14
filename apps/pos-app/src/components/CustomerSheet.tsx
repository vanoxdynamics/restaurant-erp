import React, { useEffect, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { Button, colors, Input, Modal, radii, spacing, typography } from '@repo/shared-ui';
import { formatPaiseToRupees } from '@repo/shared-utils';
import { ApiError, apiClient, Customer, useCreateCustomer, useCustomers } from '@repo/api-client';

export interface CustomerSheetProps {
  visible: boolean;
  /** The customer already on the order, if any: offered for removal. */
  current?: Customer;
  onClose: () => void;
  /** A customer was chosen or added; `null` takes the customer off the order. */
  onPick: (customer: Customer | null) => void;
  busy?: boolean;
}

const digitsOf = (text: string) => text.replace(/[\s+().-]/g, '');
const looksLikePhone = (text: string) => /^\d{3,}$/.test(digitsOf(text));

/** What sits under a customer's name in a list: their phone, how often they came and what they spent. */
export const customerSummary = (c: Customer) =>
  [c.phone, c.visits ? `${c.visits} ${c.visits === 1 ? 'visit' : 'visits'} · ${formatPaiseToRupees(c.total_spent_paise)}` : 'New', c.gstin ? 'GST' : null]
    .filter(Boolean)
    .join(' · ');

/**
 * Find a customer by phone number or name, or add a new one, and hand them back. The cashier types what the customer says (most
 * often a phone number); whatever was typed is carried into the new-customer form so nothing is typed twice.
 */
export const CustomerSheet: React.FC<CustomerSheetProps> = ({ visible, current, onClose, onPick, busy }) => {
  const [search, setSearch] = useState('');
  const [term, setTerm] = useState('');
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState('');
  const [phone, setPhone] = useState('');
  const [gstin, setGstin] = useState('');
  const [error, setError] = useState<string | null>(null);
  const create = useCreateCustomer();
  const query = useCustomers(term, { enabled: visible && !adding });

  // Wait for a pause in typing before asking the server.
  useEffect(() => {
    const timer = setTimeout(() => setTerm(search.trim()), 250);
    return () => clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    if (!visible) return;
    setSearch('');
    setTerm('');
    setAdding(false);
    setError(null);
  }, [visible]);

  const startAdding = () => {
    const typed = search.trim();
    setName(looksLikePhone(typed) ? '' : typed);
    setPhone(looksLikePhone(typed) ? digitsOf(typed) : '');
    setGstin('');
    setError(null);
    setAdding(true);
  };

  const save = async () => {
    setError(null);
    try {
      onPick(await create.mutateAsync({ name, phone, gstin }));
    } catch (e) {
      // The number already belongs to someone (perhaps added at another counter a moment ago): use that customer.
      if (e instanceof ApiError && e.code === 'customer_phone_taken' && typeof e.details?.customer_id === 'string') {
        try {
          const existing = await apiClient.getCustomer(e.details.customer_id);
          if (existing.is_active) return onPick(existing);
          setError(`${existing.name} already has this number but was hidden. A manager can restore them in the admin app.`);
        } catch {
          setError(e.message);
        }
      } else {
        setError(e instanceof ApiError ? e.message : 'Could not save the customer. Try again.');
      }
    }
  };

  const customers = query.data ?? [];
  const gstinOk = !gstin.trim() || /^[0-9A-Za-z]{15}$/.test(gstin.trim());
  const phoneOk = !phone.trim() || /^\d{7,15}$/.test(digitsOf(phone));

  return (
    <Modal visible={visible} onClose={onClose} title={adding ? 'New customer' : 'Customer'} subtitle={adding ? undefined : 'Printed on the bill'}>
      {adding ? (
        <View style={styles.form}>
          <Input label="Name" value={name} onChangeText={setName} autoFocus={!name} autoCapitalize="words" />
          <Input
            label="Phone"
            value={phone}
            onChangeText={setPhone}
            keyboardType="phone-pad"
            autoFocus={!!name}
            error={phoneOk ? undefined : 'Enter 7 to 15 digits'}
          />
          <Input
            label="GSTIN (for a business)"
            value={gstin}
            onChangeText={(v) => setGstin(v.toUpperCase())}
            autoCapitalize="characters"
            autoCorrect={false}
            error={gstinOk ? undefined : 'A GSTIN has 15 letters and digits'}
          />
          {error ? <Text style={styles.error}>{error}</Text> : null}
          <View style={styles.actions}>
            <Button title="Back" variant="secondary" onPress={() => setAdding(false)} />
            <Button
              title="Save and use"
              onPress={save}
              loading={create.isPending || busy}
              disabled={!name.trim() || !gstinOk || !phoneOk}
              style={{ flex: 1 }}
            />
          </View>
        </View>
      ) : (
        <View style={styles.form}>
          <Input
            value={search}
            onChangeText={setSearch}
            placeholder="Phone number or name"
            autoFocus
            autoCorrect={false}
            returnKeyType="search"
            accessibilityLabel="Find a customer by phone number or name"
          />
          <ScrollView style={styles.list} keyboardShouldPersistTaps="handled">
            {customers.map((c) => (
              <Pressable
                key={c.id}
                onPress={() => onPick(c)}
                disabled={busy}
                accessibilityRole="button"
                accessibilityLabel={`Use ${c.name}`}
                style={({ pressed }) => [styles.row, pressed ? styles.rowPressed : null, current?.id === c.id ? styles.rowCurrent : null]}
              >
                <Text style={styles.name} numberOfLines={1}>{c.name}</Text>
                <Text style={styles.meta} numberOfLines={1}>{customerSummary(c)}</Text>
              </Pressable>
            ))}
            {query.isError ? <Text style={styles.error}>Could not load customers. Check the connection.</Text> : null}
            {!query.isError && !query.isLoading && customers.length === 0 ? (
              <Text style={styles.empty}>{term ? `No customer matches "${term}".` : 'No customers yet.'}</Text>
            ) : null}
          </ScrollView>
          <View style={styles.actions}>
            {current ? (
              <Button title="Remove" variant="secondary" onPress={() => onPick(null)} disabled={busy} textStyle={{ color: colors.danger }} />
            ) : null}
            <Button title={term ? `Add "${term}"` : 'New customer'} onPress={startAdding} disabled={busy} style={{ flex: 1 }} />
          </View>
        </View>
      )}
    </Modal>
  );
};

const styles = StyleSheet.create({
  form: { gap: spacing.md },
  list: { maxHeight: 280, minHeight: 120 },
  row: { paddingVertical: spacing.md, paddingHorizontal: spacing.md, borderRadius: radii.md, minHeight: 56, justifyContent: 'center' },
  rowPressed: { backgroundColor: colors.surfaceLight },
  rowCurrent: { backgroundColor: colors.primaryGlow },
  name: { ...typography.bodyBold, color: colors.text },
  meta: { ...typography.caption, color: colors.textSecondary },
  empty: { ...typography.body, color: colors.textMuted, textAlign: 'center', marginTop: spacing.lg },
  error: { ...typography.caption, color: colors.danger },
  actions: { flexDirection: 'row', gap: spacing.sm },
});
