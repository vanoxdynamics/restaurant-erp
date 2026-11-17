import React, { useEffect, useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { Button, colors, Input, radii, spacing, typography } from '@repo/shared-ui';
import { formatPaiseToRupees } from '@repo/shared-utils';
import { ApiError, apparelReturnsApi, ApparelStoreCredit, newIdempotencyKey } from '@repo/api-client';

const money = (paise: number) => formatPaiseToRupees(paise);

export interface StoreCreditTenderProps {
  saleId: string;
  balanceDuePaise: number;
  /** The credit note just made by an exchange, so the cashier does not retype its code. */
  initialCode?: string;
  /** The credit was applied: the sale changed, fetch it again. */
  onApplied: () => Promise<unknown> | void;
}

/**
 * Pay (part of) a sale with store credit from an earlier return. The credit pays what it holds, up to what the sale owes;
 * anything left is taken with cash, card or UPI below. Store credit never gives change.
 */
export const StoreCreditTender: React.FC<StoreCreditTenderProps> = ({ saleId, balanceDuePaise, initialCode, onApplied }) => {
  const [code, setCode] = useState(initialCode ?? '');
  const [credit, setCredit] = useState<ApparelStoreCredit | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [key, setKey] = useState(newIdempotencyKey);

  const message = (e: unknown) => (e instanceof ApiError ? e.message : 'Something went wrong. Try again.');

  const check = async (value = code) => {
    setError(null);
    setBusy(true);
    try {
      setCredit(await apparelReturnsApi.storeCredit(value));
    } catch (e) {
      setCredit(null);
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };

  // A credit handed over from an exchange shows its balance straight away.
  useEffect(() => {
    if (initialCode) void check(initialCode);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialCode]);

  const usable = credit?.status === 'open' && credit.balance_paise > 0;
  const applies = credit ? Math.min(credit.balance_paise, balanceDuePaise) : 0;

  const apply = async () => {
    if (!credit) return;
    setError(null);
    setBusy(true);
    try {
      await apparelReturnsApi.payWithCredit(saleId, credit.code, balanceDuePaise, key);
      setKey(newIdempotencyKey());
      setCredit(null);
      setCode('');
      await onApplied();
    } catch (e) {
      setError(message(e));
      setKey(newIdempotencyKey());
    } finally {
      setBusy(false);
    }
  };

  return (
    <View style={styles.box}>
      <Text style={styles.title}>Store credit</Text>
      <View style={styles.row}>
        <View style={{ flex: 1 }}>
          <Input label="Credit note number" value={code} onChangeText={(v) => { setCode(v); setCredit(null); }} autoCapitalize="characters" placeholder="CN/…" />
        </View>
        <Button title="Check" variant="secondary" onPress={() => check()} disabled={!code.trim() || busy} />
      </View>
      {credit ? (
        <Text style={styles.info}>
          {credit.status === 'open' ? `Balance ${money(credit.balance_paise)}` : `This credit is ${credit.status}.`}
          {usable ? ` It will pay ${money(applies)} of the ${money(balanceDuePaise)} due.` : ''}
        </Text>
      ) : null}
      {error ? <Text style={styles.error}>{error}</Text> : null}
      {usable ? <Button title={`Use ${money(applies)} store credit`} onPress={apply} loading={busy} /> : null}
    </View>
  );
};

const styles = StyleSheet.create({
  box: { gap: spacing.sm, padding: spacing.md, borderRadius: radii.md, borderWidth: 1, borderColor: colors.surfaceBorder, backgroundColor: colors.surfaceLight },
  title: { ...typography.bodyBold, color: colors.text },
  row: { flexDirection: 'row', alignItems: 'flex-end', gap: spacing.sm },
  info: { ...typography.caption, color: colors.textSecondary },
  error: { ...typography.caption, color: colors.danger },
});
