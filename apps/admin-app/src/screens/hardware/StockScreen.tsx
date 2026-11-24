import React, { useEffect, useMemo, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { Button, colors, fonts, Input, Modal, radii, spacing, Switch, typography } from '@repo/shared-ui';
import { formatDateTime, formatPaiseToRupees } from '@repo/shared-utils';
import {
  ApiError,
  HardwarePurchaseInput,
  HardwareStockRow,
  newIdempotencyKey,
  useAdjustHardwareStock,
  useHardwareMovements,
  useHardwarePurchases,
  useHardwareStock,
  useReceiveHardwareStock,
  useUpdateHardwareStock,
} from '@repo/api-client';
import { QueryState } from '../../components/QueryState';
import { useAuthStore } from '../../stores/authStore';

const errorText = (e: unknown) => (e instanceof ApiError ? e.message : 'Something went wrong. Try again.');
const money = (paise: number) => formatPaiseToRupees(paise);
/** Digits and one decimal point (and a leading minus when `signed`): what a quantity or a price is typed as. */
const decimal = (text: string, signed = false) => {
  const cleaned = text.replace(signed ? /[^\d.-]/g : /[^\d.]/g, '').replace(/(?!^)-/g, '');
  const [whole, ...rest] = cleaned.split('.');
  return rest.length ? `${whole}.${rest.join('').slice(0, 4)}` : whole;
};
/** 180 -> "180", 92.5 -> "92.5", 0.333 -> "0.333" (no float noise). */
const qty = (n: number) => String(Number(n.toFixed(3)));
const rupees4 = (n: number) => String(Number(n.toFixed(4)));
const label = (r: { product_name: string; size_spec?: string }) => (r.size_spec ? `${r.product_name} · ${r.size_spec}` : r.product_name);

/**
 * Hardware shop stock for the active branch: every product in its base unit (metres of cable, pieces of screws), what is low or
 * out, receiving a delivery in any unit (2 coils of a 90 m cable adds 180 m), and correcting a product by an adjustment or a count.
 * Costs are shown to owners and managers only (the server leaves them out for everyone else).
 */
export const StockScreen: React.FC = () => {
  const branchId = useAuthStore((s) => s.activeBranchId) ?? undefined;
  const [search, setSearch] = useState('');
  const [lowOnly, setLowOnly] = useState(false);
  const [selected, setSelected] = useState<HardwareStockRow | null>(null);
  const [receiving, setReceiving] = useState(false);
  const stock = useHardwareStock({ branch_id: branchId, search: search.trim() || undefined, low_only: lowOnly }, !!branchId);
  const rows = stock.data ?? [];
  const totals = useMemo(() => ({
    value: rows.reduce((n, r) => n + (r.stock_value_paise ?? 0), 0),
    low: rows.filter((r) => r.is_low || r.is_out).length,
    costs: rows.some((r) => r.cost_price !== undefined),
  }), [rows]);

  return (
    <View style={styles.container}>
      <View style={styles.toolbar}>
        <View style={{ flex: 1 }}>
          <Input value={search} onChangeText={setSearch} placeholder="Search a product, size or scan a barcode" />
        </View>
        <Button title="Receive delivery" onPress={() => setReceiving(true)} />
      </View>
      <View style={styles.summary}>
        <Text style={styles.hint}>{rows.length} products{totals.costs ? ` · worth ${money(totals.value)} at cost` : ''} · {totals.low} low or out</Text>
        <View style={styles.toggle}>
          <Text style={styles.hint}>Low or out only</Text>
          <Switch value={lowOnly} onValueChange={setLowOnly} />
        </View>
      </View>
      <QueryState query={stock} isEmpty={rows.length === 0} emptyText={lowOnly ? 'Nothing is low.' : 'No products yet.'}>
        <ScrollView contentContainerStyle={styles.list}>
          {rows.map((r) => (
            <Pressable key={r.product_id} onPress={() => setSelected(r)} accessibilityRole="button"
              accessibilityLabel={`${label(r)}: ${qty(r.quantity)} ${r.base_unit}`}
              style={({ pressed }) => [styles.row, pressed ? { opacity: 0.8 } : null]}>
              <View style={{ flex: 1 }}>
                <Text style={styles.body}>{label(r)}</Text>
                <Text style={styles.hint}>{[r.brand, r.category_name].filter(Boolean).join(' · ')}</Text>
              </View>
              <View style={styles.qtyBox}>
                <Text style={[styles.qty, r.is_out ? styles.outText : r.is_low ? styles.lowText : null]}>{qty(r.quantity)} {r.base_unit}</Text>
                <Text style={styles.hint}>{r.is_out ? 'Out' : r.is_low ? 'Low' : stackText(r)}</Text>
              </View>
            </Pressable>
          ))}
          {branchId ? <Deliveries branchId={branchId} /> : null}
        </ScrollView>
      </QueryState>
      {selected ? <ProductSheet branchId={branchId} row={selected} onClose={() => setSelected(null)} /> : null}
      {receiving ? <ReceiveSheet branchId={branchId} rows={rows} onClose={() => setReceiving(false)} /> : null}
    </View>
  );
};

/** "1 coil + 0 m" style hint: how many of the largest alternate unit fit in the stock. */
function stackText(r: HardwareStockRow): string {
  const big = [...r.units].sort((a, b) => b.factor - a.factor)[0];
  if (!big || r.quantity < big.factor) return '';
  const whole = Math.floor(r.quantity / big.factor);
  const rest = Number((r.quantity - whole * big.factor).toFixed(3));
  return `${whole} ${big.unit}${rest ? ` + ${rest} ${r.base_unit}` : ''}`;
}

const Deliveries: React.FC<{ branchId: string }> = ({ branchId }) => {
  const purchases = useHardwarePurchases(branchId);
  if (purchases.isError) return null; // cashiers do not see deliveries
  const items = purchases.data?.items ?? [];
  if (!items.length) return null;
  return (
    <View style={styles.card}>
      <Text style={styles.cardTitle}>Recent deliveries</Text>
      {items.slice(0, 10).map((p) => (
        <View key={p.id} style={styles.deliveryRow}>
          <Text style={styles.body}>{p.received_on} · {p.supplier_name ?? 'Supplier'}{p.invoice_number ? ` · ${p.invoice_number}` : ''}</Text>
          <Text style={styles.hint}>{p.lines.map((l) => `${qty(l.quantity)} ${l.unit}`).join(', ')} · {money(p.total_cost_paise)}</Text>
        </View>
      ))}
    </View>
  );
};

const ProductSheet: React.FC<{ branchId?: string; row: HardwareStockRow; onClose: () => void }> = ({ branchId, row, onClose }) => {
  const adjust = useAdjustHardwareStock();
  const update = useUpdateHardwareStock();
  const movements = useHardwareMovements({ branch_id: branchId, product_id: row.product_id });
  const [current, setCurrent] = useState(row);
  const [mode, setMode] = useState<'delta' | 'counted'>('delta');
  const [amount, setAmount] = useState('');
  const [reason, setReason] = useState('');
  const [low, setLow] = useState(row.low_stock_level !== undefined ? qty(row.low_stock_level) : '');
  const [cost, setCost] = useState(row.cost_price !== undefined ? rupees4(row.cost_price) : '');
  const [key, setKey] = useState(newIdempotencyKey);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => setKey(newIdempotencyKey()), [mode, amount, reason]);
  const value = parseFloat(amount);
  const canAdjust = !Number.isNaN(value) && (mode === 'counted' ? value >= 0 : value !== 0) && reason.trim().length >= 3;
  const showCost = row.cost_price !== undefined || current.cost_price !== undefined || !!cost;

  const save = async () => {
    setError(null);
    try {
      setCurrent(await adjust.mutateAsync({ input: { branch_id: branchId, product_id: row.product_id, [mode]: amount, reason }, idempotencyKey: key }));
      setAmount('');
      setReason('');
    } catch (e) {
      setError(errorText(e));
    }
  };
  const saveSettings = async () => {
    setError(null);
    try {
      setCurrent(await update.mutateAsync({ productId: row.product_id, changes: {
        branch_id: branchId, low_stock_level: low.trim() ? low : null, ...(showCost ? { cost_price: cost.trim() ? cost : null } : {}),
      } }));
    } catch (e) {
      setError(errorText(e));
    }
  };

  return (
    <Modal visible onClose={onClose} title={label(row)}
      subtitle={`${qty(current.quantity)} ${row.base_unit} on hand${row.barcode ? ` · ${row.barcode}` : ''}`}
      footer={<Button title="Close" variant="secondary" onPress={onClose} />}>
      <ScrollView style={{ maxHeight: 520 }} contentContainerStyle={{ gap: spacing.md }}>
        <View style={styles.wrap}>
          <Button title="Add or remove" size="sm" variant={mode === 'delta' ? 'primary' : 'secondary'} onPress={() => setMode('delta')} />
          <Button title="Count" size="sm" variant={mode === 'counted' ? 'primary' : 'secondary'} onPress={() => setMode('counted')} />
        </View>
        <Input label={mode === 'delta' ? `${row.base_unit} (+ to add, - to remove)` : `${row.base_unit} counted on the shelf`} value={amount}
          onChangeText={(v) => setAmount(decimal(v, mode === 'delta'))} keyboardType="numbers-and-punctuation" />
        {!row.allow_fraction ? <Text style={styles.hint}>This product is counted in whole pieces.</Text> : null}
        <Input label="Reason" value={reason} onChangeText={setReason} placeholder={mode === 'delta' ? 'e.g. cut and wasted' : 'e.g. monthly count'} />
        <Button title={mode === 'delta' ? 'Save adjustment' : 'Save count'} onPress={save} disabled={!canAdjust} loading={adjust.isPending} />

        <View style={styles.pair}>
          <View style={styles.field}><Input style={styles.fill} label={`Low at or below (${row.base_unit})`} value={low} onChangeText={(v) => setLow(decimal(v))} keyboardType="decimal-pad" /></View>
          {showCost ? (
            <View style={styles.field}><Input style={styles.fill} label={`Cost per ${row.base_unit} (₹)`} value={cost} onChangeText={(v) => setCost(decimal(v))} keyboardType="decimal-pad" /></View>
          ) : null}
          <Button title="Save" size="sm" variant="secondary" onPress={saveSettings} loading={update.isPending} />
        </View>
        {error ? <Text style={styles.error}>{error}</Text> : null}

        <Text style={styles.label}>History</Text>
        {movements.isError ? <Text style={styles.hint}>Only owners and managers see the history.</Text> : null}
        {(movements.data?.items ?? []).slice(0, 15).map((m) => (
          <View key={m.id} style={styles.deliveryRow}>
            <Text style={styles.body}>{m.kind.replace('_', ' ')} {m.quantity > 0 ? `+${qty(m.quantity)}` : qty(m.quantity)} → {qty(m.balance_after)} {m.base_unit}</Text>
            <Text style={styles.hint}>{formatDateTime(m.created_at)}{m.created_by ? ` · ${m.created_by}` : ''}{m.reason ? ` · ${m.reason}` : ''}</Text>
          </View>
        ))}
      </ScrollView>
    </Modal>
  );
};

type DeliveryLine = HardwarePurchaseInput['lines'][number] & { label: string; base: string };

/** A delivery: find a product, pick the unit it arrived in, type the quantity and what one of that unit cost, add more, receive. */
const ReceiveSheet: React.FC<{ branchId?: string; rows: HardwareStockRow[]; onClose: () => void }> = ({ branchId, rows, onClose }) => {
  const receive = useReceiveHardwareStock();
  const [find, setFind] = useState('');
  const [productId, setProductId] = useState<string | null>(null);
  const [unit, setUnit] = useState<string | null>(null);
  const [amount, setAmount] = useState('');
  const [cost, setCost] = useState('');
  const [lines, setLines] = useState<DeliveryLine[]>([]);
  const [supplier, setSupplier] = useState('');
  const [invoice, setInvoice] = useState('');
  const [key, setKey] = useState(newIdempotencyKey);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  useEffect(() => setKey(newIdempotencyKey()), [lines, supplier, invoice]);

  const product = rows.find((r) => r.product_id === productId);
  const words = find.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const matches = words.length
    ? rows.filter((r) => words.every((w) => `${r.product_name} ${r.brand ?? ''} ${r.size_spec ?? ''} ${r.sku ?? ''}`.toLowerCase().includes(w))).slice(0, 8)
    : [];
  const units = product ? [{ unit: product.base_unit, factor: 1 }, ...product.units] : [];
  const chosen = units.find((u) => u.unit === (unit ?? product?.base_unit)) ?? units[0];
  const typed = parseFloat(amount);
  const baseQuantity = chosen && !Number.isNaN(typed) ? Number((typed * chosen.factor).toFixed(3)) : undefined;
  const fractional = baseQuantity !== undefined && baseQuantity !== Math.round(baseQuantity);
  const blocked = !!product && !product.allow_fraction && fractional;

  const pick = (id: string) => { setProductId(id); setUnit(null); setAmount(''); setCost(''); };
  const addLine = () => {
    if (!product || !chosen || baseQuantity === undefined || typed <= 0 || blocked) return;
    const line: DeliveryLine = {
      product_id: product.product_id, unit: chosen.unit, quantity: amount, unit_cost: cost.trim() || undefined,
      label: `${label(product)} × ${amount} ${chosen.unit}`, base: `${qty(baseQuantity)} ${product.base_unit}`,
    };
    setLines((ls) => [...ls, line]);
    setProductId(null);
    setUnit(null);
    setAmount('');
    setCost('');
    setFind('');
  };

  const submit = async () => {
    setError(null);
    try {
      const saved = await receive.mutateAsync({ input: { branch_id: branchId, supplier_name: supplier, invoice_number: invoice,
        lines: lines.map(({ label: _label, base: _base, ...l }) => l) }, idempotencyKey: key });
      setDone(`Received ${saved.lines.map((l) => `${qty(l.quantity)} ${l.unit}`).join(', ')} (${money(saved.total_cost_paise)}).`);
      setLines([]);
    } catch (e) {
      setError(errorText(e));
    }
  };

  return (
    <Modal visible onClose={onClose} title="Receive a delivery" subtitle="In the unit it arrived in"
      footer={
        <>
          <Button title="Close" variant="secondary" onPress={onClose} />
          <View style={{ flex: 1 }} />
          <Button title={lines.length ? `Receive ${lines.length} item${lines.length === 1 ? '' : 's'}` : 'Receive'} onPress={submit} disabled={!lines.length} loading={receive.isPending} />
        </>
      }>
      <ScrollView style={{ maxHeight: 560 }} contentContainerStyle={{ gap: spacing.md }}>
        {done ? <Text style={styles.done}>{done}</Text> : null}
        <View style={styles.pair}>
          <View style={styles.field}><Input style={styles.fill} label="Supplier" value={supplier} onChangeText={setSupplier} /></View>
          <View style={styles.field}><Input style={styles.fill} label="Invoice number" value={invoice} onChangeText={setInvoice} /></View>
        </View>
        {product && chosen ? (
          <View style={styles.card}>
            <Text style={styles.cardTitle}>{label(product)}</Text>
            <View style={styles.wrap}>
              {units.map((u) => (
                <Button key={u.unit} title={u.factor === 1 ? u.unit : `${u.unit} (${qty(u.factor)} ${product.base_unit})`} size="sm"
                  variant={u.unit === chosen.unit ? 'primary' : 'secondary'} onPress={() => setUnit(u.unit)} />
              ))}
            </View>
            <View style={styles.pair}>
              <View style={styles.field}><Input style={styles.fill} label={`How many ${chosen.unit}`} value={amount} onChangeText={(v) => setAmount(decimal(v))} keyboardType="decimal-pad" /></View>
              <View style={styles.field}><Input style={styles.fill} label={`Cost per ${chosen.unit} (₹, optional)`} value={cost} onChangeText={(v) => setCost(decimal(v))} keyboardType="decimal-pad" /></View>
            </View>
            {baseQuantity !== undefined && typed > 0 ? (
              <Text style={blocked ? styles.error : styles.hint}>
                {blocked ? `${qty(baseQuantity)} ${product.base_unit} is part of a piece; this product is counted whole.`
                  : `Adds ${qty(baseQuantity)} ${product.base_unit}${cost.trim() && chosen.factor > 0 ? ` at ₹${rupees4(parseFloat(cost) / chosen.factor)} each` : ''}`}
              </Text>
            ) : null}
            <View style={styles.wrap}>
              <Button title="Add to delivery" size="sm" onPress={addLine} disabled={baseQuantity === undefined || !(typed > 0) || blocked} />
              <Button title="Cancel" size="sm" variant="ghost" onPress={() => setProductId(null)} />
            </View>
          </View>
        ) : (
          <>
            <Input label="Add a product" value={find} onChangeText={setFind} placeholder="Type a name, size or brand" />
            {matches.map((p) => (
              <Pressable key={p.product_id} onPress={() => pick(p.product_id)} style={styles.match}>
                <Text style={styles.body}>{label(p)}</Text>
                <Text style={styles.hint}>{[p.base_unit, ...p.units.map((u) => u.unit)].join(' · ')}</Text>
              </Pressable>
            ))}
          </>
        )}
        {lines.length ? <Text style={styles.label}>In this delivery</Text> : null}
        {lines.map((l, i) => (
          <View key={`${l.product_id}-${i}`} style={styles.deliveryRow}>
            <View style={{ flex: 1 }}>
              <Text style={styles.body}>{l.label}</Text>
              <Text style={styles.hint}>= {l.base}{l.unit_cost ? ` · ${money(Math.round(parseFloat(l.unit_cost) * 100))} each` : ''}</Text>
            </View>
            <Button title="Remove" size="sm" variant="ghost" onPress={() => setLines(lines.filter((_, j) => j !== i))} />
          </View>
        ))}
        {error ? <Text style={styles.error}>{error}</Text> : null}
      </ScrollView>
    </Modal>
  );
};

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  toolbar: { flexDirection: 'row', gap: spacing.sm, alignItems: 'center', paddingHorizontal: spacing.xl, paddingTop: spacing.md },
  summary: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingHorizontal: spacing.xl, paddingVertical: spacing.sm },
  toggle: { flexDirection: 'row', gap: spacing.sm, alignItems: 'center' },
  list: { paddingHorizontal: spacing.xl, paddingBottom: spacing.xxl, gap: spacing.sm },
  row: { flexDirection: 'row', alignItems: 'center', gap: spacing.md, backgroundColor: colors.surface, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.surfaceBorder, padding: spacing.lg },
  qtyBox: { alignItems: 'flex-end' },
  qty: { ...typography.bodyBold, color: colors.text },
  lowText: { color: colors.warning },
  outText: { color: colors.danger },
  card: { backgroundColor: colors.surface, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.surfaceBorder, padding: spacing.lg, gap: spacing.xs },
  cardTitle: { ...typography.bodyBold, color: colors.text },
  body: { ...typography.body, color: colors.text },
  hint: { ...typography.caption, color: colors.textMuted },
  label: { ...typography.caption, color: colors.textSecondary, fontFamily: fonts.semibold },
  deliveryRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: spacing.sm, flexWrap: 'wrap', paddingVertical: 4 },
  wrap: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  pair: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, alignItems: 'flex-end' },
  field: { flexGrow: 1, flexBasis: 140, minWidth: 0 },
  fill: { width: '100%' as const, minWidth: 0 },
  match: { padding: spacing.md, borderRadius: radii.md, backgroundColor: colors.surfaceLight, gap: 2 },
  error: { ...typography.caption, color: colors.danger },
  done: { ...typography.caption, color: colors.success, fontFamily: fonts.semibold },
});
