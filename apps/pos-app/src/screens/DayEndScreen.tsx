import React, { useState } from 'react';
import { ScrollView, StyleSheet, Text, View } from 'react-native';
import { Button, colors, Input, radii, spacing, typography } from '@repo/shared-ui';
import { formatPaiseToRupees } from '@repo/shared-utils';
import { ApiError, toPaise, useCloseShift, useCurrentShift, useOpenShift } from '@repo/api-client';
import { QueryState } from '../components/QueryState';
import { TopBar } from '../components/TopBar';
import { SettlementLine } from '../devices/pinelabs';
import { hasTerminalPayments, terminal } from '../devices/terminal';

const money = (paise: number) => formatPaiseToRupees(paise);
const errorText = (e: unknown) => (e instanceof ApiError ? e.message : 'Something went wrong. Try again.');

/**
 * Day end at the counter: settle the card terminal's batch (Pine Labs prints its settlement slip), then close the cash shift
 * with a blind count. The shift is opened here too, at the start of the day.
 */
export const DayEndScreen: React.FC<{ onBack: () => void }> = ({ onBack }) => {
  const shiftQuery = useCurrentShift();
  const openShift = useOpenShift();
  const closeShift = useCloseShift();
  const shift = shiftQuery.data ?? null;

  const [openingCash, setOpeningCash] = useState('');
  const [counted, setCounted] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [settling, setSettling] = useState(false);
  const [settlement, setSettlement] = useState<{ message: string; lines: SettlementLine[] } | null>(null);
  const [closed, setClosed] = useState<{ expected?: number; variance?: number } | null>(null);

  const settle = async () => {
    setError(null);
    setSettling(true);
    try {
      const result = await terminal.settle();
      if (!result.ok) setError(`Settlement did not go through: ${result.message}`);
      setSettlement({ message: result.message, lines: result.lines });
    } finally {
      setSettling(false);
    }
  };

  const open = async () => {
    setError(null);
    try {
      await openShift.mutateAsync(toPaise(openingCash || '0'));
      setClosed(null);
    } catch (e) {
      setError(errorText(e));
    }
  };

  const close = async () => {
    setError(null);
    try {
      const result = await closeShift.mutateAsync(toPaise(counted || '0'));
      setClosed({ expected: result.expected_cash_paise, variance: result.variance_paise });
      setCounted('');
    } catch (e) {
      setError(errorText(e));
    }
  };

  return (
    <View style={styles.container}>
      <TopBar title="Day end" onBack={onBack} />
      <ScrollView contentContainerStyle={styles.scroll} keyboardShouldPersistTaps="handled">
        {error ? <Text style={styles.error}>{error}</Text> : null}

        {hasTerminalPayments() ? (
          <View style={styles.card}>
            <Text style={styles.sectionTitle}>1. Settle card payments</Text>
            <Text style={styles.hint}>Sends today’s card batch to the bank. Pine Labs prints the settlement slip. Reversals are no longer possible after this.</Text>
            <Button title="Settle now" onPress={settle} loading={settling} />
            {settlement ? (
              <View style={styles.lines}>
                <Text style={styles.body}>{settlement.message}</Text>
                {settlement.lines.map((l, i) => (
                  <View key={`${l.bank}-${i}`} style={styles.row}>
                    <Text style={styles.body}>{l.bank || 'Bank'} {l.terminalId ? `· ${l.terminalId}` : ''}</Text>
                    <Text style={styles.body}>
                      {l.debitCount} sales · {money(l.debitPaise)}
                      {l.creditCount ? ` · ${l.creditCount} refunds ${money(l.creditPaise)}` : ''}
                    </Text>
                  </View>
                ))}
              </View>
            ) : null}
          </View>
        ) : null}

        <View style={styles.card}>
          <Text style={styles.sectionTitle}>{hasTerminalPayments() ? '2. ' : ''}Cash shift</Text>
          <QueryState query={shiftQuery}>
            {shift ? (
              <>
                <Text style={styles.body}>
                  Open since {new Date(shift.start_time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} with{' '}
                  {money(shift.opening_cash_paise)} in the drawer.
                </Text>
                <Input label="Cash counted in the drawer (₹)" value={counted} onChangeText={setCounted} keyboardType="decimal-pad" />
                <Text style={styles.hint}>Count without looking at the expected amount; managers see the difference afterwards.</Text>
                <Button title="Close shift" variant="danger" onPress={close} loading={closeShift.isPending} disabled={!counted.trim()} />
              </>
            ) : (
              <>
                {closed ? (
                  <Text style={styles.body}>
                    Shift closed.
                    {closed.variance !== undefined ? ` Expected ${money(closed.expected ?? 0)}, difference ${money(closed.variance)}.` : ''}
                  </Text>
                ) : (
                  <Text style={styles.body}>No shift is open. Open one at the start of the day so cash is counted per shift.</Text>
                )}
                <Input label="Opening cash (₹)" value={openingCash} onChangeText={setOpeningCash} keyboardType="decimal-pad" />
                <Button title="Open shift" onPress={open} loading={openShift.isPending} />
              </>
            )}
          </QueryState>
        </View>
      </ScrollView>
    </View>
  );
};

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  scroll: { padding: spacing.lg, gap: spacing.lg, maxWidth: 560, width: '100%', alignSelf: 'center' },
  card: {
    backgroundColor: colors.surface,
    borderRadius: radii.lg,
    borderWidth: 1,
    borderColor: colors.surfaceBorder,
    padding: spacing.lg,
    gap: spacing.md,
  },
  sectionTitle: { ...typography.bodyBold, color: colors.text },
  body: { ...typography.body, color: colors.text },
  hint: { ...typography.caption, color: colors.textSecondary },
  lines: { gap: spacing.xs },
  row: { flexDirection: 'row', justifyContent: 'space-between', gap: spacing.md },
  error: { ...typography.caption, color: colors.danger, backgroundColor: colors.dangerBg, padding: spacing.md, borderRadius: radii.md },
});
