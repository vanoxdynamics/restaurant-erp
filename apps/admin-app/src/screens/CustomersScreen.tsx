import React, { useEffect, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { Badge, Button, colors, Input, Modal, radii, spacing, typography } from '@repo/shared-ui';
import { formatPaiseToRupees } from '@repo/shared-utils';
import {
  ApiError,
  Customer,
  CustomerInput,
  useCreateCustomer,
  useCustomerBills,
  useCustomers,
  useUpdateCustomer,
} from '@repo/api-client';
import { QueryState } from '../components/QueryState';

const EMPTY: Required<CustomerInput> = { name: '', phone: '', email: '', gstin: '', address: '', notes: '' };

const day = (iso?: string) =>
  iso ? new Date(iso).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' }) : '';

const summary = (c: Customer) =>
  c.visits
    ? `${c.visits} ${c.visits === 1 ? 'visit' : 'visits'} · ${formatPaiseToRupees(c.total_spent_paise)} · last ${day(c.last_visit_at)}`
    : 'No paid bills yet';

/**
 * The business's customers: who they are, how often they come and what they spend. Counters add customers while billing; here
 * the owner corrects details, adds a GSTIN, sees a customer's bills, and hides ones added by mistake.
 */
export const CustomersScreen: React.FC = () => {
  const [search, setSearch] = useState('');
  const [term, setTerm] = useState('');
  const [showHidden, setShowHidden] = useState(false);
  const [open, setOpen] = useState<Customer | 'new' | null>(null);
  const [form, setForm] = useState(EMPTY);
  const [error, setError] = useState<string | null>(null);

  const query = useCustomers(term, { includeInactive: showHidden });
  const create = useCreateCustomer();
  const update = useUpdateCustomer();
  const editing = open && open !== 'new' ? open : undefined;
  const bills = useCustomerBills(editing?.id);

  useEffect(() => {
    const timer = setTimeout(() => setTerm(search.trim()), 250);
    return () => clearTimeout(timer);
  }, [search]);

  const show = (customer: Customer | 'new') => {
    setForm(
      customer === 'new'
        ? EMPTY
        : { name: customer.name, phone: customer.phone ?? '', email: customer.email ?? '', gstin: customer.gstin ?? '',
            address: customer.address ?? '', notes: customer.notes ?? '' },
    );
    setError(null);
    setOpen(customer);
  };

  const set = (key: keyof CustomerInput) => (value: string) => setForm((f) => ({ ...f, [key]: value }));

  const save = async () => {
    setError(null);
    try {
      if (editing) await update.mutateAsync({ customerId: editing.id, changes: form });
      else await create.mutateAsync(form);
      setOpen(null);
    } catch (e) {
      setError(
        e instanceof ApiError && e.code === 'customer_phone_taken'
          ? `${String(e.details?.name ?? 'Another customer')} already has this phone number.`
          : e instanceof ApiError ? e.message : 'Could not save. Try again.',
      );
    }
  };

  const setActive = async (active: boolean) => {
    if (!editing) return;
    setError(null);
    try {
      await update.mutateAsync({ customerId: editing.id, changes: { is_active: active } });
      setOpen(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not save. Try again.');
    }
  };

  const customers = query.data ?? [];
  const gstinOk = !form.gstin.trim() || /^[0-9A-Za-z]{15}$/.test(form.gstin.trim());
  const saving = create.isPending || update.isPending;

  return (
    <View style={styles.container}>
      <View style={styles.head}>
        <Input
          value={search}
          onChangeText={setSearch}
          placeholder="Search by name or phone"
          autoCorrect={false}
          accessibilityLabel="Search customers by name or phone"
          containerStyle={{ flex: 1 }}
        />
        <Button title="Add" onPress={() => show('new')} />
      </View>
      <Pressable onPress={() => setShowHidden((v) => !v)} accessibilityRole="checkbox" accessibilityState={{ checked: showHidden }} style={styles.toggle}>
        <Text style={styles.toggleText}>{showHidden ? '☑' : '☐'} Show hidden customers</Text>
      </Pressable>

      <QueryState
        query={query}
        isEmpty={customers.length === 0}
        emptyText={term ? `No customer matches "${term}".` : 'No customers yet. They are added at the counter while billing, or here.'}
      >
        <ScrollView contentContainerStyle={styles.list}>
          {customers.map((c) => (
            <Pressable
              key={c.id}
              onPress={() => show(c)}
              accessibilityRole="button"
              accessibilityLabel={`${c.name}, ${summary(c)}`}
              style={({ pressed }) => [styles.card, pressed ? { opacity: 0.85 } : null]}
            >
              <View style={styles.cardHead}>
                <Text style={styles.name} numberOfLines={1}>{c.name}</Text>
                {!c.is_active ? <Badge label="hidden" variant="neutral" /> : c.gstin ? <Badge label="GST" variant="available" /> : null}
              </View>
              {c.phone ? <Text style={styles.meta}>{c.phone}</Text> : null}
              <Text style={styles.meta}>{summary(c)}</Text>
            </Pressable>
          ))}
        </ScrollView>
      </QueryState>

      <Modal visible={open !== null} onClose={() => setOpen(null)} title={editing ? editing.name : 'New customer'} subtitle={editing ? summary(editing) : undefined}>
        <ScrollView contentContainerStyle={styles.form} keyboardShouldPersistTaps="handled">
          <Input label="Name" value={form.name} onChangeText={set('name')} autoCapitalize="words" />
          <Input label="Phone" value={form.phone} onChangeText={set('phone')} keyboardType="phone-pad" />
          <Input
            label="GSTIN (for a business)"
            value={form.gstin}
            onChangeText={(v) => set('gstin')(v.toUpperCase())}
            autoCapitalize="characters"
            autoCorrect={false}
            error={gstinOk ? undefined : 'A GSTIN has 15 letters and digits'}
          />
          <Input label="Email" value={form.email} onChangeText={set('email')} keyboardType="email-address" autoCapitalize="none" autoCorrect={false} />
          <Input label="Address" value={form.address} onChangeText={set('address')} />
          <Input label="Notes" value={form.notes} onChangeText={set('notes')} />
          {error ? <Text style={styles.error}>{error}</Text> : null}
          <View style={styles.actions}>
            {editing ? (
              <Button
                title={editing.is_active ? 'Hide' : 'Restore'}
                variant="secondary"
                onPress={() => setActive(!editing.is_active)}
                disabled={saving}
              />
            ) : null}
            <Button title="Save" onPress={save} loading={saving} disabled={!form.name.trim() || !gstinOk} style={{ flex: 1 }} />
          </View>

          {editing && (bills.data ?? []).length > 0 ? (
            <View style={styles.bills}>
              <Text style={styles.billsTitle}>Bills</Text>
              {(bills.data ?? []).map((b) => (
                <View key={b.id} style={styles.billRow}>
                  <Text style={styles.meta}>{b.bill_number} · {day(b.created_at)}{b.status !== 'paid' ? ` · ${b.status.replace('_', ' ')}` : ''}</Text>
                  <Text style={styles.billAmount}>{formatPaiseToRupees(b.grand_total_paise)}</Text>
                </View>
              ))}
            </View>
          ) : null}
        </ScrollView>
      </Modal>
    </View>
  );
};

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  head: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingHorizontal: spacing.xl, paddingTop: spacing.md },
  toggle: { paddingHorizontal: spacing.xl, paddingVertical: spacing.sm, minHeight: 36, justifyContent: 'center' },
  toggleText: { ...typography.caption, color: colors.textSecondary },
  list: { paddingHorizontal: spacing.xl, paddingBottom: spacing.xxxl, gap: spacing.sm },
  card: {
    padding: spacing.lg, borderRadius: radii.lg, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.surfaceBorder, gap: 2,
  },
  cardHead: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: spacing.sm },
  name: { ...typography.bodyBold, color: colors.text, flex: 1 },
  meta: { ...typography.caption, color: colors.textSecondary },
  form: { gap: spacing.md, paddingBottom: spacing.lg },
  error: { ...typography.caption, color: colors.danger },
  actions: { flexDirection: 'row', gap: spacing.sm },
  bills: { gap: spacing.xs, marginTop: spacing.sm },
  billsTitle: { ...typography.bodyBold, color: colors.text },
  billRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', minHeight: 32 },
  billAmount: { ...typography.bodyBold, color: colors.text },
});
