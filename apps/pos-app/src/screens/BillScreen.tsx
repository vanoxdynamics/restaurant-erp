import React, { useEffect, useState } from 'react';
import { ScrollView, StyleSheet, Text, View } from 'react-native';
import { Badge, BadgeProps, Button, colors, Input, radii, spacing, typography } from '@repo/shared-ui';
import { formatPaiseToRupees, PaymentMode } from '@repo/shared-utils';
import {
  ApiError,
  Bill,
  newIdempotencyKey,
  toPaise,
  toRupees,
  useGenerateBill,
  useOrder,
  useOrderBill,
  useRecordPayment,
} from '@repo/api-client';
import { Choice } from '../components/Choice';
import { QueryState } from '../components/QueryState';
import { TopBar } from '../components/TopBar';

const money = (paise: number) => formatPaiseToRupees(paise);
const errorText = (e: unknown) => (e instanceof ApiError ? e.message : 'Something went wrong. Try again.');

const BILL_BADGE: Record<Bill['status'], BadgeProps['variant']> = {
  unpaid: 'warning',
  partially_paid: 'warning',
  paid: 'available',
  voided: 'danger',
  refunded: 'neutral',
};

/**
 * Tenders the cashier can pick. Everything is keyed in by hand for now; when the card terminal is
 * integrated it becomes another entry here that starts a terminal sale instead of asking for an
 * approval code.
 */
const TENDERS: { mode: PaymentMode; label: string; referenceLabel?: string }[] = [
  { mode: PaymentMode.CASH, label: 'Cash' },
  { mode: PaymentMode.CARD, label: 'Card', referenceLabel: 'Approval code' },
  { mode: PaymentMode.UPI, label: 'UPI', referenceLabel: 'UTR / transaction ID' },
];

const Row: React.FC<{ label: string; value: string; strong?: boolean }> = ({ label, value, strong }) => (
  <View style={styles.row}>
    <Text style={strong ? styles.rowStrong : styles.rowLabel}>{label}</Text>
    <Text style={strong ? styles.rowStrong : styles.rowValue}>{value}</Text>
  </View>
);

export interface BillScreenProps {
  orderId: string;
  title: string;
  onBack: () => void;
  onDone: () => void;
}

export const BillScreen: React.FC<BillScreenProps> = ({ orderId, title, onBack, onDone }) => {
  const orderQuery = useOrder(orderId);
  const billQuery = useOrderBill(orderId);
  const generate = useGenerateBill();
  const pay = useRecordPayment();

  const order = orderQuery.data;
  const bill = billQuery.data ?? undefined;
  const balance = bill?.balance_due_paise ?? 0;
  const payable = bill?.status === 'unpaid' || bill?.status === 'partially_paid';

  const [mode, setMode] = useState<PaymentMode>(PaymentMode.CASH);
  const [amount, setAmount] = useState('');
  const [reference, setReference] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [change, setChange] = useState(0);

  // One key per distinct attempt: retrying the same tender is safe, a different tender gets a new key.
  const [generateKey, setGenerateKey] = useState(newIdempotencyKey);
  const [payKey, setPayKey] = useState(newIdempotencyKey);
  useEffect(() => setPayKey(newIdempotencyKey()), [mode, amount, reference, bill?.paid_paise]);

  // Suggest the full balance; it follows the balance down after each partial payment.
  useEffect(() => {
    if (payable) setAmount(toRupees(balance));
  }, [payable, balance]);

  const tender = TENDERS.find((t) => t.mode === mode)!;
  const amountPaise = toPaise(amount);
  const overBalance = mode !== PaymentMode.CASH && amountPaise > balance;
  const missingReference = !!tender.referenceLabel && !reference.trim();
  const canPay = amountPaise > 0 && !overBalance && !missingReference;

  const generateBill = async () => {
    setError(null);
    try {
      await generate.mutateAsync({ orderId, idempotencyKey: generateKey });
      setGenerateKey(newIdempotencyKey());
    } catch (e) {
      setError(errorText(e));
    }
  };

  const recordPayment = async () => {
    if (!bill) return;
    setError(null);
    try {
      const result = await pay.mutateAsync({
        orderId,
        billId: bill.id,
        payment: { payment_mode: mode, amount_paise: amountPaise, reference_id: reference.trim() || undefined },
        idempotencyKey: payKey,
      });
      setChange(result.change_due_paise);
      setReference('');
    } catch (e) {
      setError(errorText(e));
    }
  };

  return (
    <View style={styles.container}>
      <TopBar title={`Bill · ${title}`} subtitle={bill?.bill_number} onBack={onBack} />
      <QueryState query={billQuery.isLoading || billQuery.isError ? billQuery : orderQuery}>
        <ScrollView contentContainerStyle={styles.scroll}>
          {error ? <Text style={styles.error}>{error}</Text> : null}

          {!bill ? (
            <View style={styles.card}>
              <Text style={styles.sectionTitle}>Ready to bill</Text>
              <Row label="Items total" value={money(order?.subtotal_paise ?? 0)} />
              <Text style={styles.hint}>GST is added when the bill is generated. Every item must be sent to the kitchen first.</Text>
              <Button title="Generate bill" size="lg" onPress={generateBill} loading={generate.isPending} />
            </View>
          ) : (
            <>
              <View style={styles.card}>
                <View style={styles.headRow}>
                  <Text style={styles.sectionTitle}>{bill.bill_number}</Text>
                  <Badge label={bill.status} variant={BILL_BADGE[bill.status]} />
                </View>
                <Row label="Subtotal" value={money(bill.subtotal_paise)} />
                {bill.discount_paise > 0 ? <Row label="Discount" value={`- ${money(bill.discount_paise)}`} /> : null}
                <Row label="CGST" value={money(bill.cgst_paise)} />
                <Row label="SGST" value={money(bill.sgst_paise)} />
                <Row label="Total" value={money(bill.grand_total_paise)} strong />
                {bill.payments.length > 0 ? (
                  <View style={styles.payments}>
                    {bill.payments.map((p) => (
                      <Row
                        key={p.id}
                        label={`${p.payment_mode.toUpperCase()}${p.reference_id ? ` · ${p.reference_id}` : ''}`}
                        value={money(p.amount_paise)}
                      />
                    ))}
                  </View>
                ) : null}
                <Row label="Balance due" value={money(balance)} strong />
              </View>

              {payable ? (
                <View style={styles.card}>
                  <Text style={styles.sectionTitle}>Take payment</Text>
                  <View style={styles.choices}>
                    {TENDERS.map((t) => (
                      <Choice key={t.mode} label={t.label} selected={mode === t.mode} onPress={() => setMode(t.mode)} />
                    ))}
                  </View>
                  <Input
                    label="Amount (₹)"
                    value={amount}
                    onChangeText={setAmount}
                    keyboardType="decimal-pad"
                    error={overBalance ? `More than the balance of ${money(balance)}` : undefined}
                  />
                  {tender.referenceLabel ? (
                    <Input label={tender.referenceLabel} value={reference} onChangeText={setReference} autoCapitalize="characters" />
                  ) : null}
                  <Button
                    title={`Record ${tender.label} ${money(amountPaise)}`}
                    size="lg"
                    onPress={recordPayment}
                    disabled={!canPay}
                    loading={pay.isPending}
                  />
                  <Text style={styles.hint}>Enter a smaller amount to split the bill across several payments.</Text>
                </View>
              ) : null}

              {bill.status === 'paid' ? (
                <View style={styles.card}>
                  <Text style={styles.paid}>Paid in full</Text>
                  {change > 0 ? <Text style={styles.change}>Return {money(change)} change to the guest</Text> : null}
                  <Button title="Done" size="lg" onPress={onDone} />
                </View>
              ) : null}
            </>
          )}
        </ScrollView>
      </QueryState>
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
  headRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  sectionTitle: { ...typography.bodyBold, color: colors.text },
  row: { flexDirection: 'row', justifyContent: 'space-between' },
  rowLabel: { ...typography.body, color: colors.textSecondary },
  rowValue: { ...typography.body, color: colors.text },
  rowStrong: { ...typography.bodyBold, color: colors.text },
  payments: {
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.surfaceBorder,
    paddingTop: spacing.md,
    gap: spacing.sm,
  },
  choices: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  hint: { ...typography.caption, color: colors.textMuted },
  error: { ...typography.caption, color: colors.danger },
  paid: { ...typography.bodyBold, color: colors.success },
  change: { ...typography.body, color: colors.text },
});
