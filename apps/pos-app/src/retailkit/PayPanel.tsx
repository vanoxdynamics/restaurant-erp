import React, { useEffect, useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { Badge, BadgeProps, Button, colors, fonts, Input, Modal, radii, spacing, typography } from '@repo/shared-ui';
import { formatPaiseToRupees } from '@repo/shared-utils';
import { ApiError, newIdempotencyKey, PrintablePage, TerminalOutcome, toPaise, toRupees } from '@repo/api-client';
import { Choice } from '../components/Choice';
import { printerKind } from '../device';
import { TransactionResult } from '../devices/pinelabs';
import { hasLinePrinter, hasTerminalPayments, terminal } from '../devices/terminal';
import { printHtml } from '../printing/printHtml';
import { usePrinterSettings } from '../stores/printerStore';

/**
 * Taking payment on a shop sale (dress and hardware shops): cash with change, card and UPI (on the Pine Labs terminal when the
 * device has one, keyed in by hand otherwise), several tenders at once, reversing a terminal payment, and printing.
 *
 * Modelled on the payment half of the restaurant BillScreen, but it calls no hooks of its own: each module passes a TenderApi
 * with its own endpoints, and the sale as its server last sent it.
 */

export type TenderStatus = 'success' | 'pending' | 'failed' | 'reversed';

export interface TenderPayment {
  id: string;
  payment_mode: string;
  amount_paise: number;
  status: TenderStatus;
  reference_id?: string | null;
  provider: string;
  /** The reference given to the terminal for this payment. */
  billing_ref_no?: string;
  /** The terminal's masked transaction details (InvoiceNumber, AcquiringBankCode, ...). */
  terminal?: Record<string, unknown>;
}

export interface TenderSale {
  id: string;
  number: string;
  status: 'unpaid' | 'partially_paid' | 'paid' | 'voided' | 'refunded';
  balance_due_paise: number;
  payments: TenderPayment[];
}

export interface TenderInput {
  mode: string;
  amountPaise: number;
  reference?: string;
}

export interface TenderApi {
  record(input: TenderInput & { idempotencyKey: string }): Promise<{ changePaise: number }>;
  split(tenders: TenderInput[], idempotencyKey: string): Promise<void>;
  startTerminal(input: { mode: 'card' | 'upi'; amountPaise: number; idempotencyKey: string }): Promise<TenderPayment>;
  completeTerminal(paymentId: string, outcome: TerminalOutcome): Promise<void>;
  reverse(paymentId: string, input: { reason: string; approverPin?: string; details?: TerminalOutcome['details']; checkOnly?: boolean }): Promise<void>;
  receiptPrint(paperMm: 58 | 80): Promise<PrintablePage>;
  receiptHtml(paperMm: 58 | 80): Promise<string>;
}

const money = (paise: number) => formatPaiseToRupees(paise);
export const errorText = (e: unknown) => (e instanceof ApiError ? e.message : 'Something went wrong. Try again.');

const SALE_BADGE: Record<TenderSale['status'], BadgeProps['variant']> = {
  unpaid: 'warning',
  partially_paid: 'warning',
  paid: 'available',
  voided: 'danger',
  refunded: 'neutral',
};

const PAYMENT_STATUS: Record<TenderStatus, string | null> = {
  success: null,
  pending: 'Not confirmed',
  failed: 'Declined',
  reversed: 'Reversed',
};

const TENDERS: { mode: 'cash' | 'card' | 'upi'; label: string; referenceLabel?: string }[] = [
  { mode: 'cash', label: 'Cash' },
  { mode: 'card', label: 'Card', referenceLabel: 'Approval code' },
  { mode: 'upi', label: 'UPI', referenceLabel: 'UTR / transaction ID' },
];

const modeLabel = (mode: string) => mode.replace(/_/g, ' ').toUpperCase();

export const Row: React.FC<{ label: string; value: string; strong?: boolean; muted?: boolean }> = ({ label, value, strong, muted }) => (
  <View style={styles.row}>
    <Text style={[strong ? styles.rowStrong : styles.rowLabel, muted ? styles.muted : null]}>{label}</Text>
    <Text style={[strong ? styles.rowStrong : styles.rowValue, muted ? styles.muted : null]}>{value}</Text>
  </View>
);

export interface PayPanelProps {
  sale: TenderSale;
  api: TenderApi;
  /** Totals the module wants above the payments (subtotal, discount, taxes, total). */
  summary?: React.ReactNode;
  /** Tenders of the module's own (store credit, on account), shown inside "Take payment". */
  extraTenders?: React.ReactNode;
  /** Called once the sale is paid and the cashier taps Done. */
  onDone: () => void;
  doneLabel?: string;
}

export const PayPanel: React.FC<PayPanelProps> = ({ sale, api, summary, extraTenders, onDone, doneLabel = 'Next sale' }) => {
  const paperMm = usePrinterSettings((s) => s.paperMm);
  const balance = sale.balance_due_paise;
  const payable = sale.status === 'unpaid' || sale.status === 'partially_paid';
  const pending = sale.payments.filter((p) => p.status === 'pending');
  const onTerminal = hasTerminalPayments();
  const canPrint = hasLinePrinter() || printerKind() === 'system';

  const [mode, setMode] = useState<'cash' | 'card' | 'upi' | 'split'>('cash');
  const [amount, setAmount] = useState('');
  const [reference, setReference] = useState('');
  const [split, setSplit] = useState<Record<'cash' | 'card' | 'upi', { amount: string; reference: string }>>({
    cash: { amount: '', reference: '' }, card: { amount: '', reference: '' }, upi: { amount: '', reference: '' },
  });
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [change, setChange] = useState(0);
  const [resolving, setResolving] = useState<TenderPayment | null>(null);
  const [slipCode, setSlipCode] = useState('');
  const [reversing, setReversing] = useState<TenderPayment | null>(null);
  const [unrecordedVoid, setUnrecordedVoid] = useState<{ payment: TenderPayment; reason: string; pin: string; details: TransactionResult['details'] } | null>(null);

  // One key per distinct attempt: retrying the same tender is safe, a different tender gets a new key.
  const [payKey, setPayKey] = useState(newIdempotencyKey);
  const [splitKey, setSplitKey] = useState(newIdempotencyKey);
  const [terminalKey, setTerminalKey] = useState(newIdempotencyKey);
  useEffect(() => setPayKey(newIdempotencyKey()), [mode, amount, reference, balance]);
  useEffect(() => setSplitKey(newIdempotencyKey()), [split, balance]);

  useEffect(() => {
    if (payable) setAmount(toRupees(balance));
  }, [payable, balance]);

  const tender = TENDERS.find((t) => t.mode === mode);
  const viaTerminal = onTerminal && (mode === 'card' || mode === 'upi');
  const amountPaise = toPaise(amount);
  const overBalance = mode !== 'cash' && amountPaise > balance;
  const missingReference = !viaTerminal && !!tender?.referenceLabel && !reference.trim();
  const canPay = amountPaise > 0 && !overBalance && !missingReference && pending.length === 0 && !busy;

  const splitTenders: TenderInput[] = (['cash', 'card', 'upi'] as const)
    .map((m) => ({ mode: m, amountPaise: toPaise(split[m].amount), reference: split[m].reference.trim() || undefined }))
    .filter((t) => t.amountPaise > 0);
  const splitTotal = splitTenders.reduce((sum, t) => sum + t.amountPaise, 0);
  const splitMissingRef = splitTenders.some((t) => t.mode !== 'cash' && !t.reference);
  const canSplit = splitTenders.length > 0 && splitTotal === balance && !splitMissingRef && !busy;

  const run = async (label: string, work: () => Promise<void>) => {
    setError(null);
    setNotice(null);
    setBusy(label);
    try {
      await work();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  const recordPayment = () => run('Recording the payment…', async () => {
    const result = await api.record({ mode, amountPaise, reference: reference.trim() || undefined, idempotencyKey: payKey });
    setChange(result.changePaise);
    setReference('');
  });

  const recordSplit = () => run('Recording the payments…', async () => {
    await api.split(splitTenders, splitKey);
    setChange(0);
  });

  /** Tell the server what the terminal said. The money may already be taken, so a dropped connection is retried. */
  const saveOutcome = async (payment: TenderPayment, result: TransactionResult, source: 'terminal' | 'manual' = 'terminal', code?: string) => {
    const outcome: TerminalOutcome = {
      outcome: result.status === 'approved' ? 'approved' : 'declined',
      approval_code: code ?? result.approvalCode,
      message: result.message.slice(0, 255),
      source,
      details: result.details,
    };
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        await api.completeTerminal(payment.id, outcome);
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
    const payMode = mode === 'upi' ? 'upi' : 'card';
    setError(null);
    setNotice(null);
    setBusy(payMode === 'card' ? 'Insert, tap or swipe the card on the terminal…' : 'Ask the customer to pay by UPI on the terminal…');
    try {
      // Saved as pending first: if anything below goes wrong, the payment is still on the sale to be resolved.
      const started = await api.startTerminal({ mode: payMode, amountPaise, idempotencyKey: terminalKey });
      setTerminalKey(newIdempotencyKey());
      const ref = started.billing_ref_no ?? '';
      let result = await terminal.sale(payMode, amountPaise, ref);
      if (result.status === 'unknown') {
        setBusy('Checking the result with the terminal…');
        result = await terminal.status(amountPaise, ref);
      }
      if (result.status === 'unknown') {
        setError('The terminal did not confirm this payment. Check the charge slip, then resolve it below.');
        return;
      }
      if (await saveOutcome(started, result)) {
        if (result.status === 'approved') setNotice(`${payMode === 'card' ? 'Card' : 'UPI'} payment approved (${result.approvalCode}).`);
        else setError(`Not paid: ${result.message}`);
      }
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  const checkTerminal = async (payment: TenderPayment) => {
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

  const resolveFromSlip = async (payment: TenderPayment, approved: boolean) => {
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

  const recordReversal = async (v: { payment: TenderPayment; reason: string; pin: string; details: TransactionResult['details'] }) => {
    try {
      await api.reverse(v.payment.id, { reason: v.reason, approverPin: v.pin || undefined, details: v.details });
      setUnrecordedVoid(null);
      setNotice('Payment reversed. Collect the balance again.');
    } catch (e) {
      setUnrecordedVoid(v);
      setError(`Voided on the terminal, but not recorded yet: ${errorText(e)}`);
    }
  };

  const reversePayment = async (reason: string, pin: string) => {
    const target = reversing;
    if (!target) return;
    setReversing(null);
    const meta = target.terminal ?? {};
    const payMode = target.payment_mode === 'upi' ? 'upi' : 'card';
    setError(null);
    try {
      // Approval first: a wrong PIN must stop us before the card is refunded.
      setBusy('Checking the approval…');
      await api.reverse(target.id, { reason, approverPin: pin || undefined, checkOnly: true });
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

  const print = () => run('Printing…', async () => {
    if (hasLinePrinter()) {
      const result = await terminal.print(await api.receiptPrint(paperMm));
      if (result.ok) setNotice('Printed.');
      else setError(`Not printed: ${result.message}`);
    } else {
      await printHtml(await api.receiptHtml(paperMm), paperMm);
    }
  });

  const reversible = (p: TenderPayment) =>
    onTerminal && p.status === 'success' && p.provider !== 'manual' && !!p.terminal?.InvoiceNumber && sale.status !== 'voided';

  return (
    <View style={styles.stack}>
      {error ? <Text style={styles.error}>{error}</Text> : null}
      {notice ? <Text style={styles.notice}>{notice}</Text> : null}
      {busy ? <Text style={styles.busy}>{busy}</Text> : null}

      <View style={styles.card}>
        <View style={styles.headRow}>
          <Text style={styles.sectionTitle}>{sale.number}</Text>
          <Badge label={sale.status.replace('_', ' ')} variant={SALE_BADGE[sale.status]} />
        </View>
        {summary}
        {sale.payments.length > 0 ? (
          <View style={styles.payments}>
            {sale.payments.map((p) => (
              <View key={p.id}>
                <Row
                  label={`${modeLabel(p.payment_mode)}${p.reference_id ? ` · ${p.reference_id}` : ''}${PAYMENT_STATUS[p.status] ? ` · ${PAYMENT_STATUS[p.status]}` : ''}`}
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
        {canPrint ? <Button title="Print invoice" variant="secondary" onPress={print} disabled={!!busy} /> : null}
      </View>

      {unrecordedVoid ? (
        <View style={[styles.card, styles.pendingCard]}>
          <Text style={styles.sectionTitle}>
            {modeLabel(unrecordedVoid.payment.payment_mode)} {money(unrecordedVoid.payment.amount_paise)} voided on the terminal
          </Text>
          <Text style={styles.hint}>The card has been refunded. Record it so the sale shows the balance again.</Text>
          <Button title="Record reversal" onPress={() => recordReversal(unrecordedVoid)} disabled={!!busy} />
        </View>
      ) : null}

      {pending.map((p) => (
        <View key={p.id} style={[styles.card, styles.pendingCard]}>
          <Text style={styles.sectionTitle}>
            {modeLabel(p.payment_mode)} {money(p.amount_paise)} not confirmed
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
          {extraTenders}
          <View style={styles.choices}>
            {TENDERS.map((t) => (
              <Choice key={t.mode} label={t.label} selected={mode === t.mode} onPress={() => setMode(t.mode)} />
            ))}
            {onTerminal ? null : <Choice label="Split" selected={mode === 'split'} onPress={() => setMode('split')} />}
          </View>
          {mode === 'split' ? (
            <>
              {TENDERS.map((t) => (
                <View key={t.mode} style={styles.splitRow}>
                  <View style={{ flex: 1 }}>
                    <Input label={`${t.label} (₹)`} value={split[t.mode].amount} keyboardType="decimal-pad"
                      onChangeText={(v) => setSplit((s) => ({ ...s, [t.mode]: { ...s[t.mode], amount: v } }))} />
                  </View>
                  {t.referenceLabel ? (
                    <View style={{ flex: 1 }}>
                      <Input label={t.referenceLabel} value={split[t.mode].reference} autoCapitalize="characters"
                        onChangeText={(v) => setSplit((s) => ({ ...s, [t.mode]: { ...s[t.mode], reference: v } }))} />
                    </View>
                  ) : <View style={{ flex: 1 }} />}
                </View>
              ))}
              <Row label="Tendered" value={`${money(splitTotal)} of ${money(balance)}`} />
              <Button title="Record payments" size="lg" onPress={recordSplit} disabled={!canSplit} loading={!!busy} />
              <Text style={styles.hint}>The amounts must add up to the balance. Card and UPI need their reference.</Text>
            </>
          ) : (
            <>
              <Input
                label="Amount (₹)"
                value={amount}
                onChangeText={setAmount}
                keyboardType="decimal-pad"
                error={overBalance ? `More than the balance of ${money(balance)}` : undefined}
              />
              {!viaTerminal && tender?.referenceLabel ? (
                <Input label={tender.referenceLabel} value={reference} onChangeText={setReference} autoCapitalize="characters" />
              ) : null}
              {viaTerminal ? (
                <Button
                  title={`Charge ${money(amountPaise)} on ${mode === 'card' ? 'card' : 'UPI'}`}
                  size="lg"
                  onPress={chargeOnTerminal}
                  disabled={!canPay}
                  loading={!!busy}
                />
              ) : (
                <Button title={`Record ${tender?.label ?? ''} ${money(amountPaise)}`} size="lg" onPress={recordPayment}
                  disabled={!canPay} loading={!!busy} />
              )}
              <Text style={styles.hint}>Enter a smaller amount to take the rest another way.</Text>
            </>
          )}
        </View>
      ) : null}

      {sale.status === 'paid' ? (
        <View style={styles.card}>
          <Text style={styles.paid}>Paid in full</Text>
          {change > 0 ? <Text style={styles.change}>Return {money(change)} change to the customer</Text> : null}
          <Button title={doneLabel} size="lg" onPress={onDone} />
        </View>
      ) : null}

      <ReverseSheet payment={reversing} loading={!!busy} error={reversing ? error : null} onClose={() => setReversing(null)}
        onSubmit={reversePayment} />
    </View>
  );
};

const ReverseSheet: React.FC<{
  payment: TenderPayment | null;
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
      subtitle={payment ? `${modeLabel(payment.payment_mode)} ${money(payment.amount_paise)} · ${payment.reference_id ?? ''}` : ''}
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

export const payStyles = StyleSheet.create({
  card: {
    backgroundColor: colors.surface,
    borderRadius: radii.lg,
    borderWidth: 1,
    borderColor: colors.surfaceBorder,
    padding: spacing.lg,
    gap: spacing.md,
  },
  sectionTitle: { ...typography.bodyBold, color: colors.text },
  hint: { ...typography.caption, color: colors.textMuted },
  error: { ...typography.caption, color: colors.danger, backgroundColor: colors.dangerBg, padding: spacing.md, borderRadius: radii.md },
  notice: { ...typography.caption, color: colors.primary, backgroundColor: colors.primaryGlow, padding: spacing.md, borderRadius: radii.md },
});

const styles = StyleSheet.create({
  ...payStyles,
  stack: { gap: spacing.lg },
  pendingCard: { borderColor: colors.billed },
  headRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
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
  choices: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  splitRow: { flexDirection: 'row', gap: spacing.sm },
  busy: { ...typography.bodyBold, color: colors.text, fontFamily: fonts.semibold, textAlign: 'center' },
  paid: { ...typography.bodyBold, color: colors.success },
  change: { ...typography.body, color: colors.text },
});
