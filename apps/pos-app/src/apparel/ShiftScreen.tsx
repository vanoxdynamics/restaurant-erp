import React, { useState } from 'react';
import { RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';
import { Button, colors, Input, radii, spacing, typography } from '@repo/shared-ui';
import { formatPaiseToRupees } from '@repo/shared-utils';
import { ApiError, toPaise, useApparelShiftReport, useCloseShift, useCurrentShift, useOpenShift } from '@repo/api-client';
import { QueryState } from '../components/QueryState';
import { TopBar } from '../components/TopBar';
import { useSessionStore } from '../stores/sessionStore';

const money = (paise: number) => formatPaiseToRupees(paise);
const MODE: Record<string, string> = { cash: 'Cash', card: 'Card', upi: 'UPI', credit_note: 'Store credit' };
const errorText = (e: unknown) => (e instanceof ApiError ? e.message : 'Something went wrong. Try again.');
const time = (iso?: string) => (iso ? new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '');

const Line: React.FC<{ label: string; value: string; strong?: boolean }> = ({ label, value, strong }) => (
  <View style={styles.line}>
    <Text style={[styles.label, strong ? styles.strong : null]}>{label}</Text>
    <Text style={[styles.value, strong ? styles.strong : null]}>{value}</Text>
  </View>
);

/**
 * My shift, for every role: open it with the cash in the drawer, see what was sold in it (invoices, pieces, payments by mode,
 * returns) and close it with a blind count. Sales belong to the shift in which their payment was taken.
 */
export const ShiftScreen: React.FC<{ onBack: () => void }> = ({ onBack }) => {
  const userName = useSessionStore((s) => s.user?.full_name ?? s.user?.username ?? '');
  const current = useCurrentShift();
  const isOpen = !!current.data;
  const report = useApparelShiftReport(current.data?.id, isOpen);
  const openShift = useOpenShift();
  const closeShift = useCloseShift();

  const [openingCash, setOpeningCash] = useState('');
  const [counted, setCounted] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [closedNote, setClosedNote] = useState<string | null>(null);

  const open = async () => {
    setError(null);
    try {
      await openShift.mutateAsync(toPaise(openingCash || '0'));
      setClosedNote(null);
      setOpeningCash('');
      void report.refetch();
    } catch (e) {
      setError(errorText(e));
    }
  };

  const close = async () => {
    setError(null);
    try {
      const shift = await closeShift.mutateAsync(toPaise(counted || '0'));
      setClosedNote(
        shift.variance_paise !== undefined
          ? `Shift closed. Expected ${money(shift.expected_cash_paise ?? 0)}, difference ${money(shift.variance_paise)}.`
          : 'Shift closed. Your count was recorded.',
      );
      setCounted('');
    } catch (e) {
      setError(errorText(e));
    }
  };

  const r = report.data;

  return (
    <View style={styles.container}>
      <TopBar title="My shift" subtitle={userName} onBack={onBack} />
      <QueryState query={current}>
        <ScrollView
          contentContainerStyle={styles.body}
          keyboardShouldPersistTaps="handled"
          refreshControl={<RefreshControl refreshing={report.isRefetching} onRefresh={() => void report.refetch()} />}
        >
          {error ? <Text style={styles.error}>{error}</Text> : null}
          {closedNote ? <Text style={styles.note}>{closedNote}</Text> : null}

          {isOpen && current.data ? (
            <>
              <View style={styles.card}>
                <Text style={styles.title}>Sales this shift</Text>
                <Text style={styles.label}>
                  Open since {time(current.data.start_time)} with {money(current.data.opening_cash_paise)} in the drawer.
                </Text>
                {r ? (
                  <>
                    <Line label={`${r.sales.count} paid ${r.sales.count === 1 ? 'invoice' : 'invoices'} · ${r.sales.units} pieces`} value={money(r.sales.total_paise)} strong />
                    <Line label="Discounts given" value={`−${money(r.sales.discount_paise)}`} />
                  </>
                ) : (
                  <Text style={styles.label}>{report.isLoading ? 'Loading…' : 'No sales yet.'}</Text>
                )}
              </View>
              {r ? (
                <>
                  <View style={styles.card}>
                    <Text style={styles.title}>Payments taken</Text>
                    {r.payments.length === 0 ? <Text style={styles.label}>No payments yet.</Text> : null}
                    {r.payments.map((p) => (
                      <Line key={p.mode} label={`${MODE[p.mode] ?? p.mode} (${p.count})`} value={money(p.amount_paise)} />
                    ))}
                  </View>
                  <View style={styles.card}>
                    <Text style={styles.title}>Returns</Text>
                    <Line label={`Taken back (${r.returns.count})`} value={money(r.returns.total_paise)} />
                    <Line label="Refunded in cash" value={money(r.returns.cash_paise)} />
                    <Line label="Given as store credit" value={money(r.returns.store_credit_paise)} />
                  </View>
                </>
              ) : null}
              <View style={styles.card}>
                <Text style={styles.title}>Close my shift</Text>
                <Input label="Cash counted in the drawer (₹)" value={counted} onChangeText={setCounted} keyboardType="decimal-pad" />
                <Text style={styles.hint}>Count without looking at the expected amount; managers see the difference afterwards.</Text>
                <Button title="Close shift" variant="danger" onPress={close} loading={closeShift.isPending} disabled={!counted.trim()} />
              </View>
            </>
          ) : (
            <View style={styles.card}>
              <Text style={styles.title}>Open my shift</Text>
              <Text style={styles.label}>No shift is open. Count the cash in your drawer and open one; your sales are shown per shift.</Text>
              <Input label="Opening cash (₹)" value={openingCash} onChangeText={setOpeningCash} keyboardType="decimal-pad" />
              <Button title="Open shift" onPress={open} loading={openShift.isPending} />
            </View>
          )}
        </ScrollView>
      </QueryState>
    </View>
  );
};

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  body: { padding: spacing.lg, gap: spacing.md, maxWidth: 560, width: '100%', alignSelf: 'center' },
  card: { gap: spacing.xs, padding: spacing.lg, borderRadius: radii.lg, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.surfaceBorder },
  title: { ...typography.bodyBold, color: colors.text, marginBottom: spacing.xs },
  line: { flexDirection: 'row', justifyContent: 'space-between', gap: spacing.md },
  label: { ...typography.body, color: colors.textSecondary, flexShrink: 1 },
  value: { ...typography.body, color: colors.text },
  strong: { ...typography.bodyBold, color: colors.text },
  hint: { ...typography.caption, color: colors.textSecondary },
  error: { ...typography.caption, color: colors.danger, backgroundColor: colors.dangerBg, padding: spacing.md, borderRadius: radii.md },
  note: { ...typography.caption, color: colors.text, backgroundColor: colors.surface, padding: spacing.md, borderRadius: radii.md },
});
