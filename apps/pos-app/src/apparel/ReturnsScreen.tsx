import React, { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { Button, colors, fonts, Input, Modal, radii, spacing, typography } from '@repo/shared-ui';
import { formatPaiseToRupees } from '@repo/shared-utils';
import {
  ApiError,
  apparelReturnsApi,
  ApparelRefundMode,
  ApparelReturn,
  ApparelSale,
  newIdempotencyKey,
  useCreateApparelReturn,
} from '@repo/api-client';
import { Choice } from '../components/Choice';
import { StepButton } from '../components/ItemOptionsSheet';
import { TopBar } from '../components/TopBar';
import { hasLinePrinter, terminal } from '../devices/terminal';
import { printerKind } from '../device';
import { printHtml } from '../printing/printHtml';
import { usePrinterSettings } from '../stores/printerStore';
import { useDeviceStore } from '../stores/deviceStore';

const money = (paise: number) => formatPaiseToRupees(paise);
const message = (e: unknown) => (e instanceof ApiError ? e.message : 'Something went wrong. Try again.');

export interface ReturnsScreenProps {
  onBack: () => void;
  /** The customer took store credit and is choosing something else: go to the counter with the credit ready. */
  onExchange: (credit: { code: string; amount_paise: number }) => void;
}

interface Pick {
  quantity: number;
  restock: boolean;
}

/**
 * Taking goods back. Find the sale by its invoice number (typed or scanned) or the customer's phone, choose the pieces and
 * whether they go back on the shelf, then refund in cash or give store credit (an exchange). What each piece is worth comes
 * from the original invoice, so the amount shown here is only a preview of what the server works out.
 */
export const ReturnsScreen: React.FC<ReturnsScreenProps> = ({ onBack, onExchange }) => {
  const device = useDeviceStore((s) => s.device)!;
  const paperMm = usePrinterSettings((s) => s.paperMm);
  const createReturn = useCreateApparelReturn();

  const [query, setQuery] = useState('');
  const [found, setFound] = useState<ApparelSale[] | null>(null);
  const [sale, setSale] = useState<ApparelSale | null>(null);
  const [picks, setPicks] = useState<Record<string, Pick>>({});
  const [reason, setReason] = useState('');
  const [mode, setMode] = useState<ApparelRefundMode>('cash');
  const [error, setError] = useState<string | null>(null);
  const [searching, setSearching] = useState(false);
  const [pinOpen, setPinOpen] = useState(false);
  const [pin, setPin] = useState('');
  const [done, setDone] = useState<ApparelReturn | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [key, setKey] = useState(newIdempotencyKey);

  const find = async () => {
    const text = query.trim();
    if (!text) return;
    setError(null);
    setSearching(true);
    try {
      // A phone number is ten or more digits; an invoice number has letters and slashes, or is just the counter's tail.
      const phone = /^\+?[\d\s-]+$/.test(text) && text.replace(/\D/g, '').length >= 10;
      const sales = await apparelReturnsApi.findSales({ ...(phone ? { phone: text } : { search: text }), branch_id: device.branchId });
      setFound(sales);
      if (sales.length === 1) choose(sales[0]);
      else if (sales.length === 0) setError('No paid sale matches that.');
    } catch (e) {
      setError(message(e));
    } finally {
      setSearching(false);
    }
  };

  const choose = (s: ApparelSale) => {
    setSale(s);
    setPicks({});
    setError(null);
    setKey(newIdempotencyKey());
  };

  const left = (line: ApparelSale['lines'][number]) => line.quantity - line.returned_quantity;
  const chosen = sale ? sale.lines.filter((l) => (picks[l.id]?.quantity ?? 0) > 0) : [];
  // A preview only: the server values pieces from the stored invoice line and the last pieces take what is left.
  const preview = chosen.reduce((n, l) => n + Math.round((l.line_total_paise * picks[l.id].quantity) / l.quantity), 0);

  const setPick = (lineId: string, patch: Partial<Pick>) => {
    setPicks((p) => ({ ...p, [lineId]: { ...(p[lineId] ?? { quantity: 0, restock: true }), ...patch } }));
    setKey(newIdempotencyKey());
  };

  const submit = async (approverPin?: string) => {
    if (!sale || !chosen.length) return;
    setError(null);
    const attemptKey = approverPin ? newIdempotencyKey() : key;
    try {
      const made = await createReturn.mutateAsync({
        idempotencyKey: attemptKey,
        input: {
          sale_id: sale.id,
          lines: chosen.map((l) => ({ sale_line_id: l.id, quantity: picks[l.id].quantity, restock: picks[l.id].restock })),
          reason, refund_mode: mode, client_return_id: attemptKey, approver_pin: approverPin,
        },
      });
      setPinOpen(false);
      setPin('');
      setDone(made);
    } catch (e) {
      if (e instanceof ApiError && e.code === 'approval_required') {
        setPinOpen(true);
        setError(approverPin ? 'That PIN was not accepted. Ask a manager.' : 'This sale is older than the return period. A manager must approve.');
        setPin('');
        return;
      }
      setPinOpen(false);
      setError(message(e));
    }
  };

  const print = async (ret: ApparelReturn) => {
    setNotice(null);
    try {
      if (hasLinePrinter()) {
        const result = await terminal.print(await apparelReturnsApi.receiptPrint(ret.id, paperMm));
        setNotice(result.ok ? 'Printed.' : `Not printed: ${result.message}`);
      } else if (printerKind() === 'system') {
        await printHtml(await apparelReturnsApi.receiptHtml(ret.id, paperMm), paperMm);
      } else setNotice('No printer is set up on this device.');
    } catch (e) {
      setNotice(message(e));
    }
  };

  const again = () => {
    setDone(null);
    setSale(null);
    setFound(null);
    setQuery('');
    setPicks({});
    setReason('');
    setNotice(null);
    setKey(newIdempotencyKey());
  };

  if (done) {
    return (
      <View style={styles.container}>
        <TopBar title="Goods taken back" subtitle={done.number} onBack={onBack} />
        <ScrollView contentContainerStyle={styles.body}>
          <View style={styles.card}>
            <Text style={styles.big}>{money(done.total_paise)}</Text>
            <Text style={styles.text}>
              {done.refund_mode === 'cash' ? 'Give the customer this in cash.' : `Kept as store credit. Credit note ${done.store_credit_code}.`}
            </Text>
            {notice ? <Text style={styles.hint}>{notice}</Text> : null}
            <Button title="Print credit note" variant="secondary" onPress={() => print(done)} />
            {done.refund_mode === 'store_credit' && done.store_credit_code ? (
              <Button title="Choose something else (exchange)" onPress={() => onExchange({ code: done.store_credit_code!, amount_paise: done.total_paise })} />
            ) : null}
            <Button title="Take back more" variant="ghost" onPress={again} />
            <Button title="Back to the counter" variant={done.refund_mode === 'cash' ? 'primary' : 'ghost'} onPress={onBack} />
          </View>
        </ScrollView>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <TopBar title="Returns and exchanges" subtitle={device.branchName} onBack={onBack} />
      <ScrollView contentContainerStyle={styles.body} keyboardShouldPersistTaps="handled">
        <View style={styles.card}>
          <Text style={styles.title}>Find the sale</Text>
          <View style={styles.row}>
            <View style={{ flex: 1 }}>
              <Input value={query} onChangeText={setQuery} onSubmitEditing={find} placeholder="Invoice number, or customer phone" autoCapitalize="characters" autoFocus
                accessibilityLabel="Invoice number, or customer phone" returnKeyType="search" />
            </View>
            <Button title="Find" onPress={find} loading={searching} disabled={!query.trim()} />
          </View>
          {!sale && found && found.length > 1
            ? found.map((s) => (
                <Pressable key={s.id} onPress={() => choose(s)} accessibilityRole="button" accessibilityLabel={`Sale ${s.number}`} style={styles.saleRow}>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.name}>{s.number}</Text>
                    <Text style={styles.meta}>{s.business_date} · {s.lines.reduce((n, l) => n + l.quantity, 0)} pieces</Text>
                  </View>
                  <Text style={styles.name}>{money(s.total_paise)}</Text>
                </Pressable>
              ))
            : null}
          {error && !sale ? <Text style={styles.error}>{error}</Text> : null}
        </View>

        {sale ? (
          <View style={styles.card}>
            <View style={styles.row}>
              <Text style={[styles.title, { flex: 1 }]}>{sale.number}</Text>
              <Text style={styles.meta}>{sale.business_date}</Text>
            </View>
            {sale.lines.map((line) => {
              const pick = picks[line.id] ?? { quantity: 0, restock: true };
              const remaining = left(line);
              return (
                <View key={line.id} style={styles.line}>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.name} numberOfLines={2}>{line.name}</Text>
                    <Text style={styles.meta}>
                      {line.size} · {line.colour} · sold {line.quantity}
                      {line.returned_quantity ? `, already back ${line.returned_quantity}` : ''}
                    </Text>
                    {pick.quantity > 0 ? (
                      <Pressable onPress={() => setPick(line.id, { restock: !pick.restock })} accessibilityRole="switch" accessibilityState={{ checked: pick.restock }}
                        accessibilityLabel={`${line.name} goes back on the shelf`} hitSlop={6}>
                        <Text style={styles.link}>{pick.restock ? 'Goes back on the shelf' : 'Damaged: not back on the shelf'} (tap to change)</Text>
                      </Pressable>
                    ) : null}
                  </View>
                  {remaining > 0 ? (
                    <View style={styles.stepper}>
                      <StepButton icon="minus" label={`One less ${line.name}`} size={36} onPress={() => setPick(line.id, { quantity: Math.max(0, pick.quantity - 1) })} />
                      <Text style={styles.qty}>{pick.quantity}</Text>
                      <StepButton icon="plus" label={`One more ${line.name}`} size={36} onPress={() => setPick(line.id, { quantity: Math.min(remaining, pick.quantity + 1) })} />
                    </View>
                  ) : (
                    <Text style={styles.meta}>All back</Text>
                  )}
                </View>
              );
            })}

            <Input label="Reason" value={reason} onChangeText={(v) => { setReason(v); setKey(newIdempotencyKey()); }} placeholder="e.g. Did not fit" />
            <View style={styles.row}>
              <Choice label="Cash refund" selected={mode === 'cash'} onPress={() => setMode('cash')} />
              <Choice label="Exchange (store credit)" selected={mode === 'store_credit'} onPress={() => setMode('store_credit')} />
            </View>
            <View style={styles.row}>
              <Text style={[styles.title, { flex: 1 }]}>{mode === 'cash' ? 'Refund' : 'Store credit'}</Text>
              <Text style={styles.big}>{chosen.length ? `about ${money(preview)}` : money(0)}</Text>
            </View>
            {error ? <Text style={styles.error}>{error}</Text> : null}
            <Button title={mode === 'cash' ? 'Refund in cash' : 'Give store credit'} size="lg" onPress={() => submit()}
              disabled={!chosen.length || reason.trim().length < 3} loading={createReturn.isPending} />
          </View>
        ) : null}
      </ScrollView>

      <Modal visible={pinOpen} onClose={() => setPinOpen(false)} title="Manager approval" subtitle="This sale is older than the return period."
        footer={<Button title="Approve and take back" onPress={() => submit(pin)} disabled={pin.length < 4} loading={createReturn.isPending} />}>
        <Input label="Manager PIN" value={pin} onChangeText={(v) => setPin(v.replace(/\D/g, ''))} secureTextEntry keyboardType="number-pad" maxLength={6} autoFocus />
        {error ? <Text style={styles.error}>{error}</Text> : null}
      </Modal>
    </View>
  );
};

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  body: { padding: spacing.lg, gap: spacing.md, maxWidth: 720, width: '100%', alignSelf: 'center' },
  card: { gap: spacing.md, padding: spacing.lg, borderRadius: radii.lg, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.surfaceBorder },
  row: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, flexWrap: 'wrap' },
  title: { ...typography.bodyBold, color: colors.text },
  text: { ...typography.body, color: colors.textSecondary },
  hint: { ...typography.caption, color: colors.textSecondary },
  big: { ...typography.h2, fontFamily: fonts.bold, color: colors.text },
  name: { ...typography.bodyBold, color: colors.text },
  meta: { ...typography.caption, color: colors.textSecondary },
  link: { ...typography.caption, color: colors.primary, fontFamily: fonts.semibold, marginTop: 2 },
  error: { ...typography.caption, color: colors.danger },
  saleRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingVertical: spacing.sm, borderTopWidth: 1, borderTopColor: colors.surfaceBorder },
  line: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingVertical: spacing.sm, borderBottomWidth: 1, borderBottomColor: colors.surfaceBorder },
  stepper: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs },
  qty: { ...typography.bodyBold, minWidth: 24, textAlign: 'center', color: colors.text },
});
