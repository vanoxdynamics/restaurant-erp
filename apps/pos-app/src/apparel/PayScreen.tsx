import React, { useMemo, useState } from 'react';
import { ScrollView, StyleSheet, Text, View } from 'react-native';
import { useQueryClient } from '@tanstack/react-query';
import { Button, colors, Input, Modal, radii, spacing, typography } from '@repo/shared-ui';
import { formatPaiseToRupees } from '@repo/shared-utils';
import { ApiError, apparelSalesApi, ApparelSale, TerminalOutcome, useApparelSale, useVoidApparelSale } from '@repo/api-client';
import { QueryState } from '../components/QueryState';
import { TopBar } from '../components/TopBar';
import { PayPanel, Row, type TenderApi, type TenderSale } from '../retailkit/PayPanel';

const money = (paise: number) => formatPaiseToRupees(paise);

export interface PayScreenProps {
  saleId: string;
  /** Back to the counter with the sale left as it is (unpaid sales keep their pieces out of stock until voided). */
  onBack: () => void;
  /** Paid (or cancelled): next customer. */
  onDone: () => void;
}

const asTenderSale = (sale: ApparelSale): TenderSale => ({
  id: sale.id,
  number: sale.number,
  status: sale.status,
  balance_due_paise: sale.status === 'voided' ? 0 : sale.balance_due_paise,
  payments: sale.payments,
});

/** Payment for a dress shop invoice: the shared pay panel with this module's endpoints, above the invoice's own lines and tax. */
export const PayScreen: React.FC<PayScreenProps> = ({ saleId, onBack, onDone }) => {
  const query = useApparelSale(saleId);
  const queryClient = useQueryClient();
  const voidSale = useVoidApparelSale();
  const sale = query.data;

  const [voiding, setVoiding] = useState(false);
  const [reason, setReason] = useState('');
  const [pin, setPin] = useState('');
  const [voidError, setVoidError] = useState<string | null>(null);

  /** Every tender changes the sale: fetch it again so the panel shows what the server now says. */
  const refreshed = async <T,>(work: Promise<T>): Promise<T> => {
    try {
      return await work;
    } finally {
      await queryClient.invalidateQueries({ queryKey: ['apparel', 'sale', saleId] });
    }
  };

  const api: TenderApi = useMemo(() => ({
    record: ({ mode, amountPaise, reference, idempotencyKey }) =>
      refreshed(apparelSalesApi.pay(saleId, { mode, amountPaise, reference }, idempotencyKey)),
    split: (tenders, key) => refreshed(apparelSalesApi.split(saleId, tenders, key)),
    startTerminal: async ({ mode, amountPaise, idempotencyKey }) =>
      refreshed(apparelSalesApi.startTerminal(saleId, { mode, amountPaise }, idempotencyKey)),
    completeTerminal: (paymentId: string, outcome: TerminalOutcome) => refreshed(apparelSalesApi.completeTerminal(paymentId, outcome)),
    reverse: (paymentId, input) =>
      refreshed(apparelSalesApi.reverse(paymentId, {
        reason: input.reason, approver_pin: input.approverPin, details: input.details, check_only: input.checkOnly,
      })),
    receiptPrint: (paperMm) => apparelSalesApi.receiptPrint(saleId, paperMm),
    receiptHtml: (paperMm) => apparelSalesApi.receiptHtml(saleId, paperMm),
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [saleId]);

  const cancel = async () => {
    setVoidError(null);
    try {
      await voidSale.mutateAsync({ saleId, reason: reason.trim(), approverPin: pin || undefined });
      setVoiding(false);
      onDone();
    } catch (e) {
      setVoidError(e instanceof ApiError ? e.message : 'Could not cancel the sale. Try again.');
    }
  };

  const untouched = !!sale && sale.status === 'unpaid' && sale.payments.length === 0;

  return (
    <View style={styles.container}>
      <TopBar title="Payment" subtitle={sale?.number} onBack={onBack} />
      <QueryState query={query} isEmpty={!sale} emptyText="Sale not found.">
        {sale ? (
          <ScrollView contentContainerStyle={styles.body}>
            <PayPanel
              sale={asTenderSale(sale)}
              api={api}
              onDone={onDone}
              summary={
                <View style={styles.summary}>
                  {sale.lines.map((l) => (
                    <Row
                      key={l.id}
                      label={`${l.quantity} × ${l.name} (${l.size} / ${l.colour})`}
                      value={money(l.line_total_paise)}
                    />
                  ))}
                  <View style={styles.rule} />
                  <Row label="Subtotal" value={money(sale.subtotal_paise)} />
                  {sale.discount_paise > 0 ? <Row label="Discount" value={`−${money(sale.discount_paise)}`} /> : null}
                  <Row label="Taxable value" value={money(sale.taxable_paise)} />
                  <Row label="CGST" value={money(sale.cgst_paise)} />
                  <Row label="SGST" value={money(sale.sgst_paise)} />
                  {sale.round_off_paise !== 0 ? <Row label="Round off" value={money(sale.round_off_paise)} /> : null}
                  <Row label="Total" value={money(sale.total_paise)} strong />
                  {sale.status === 'partially_paid' ? <Row label="Balance due" value={money(sale.balance_due_paise)} strong /> : null}
                </View>
              }
            />
            {untouched ? (
              <Button title="Cancel this sale" variant="ghost" onPress={() => { setVoiding(true); setVoidError(null); }} />
            ) : null}
            {sale.status === 'voided' ? <Text style={styles.voided}>This sale was cancelled. Its pieces are back in stock.</Text> : null}
          </ScrollView>
        ) : null}
      </QueryState>

      <Modal
        visible={voiding}
        onClose={() => setVoiding(false)}
        title="Cancel this sale"
        subtitle="The pieces go back into stock. Needs a manager."
        footer={<Button title="Cancel the sale" variant="danger" onPress={cancel} disabled={reason.trim().length < 3} loading={voidSale.isPending} />}
      >
        <Input label="Reason" value={reason} onChangeText={setReason} placeholder="e.g. Customer changed their mind" />
        <Input label="Manager PIN (not needed if you are a manager)" value={pin} onChangeText={(v) => setPin(v.replace(/\D/g, ''))} secureTextEntry keyboardType="number-pad" maxLength={6} />
        {voidError ? <Text style={styles.error}>{voidError}</Text> : null}
      </Modal>
    </View>
  );
};

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  body: { padding: spacing.lg, gap: spacing.md, maxWidth: 720, width: '100%', alignSelf: 'center' },
  summary: { gap: spacing.xs, marginVertical: spacing.sm },
  rule: { height: 1, backgroundColor: colors.surfaceBorder, marginVertical: spacing.xs, borderRadius: radii.full },
  voided: { ...typography.caption, color: colors.danger, textAlign: 'center' },
  error: { ...typography.caption, color: colors.danger, marginTop: spacing.sm },
});
