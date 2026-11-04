import React, { useEffect, useState } from 'react';
import { ScrollView, StyleSheet, Text, View } from 'react-native';
import { Badge, BadgeProps, Button, colors, fonts, Input, Modal, radii, spacing, typography } from '@repo/shared-ui';
import { formatPaiseToRupees, PaymentMode } from '@repo/shared-utils';
import {
  ApiError,
  apiClient,
  Bill,
  BillPayment,
  newIdempotencyKey,
  toPaise,
  toRupees,
  useCompleteTerminalPayment,
  useGenerateBill,
  useOrder,
  useOrderBill,
  useRecordPayment,
  useReverseTerminalPayment,
  useStartTerminalPayment,
} from '@repo/api-client';
import { Choice } from '../components/Choice';
import { QueryState } from '../components/QueryState';
import { TopBar } from '../components/TopBar';
import { printerKind } from '../device';
import { TransactionResult } from '../devices/pinelabs';
import { hasLinePrinter, hasTerminalPayments, terminal } from '../devices/terminal';
import { printHtml } from '../printing/printHtml';
import { usePrinterSettings } from '../stores/printerStore';

const money = (paise: number) => formatPaiseToRupees(paise);
const errorText = (e: unknown) => (e instanceof ApiError ? e.message : 'Something went wrong. Try again.');

const BILL_BADGE: Record<Bill['status'], BadgeProps['variant']> = {
  unpaid: 'warning',
  partially_paid: 'warning',
  paid: 'available',
  voided: 'danger',
  refunded: 'neutral',
};

const PAYMENT_STATUS: Record<BillPayment['status'], string | null> = {
  success: null,
  pending: 'Not confirmed',
  failed: 'Declined',
  reversed: 'Reversed',
};

/**
 * Tenders the cashier can pick. On the counter device card and UPI are charged on the Pine Labs terminal; elsewhere they are
 * keyed in by hand with the approval code or UTR.
 */
const TENDERS: { mode: PaymentMode; label: string; referenceLabel?: string }[] = [
  { mode: PaymentMode.CASH, label: 'Cash' },
  { mode: PaymentMode.CARD, label: 'Card', referenceLabel: 'Approval code' },
  { mode: PaymentMode.UPI, label: 'UPI', referenceLabel: 'UTR / transaction ID' },
];

const Row: React.FC<{ label: string; value: string; strong?: boolean; muted?: boolean }> = ({ label, value, strong, muted }) => (
  <View style={styles.row}>
    <Text style={[strong ? styles.rowStrong : styles.rowLabel, muted ? styles.muted : null]}>{label}</Text>
    <Text style={[strong ? styles.rowStrong : styles.rowValue, muted ? styles.muted : null]}>{value}</Text>
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
  const startTerminal = useStartTerminalPayment();
  const completeTerminal = useCompleteTerminalPayment();
  const reverse = useReverseTerminalPayment();
  const paperMm = usePrinterSettings((s) => s.paperMm);

  const order = orderQuery.data;
  const bill = billQuery.data ?? undefined;
  const balance = bill?.balance_due_paise ?? 0;
  const payable = bill?.status === 'unpaid' || bill?.status === 'partially_paid';
  const pending = (bill?.payments ?? []).filter((p) => p.status === 'pending');
  const onTerminal = hasTerminalPayments();
  const canPrintBill = hasLinePrinter() || printerKind() === 'system';

  const [mode, setMode] = useState<PaymentMode>(PaymentMode.CASH);
  const [amount, setAmount] = useState('');
  const [reference, setReference] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [change, setChange] = useState(0);

  // Discount at billing (a manager's PIN when above the threshold; the server decides).
  const [discountOpen, setDiscountOpen] = useState(false);
  const [discount, setDiscount] = useState('');
  const [discountReason, setDiscountReason] = useState('');
  const [discountPin, setDiscountPin] = useState('');

  // Resolving an unconfirmed terminal payment from its charge slip.
  const [resolving, setResolving] = useState<BillPayment | null>(null);
  const [slipCode, setSlipCode] = useState('');
  const [reversing, setReversing] = useState<BillPayment | null>(null);
  // Voided on the terminal but not yet recorded (e.g. the connection dropped): retried without voiding the card again.
  const [unrecordedVoid, setUnrecordedVoid] = useState<{ payment: BillPayment; reason: string; pin: string; details: TransactionResult['details'] } | null>(null);

  // One key per distinct attempt: retrying the same tender is safe, a different tender gets a new key.
  const [generateKey, setGenerateKey] = useState(newIdempotencyKey);
  const [payKey, setPayKey] = useState(newIdempotencyKey);
  const [terminalKey, setTerminalKey] = useState(newIdempotencyKey);
  useEffect(() => setPayKey(newIdempotencyKey()), [mode, amount, reference, bill?.paid_paise]);

  // Suggest the full balance; it follows the balance down after each partial payment.
  useEffect(() => {
    if (payable) setAmount(toRupees(balance));
  }, [payable, balance]);

  const tender = TENDERS.find((t) => t.mode === mode)!;
  const viaTerminal = onTerminal && mode !== PaymentMode.CASH;
  const amountPaise = toPaise(amount);
  const overBalance = mode !== PaymentMode.CASH && amountPaise > balance;
  const missingReference = !viaTerminal && !!tender.referenceLabel && !reference.trim();
  const canPay = amountPaise > 0 && !overBalance && !missingReference && pending.length === 0 && !busy;

  const generateBill = async () => {
    setError(null);
    const discountPaise = discountOpen ? toPaise(discount) : 0;
    try {
      await generate.mutateAsync({
        orderId,
        idempotencyKey: generateKey,
        discount: discountPaise > 0 ? { amount_paise: discountPaise, reason: discountReason.trim(), approver_pin: discountPin || undefined } : undefined,
      });
      setGenerateKey(newIdempotencyKey());
    } catch (e) {
      setError(errorText(e));
      if (e instanceof ApiError && e.code === 'approval_required') setDiscountOpen(true);
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

  /** Tell the server what the terminal said. The money may already be taken, so a dropped connection is retried. */
  const saveOutcome = async (payment: BillPayment, result: TransactionResult, source: 'terminal' | 'manual' = 'terminal', code?: string) => {
    const outcome = {
      outcome: result.status === 'approved' ? ('approved' as const) : ('declined' as const),
      approval_code: code ?? result.approvalCode,
      message: result.message.slice(0, 255),
      source,
      details: result.details,
    };
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        await completeTerminal.mutateAsync({ orderId, paymentId: payment.id, outcome });
        return true;
      } catch (e) {
        if (e instanceof ApiError && e.status !== 0) {
          setError(errorText(e));
          return false;
        }
        await new Promise((r) => setTimeout(r, 1500 * (attempt + 1)));
      }
    }
    setError('The terminal answered but the server could not be reached. Do not charge again: use "Check terminal" below when the connection is back.');
    return false;
  };

  const chargeOnTerminal = async () => {
    if (!bill) return;
    const payMode = mode === PaymentMode.UPI ? 'upi' : 'card';
    setError(null);
    setNotice(null);
    setBusy(payMode === 'card' ? 'Insert, tap or swipe the card on the terminal…' : 'Ask the guest to pay by UPI on the terminal…');
    try {
      // Saved as pending first: if anything below goes wrong, the payment is still on the bill to be resolved.
      const started = await startTerminal.mutateAsync({ orderId, billId: bill.id, mode: payMode, amountPaise, idempotencyKey: terminalKey });
      setTerminalKey(newIdempotencyKey());
      const ref = started.payment.billing_ref_no ?? '';
      let result = await terminal.sale(payMode, amountPaise, ref);
      if (result.status === 'unknown') {
        setBusy('Checking the result with the terminal…');
        result = await terminal.status(amountPaise, ref);
      }
      if (result.status === 'unknown') {
        setError('The terminal did not confirm this payment. Check the charge slip, then resolve it below.');
        return;
      }
      if (await saveOutcome(started.payment, result)) {
        if (result.status === 'approved') setNotice(`${payMode === 'card' ? 'Card' : 'UPI'} payment approved (${result.approvalCode}).`);
        else setError(`Not paid: ${result.message}`);
      }
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  const checkTerminal = async (payment: BillPayment) => {
    setError(null);
    setBusy('Checking the result with the terminal…');
    try {
      const result = await terminal.status(payment.amount_paise, payment.billing_ref_no ?? '');
      if (result.status === 'unknown') setError('The terminal still has no result. Check the charge slip and resolve it by hand.');
      else if (await saveOutcome(payment, result)) setNotice(result.status === 'approved' ? 'The payment was approved.' : 'The payment was not taken.');
    } finally {
      setBusy(null);
    }
  };

  const resolveFromSlip = async (payment: BillPayment, approved: boolean) => {
    setError(null);
    const ok = await saveOutcome(
      payment,
      { status: approved ? 'approved' : 'declined', message: approved ? 'Approved (from charge slip)' : 'Not charged (checked by cashier)', details: {} },
      'manual',
      approved ? slipCode.trim() : undefined,
    );
    if (ok) {
      setResolving(null);
      setSlipCode('');
    }
  };

  const reversePayment = async (reason: string, pin: string) => {
    const target = reversing;
    if (!target) return;
    // The terminal's own screen takes over from here; the outcome is shown on the bill.
    setReversing(null);
    const meta = target.terminal ?? {};
    const payMode = target.payment_mode === PaymentMode.UPI ? 'upi' : 'card';
    setError(null);
    try {
      // Approval first: a wrong PIN must stop us before the card is refunded.
      setBusy('Checking the approval…');
      await reverse.mutateAsync({ orderId, paymentId: target.id, reason, approverPin: pin || undefined, checkOnly: true });
      setBusy('Voiding on the terminal…');
      const result = await terminal.voidSale(payMode, target.amount_paise, target.billing_ref_no ?? '',
        String(meta.InvoiceNumber ?? ''), String(meta.AcquiringBankCode ?? ''));
      if (result.status !== 'approved') {
        setError(`The terminal did not void it: ${result.message}`);
        return;
      }
      await recordReversal({ payment: target, reason, pin, details: result.details });
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  const recordReversal = async (v: { payment: BillPayment; reason: string; pin: string; details: TransactionResult['details'] }) => {
    try {
      await reverse.mutateAsync({ orderId, paymentId: v.payment.id, reason: v.reason, approverPin: v.pin || undefined, details: v.details });
      setUnrecordedVoid(null);
      setNotice('Payment reversed. Collect the balance again.');
    } catch (e) {
      setUnrecordedVoid(v);
      setError(`Voided on the terminal, but not recorded yet: ${errorText(e)}`);
    }
  };

  const printBill = async () => {
    if (!bill) return;
    setError(null);
    setBusy('Printing the bill…');
    try {
      if (hasLinePrinter()) {
        const result = await terminal.print(await apiClient.getReceiptPrint(bill.id, paperMm));
        if (result.ok) setNotice('Bill printed.');
        else setError(`Bill not printed: ${result.message}`);
      } else {
        await printHtml(await apiClient.getReceiptHtml(bill.id, paperMm), paperMm);
      }
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  const reversible = (p: BillPayment) =>
    onTerminal && p.status === 'success' && p.provider !== 'manual' && !!p.terminal?.InvoiceNumber && bill?.status !== 'voided';

  return (
    <View style={styles.container}>
      <TopBar title={`Bill · ${title}`} subtitle={bill?.bill_number} onBack={onBack} />
      <QueryState query={billQuery.isLoading || billQuery.isError ? billQuery : orderQuery}>
        <ScrollView contentContainerStyle={styles.scroll} keyboardShouldPersistTaps="handled">
          {error ? <Text style={styles.error}>{error}</Text> : null}
          {notice ? <Text style={styles.notice}>{notice}</Text> : null}
          {busy ? <Text style={styles.busy}>{busy}</Text> : null}

          {!bill ? (
            <View style={styles.card}>
              <Text style={styles.sectionTitle}>Ready to bill</Text>
              <Row label="Items total" value={money(order?.subtotal_paise ?? 0)} />
              {discountOpen ? (
                <View style={styles.discount}>
                  <Input label="Discount (₹)" value={discount} onChangeText={setDiscount} keyboardType="decimal-pad" />
                  <Input label="Reason" value={discountReason} onChangeText={setDiscountReason} placeholder="e.g. regular guest" />
                  <Input
                    label="Manager PIN (if above the limit)"
                    value={discountPin}
                    onChangeText={(v) => setDiscountPin(v.replace(/\D/g, '').slice(0, 6))}
                    keyboardType="number-pad"
                    secureTextEntry
                  />
                </View>
              ) : (
                <Button title="Add discount" variant="ghost" size="sm" onPress={() => setDiscountOpen(true)} style={{ alignSelf: 'flex-start', paddingHorizontal: 0 }} />
              )}
              <Text style={styles.hint}>GST is added when the bill is generated. Every item must be sent to the kitchen first.</Text>
              <Button
                title="Generate bill"
                size="lg"
                onPress={generateBill}
                loading={generate.isPending}
                disabled={discountOpen && toPaise(discount) > 0 && !discountReason.trim()}
              />
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
                      <View key={p.id}>
                        <Row
                          label={`${p.payment_mode.toUpperCase()}${p.reference_id ? ` · ${p.reference_id}` : ''}${PAYMENT_STATUS[p.status] ? ` · ${PAYMENT_STATUS[p.status]}` : ''}`}
                          value={money(p.amount_paise)}
                          muted={p.status === 'failed' || p.status === 'reversed'}
                        />
                        {reversible(p) ? (
                          <Button title="Reverse" variant="ghost" size="sm" onPress={() => setReversing(p)}
                            textStyle={{ color: colors.danger }} style={{ alignSelf: 'flex-end', paddingHorizontal: 0 }} />
                        ) : null}
                      </View>
                    ))}
                  </View>
                ) : null}
                <Row label="Balance due" value={money(balance)} strong />
                {canPrintBill ? (
                  <Button title="Print bill" variant="secondary" onPress={printBill} disabled={!!busy} />
                ) : null}
              </View>

              {unrecordedVoid ? (
                <View style={[styles.card, styles.pendingCard]}>
                  <Text style={styles.sectionTitle}>
                    {unrecordedVoid.payment.payment_mode.toUpperCase()} {money(unrecordedVoid.payment.amount_paise)} voided on the terminal
                  </Text>
                  <Text style={styles.hint}>The card has been refunded. Record it so the bill shows the balance again.</Text>
                  <Button title="Record reversal" onPress={() => recordReversal(unrecordedVoid)} loading={reverse.isPending} />
                </View>
              ) : null}

              {pending.map((p) => (
                <View key={p.id} style={[styles.card, styles.pendingCard]}>
                  <Text style={styles.sectionTitle}>
                    {p.payment_mode.toUpperCase()} {money(p.amount_paise)} not confirmed
                  </Text>
                  <Text style={styles.hint}>
                    Terminal reference {p.billing_ref_no}. The terminal may have charged it. Check before collecting again.
                  </Text>
                  {onTerminal ? <Button title="Check terminal" onPress={() => checkTerminal(p)} disabled={!!busy} /> : null}
                  {resolving?.id === p.id ? (
                    <>
                      <Input label="Approval code on the charge slip" value={slipCode} onChangeText={setSlipCode} autoCapitalize="characters" />
                      <View style={styles.choices}>
                        <Button title="Approved" variant="success" onPress={() => resolveFromSlip(p, true)} disabled={!slipCode.trim()} />
                        <Button title="Not charged" variant="secondary" onPress={() => resolveFromSlip(p, false)} />
                      </View>
                    </>
                  ) : (
                    <Button title="Resolve from charge slip" variant="ghost" onPress={() => setResolving(p)} />
                  )}
                </View>
              ))}

              {payable && pending.length === 0 ? (
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
                  {!viaTerminal && tender.referenceLabel ? (
                    <Input label={tender.referenceLabel} value={reference} onChangeText={setReference} autoCapitalize="characters" />
                  ) : null}
                  {viaTerminal ? (
                    <Button
                      title={`Charge ${money(amountPaise)} on ${tender.label === 'Card' ? 'card' : 'UPI'}`}
                      size="lg"
                      onPress={chargeOnTerminal}
                      disabled={!canPay}
                      loading={!!busy}
                    />
                  ) : (
                    <Button
                      title={`Record ${tender.label} ${money(amountPaise)}`}
                      size="lg"
                      onPress={recordPayment}
                      disabled={!canPay}
                      loading={pay.isPending}
                    />
                  )}
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
      <ReverseSheet
        payment={reversing}
        loading={!!busy}
        error={reversing ? error : null}
        onClose={() => setReversing(null)}
        onSubmit={reversePayment}
      />
    </View>
  );
};

const ReverseSheet: React.FC<{
  payment: BillPayment | null;
  loading: boolean;
  error: string | null;
  onClose: () => void;
  onSubmit: (reason: string, pin: string) => void;
}> = ({ payment, loading, error, onClose, onSubmit }) => {
  const [reason, setReason] = useState('');
  const [pin, setPin] = useState('');
  useEffect(() => {
    setReason('');
    setPin('');
  }, [payment]);
  return (
    <Modal
      visible={payment !== null}
      onClose={onClose}
      title="Reverse payment"
      subtitle={payment ? `${payment.payment_mode.toUpperCase()} ${money(payment.amount_paise)} · ${payment.reference_id ?? ''}` : ''}
      footer={
        <>
          <Button title="Back" variant="secondary" onPress={onClose} />
          <Button title="Void on terminal" variant="danger" onPress={() => onSubmit(reason.trim(), pin)} disabled={reason.trim().length < 3} loading={loading} />
        </>
      }
    >
      <Input label="Reason" value={reason} onChangeText={setReason} placeholder="e.g. charged the wrong amount" />
      <View style={{ marginTop: spacing.md }}>
        <Input
          label="Manager PIN"
          value={pin}
          onChangeText={(v) => setPin(v.replace(/\D/g, '').slice(0, 6))}
          keyboardType="number-pad"
          secureTextEntry
          helper="Same day only, before the day-end settlement."
        />
      </View>
      {error ? <Text style={[styles.error, { marginTop: spacing.md }]}>{error}</Text> : null}
    </Modal>
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
  pendingCard: { borderColor: colors.billed },
  headRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  sectionTitle: { ...typography.bodyBold, color: colors.text },
  row: { flexDirection: 'row', justifyContent: 'space-between', gap: spacing.md },
  rowLabel: { ...typography.body, color: colors.textSecondary, flexShrink: 1 },
  rowValue: { ...typography.body, color: colors.text },
  rowStrong: { ...typography.bodyBold, color: colors.text },
  muted: { color: colors.textMuted, textDecorationLine: 'line-through' },
  payments: {
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.surfaceBorder,
    paddingTop: spacing.md,
    gap: spacing.sm,
  },
  discount: { gap: spacing.sm },
  choices: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  hint: { ...typography.caption, color: colors.textMuted },
  error: { ...typography.caption, color: colors.danger, backgroundColor: colors.dangerBg, padding: spacing.md, borderRadius: radii.md },
  notice: { ...typography.caption, color: colors.primary, backgroundColor: colors.primaryGlow, padding: spacing.md, borderRadius: radii.md },
  busy: { ...typography.bodyBold, color: colors.text, fontFamily: fonts.semibold, textAlign: 'center' },
  paid: { ...typography.bodyBold, color: colors.success },
  change: { ...typography.body, color: colors.text },
});
