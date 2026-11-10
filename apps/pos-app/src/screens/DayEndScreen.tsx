import React, { useState } from 'react';
import { ScrollView, StyleSheet, Text, View } from 'react-native';
import { Button, colors, Input, radii, spacing, typography } from '@repo/shared-ui';
import { formatPaiseToRupees } from '@repo/shared-utils';
import { ApiError, CashDay, toPaise, useCashDay, useCloseCashDay, useOpenCashDay } from '@repo/api-client';
import { QueryState } from '../components/QueryState';
import { TopBar } from '../components/TopBar';
import { SettlementLine } from '../devices/pinelabs';
import { hasTerminalPayments, terminal } from '../devices/terminal';

const money = (paise: number) => formatPaiseToRupees(paise);
const errorText = (e: unknown) => (e instanceof ApiError ? e.message : 'Something went wrong. Try again.');

const time = (iso?: string) => (iso ? new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '');
const day = (iso?: string) => (iso ? new Date(iso).toLocaleDateString([], { day: 'numeric', month: 'short' }) : '');

/**
 * The counter's cash: open the day with the cash in the drawer, close it with a blind count. One day per branch, whoever is at
 * the counter; managers see the expected cash and the difference after closing. With a card terminal, its batch is settled
 * here first (Pine Labs prints the settlement slip).
 */
export const DayEndScreen: React.FC<{ onBack: () => void }> = ({ onBack }) => {
  const dayQuery = useCashDay();
  const openDay = useOpenCashDay();
  const closeDay = useCloseCashDay();
  const cashDay = dayQuery.data;
  const isOpen = cashDay?.status === 'open';

  const [openingCash, setOpeningCash] = useState('');
  const [counted, setCounted] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [settling, setSettling] = useState(false);
  const [settlement, setSettlement] = useState<{ message: string; lines: SettlementLine[] } | null>(null);
  const [closed, setClosed] = useState<CashDay | null>(null);

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
      await openDay.mutateAsync(toPaise(openingCash || '0'));
      setClosed(null);
      setOpeningCash('');
    } catch (e) {
      setError(errorText(e));
    }
  };

  const close = async () => {
    setError(null);
    try {
      setClosed(await closeDay.mutateAsync(toPaise(counted || '0')));
      setCounted('');
    } catch (e) {
      setError(errorText(e));
    }
  };

  return (
    <View style={styles.container}>
      <TopBar title="Cash" onBack={onBack} />
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
          <Text style={styles.sectionTitle}>{hasTerminalPayments() ? '2. ' : ''}{isOpen ? 'Close the day' : 'Open the day'}</Text>
          <QueryState query={dayQuery}>
            {isOpen && cashDay ? (
              <>
                {cashDay.stale ? (
                  <Text style={styles.warning}>This day was opened on {day(cashDay.opened_at)} and never closed. Count the drawer and close it now.</Text>
                ) : null}
                <Text style={styles.body}>
                  Open since {time(cashDay.opened_at)}
                  {cashDay.opened_by ? ` (by ${cashDay.opened_by})` : ''} with {money(cashDay.opening_cash_paise ?? 0)} in the drawer.
                </Text>
                <Input label="Cash counted in the drawer (₹)" value={counted} onChangeText={setCounted} keyboardType="decimal-pad" />
                <Text style={styles.hint}>Count without looking at the expected amount; managers see the difference afterwards.</Text>
                <Button title="Close day" variant="danger" onPress={close} loading={closeDay.isPending} disabled={!counted.trim()} />
              </>
            ) : (
              <>
                {closed ? (
                  <Text style={styles.body}>
                    Day closed with {money(closed.counted_cash_paise ?? 0)} counted.
                    {closed.variance_paise !== undefined
                      ? ` Expected ${money(closed.expected_cash_paise ?? 0)}, difference ${money(closed.variance_paise)}.`
                      : ''}
                  </Text>
                ) : (
                  <Text style={styles.body}>The day is not open. Count the cash in the drawer and open it before the first sale.</Text>
                )}
                <Input label="Opening cash (₹)" value={openingCash} onChangeText={setOpeningCash} keyboardType="decimal-pad" />
                <Button title="Open day" onPress={open} loading={openDay.isPending} />
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
  warning: { ...typography.caption, color: colors.warning, backgroundColor: colors.warningBg, padding: spacing.md, borderRadius: radii.md },
  lines: { gap: spacing.xs },
  row: { flexDirection: 'row', justifyContent: 'space-between', gap: spacing.md },
  error: { ...typography.caption, color: colors.danger, backgroundColor: colors.dangerBg, padding: spacing.md, borderRadius: radii.md },
});
