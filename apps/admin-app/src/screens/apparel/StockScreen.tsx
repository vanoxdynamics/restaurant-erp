import React, { useEffect, useMemo, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { Button, colors, fonts, Input, Modal, radii, spacing, Switch, typography } from '@repo/shared-ui';
import { formatDateTime, formatPaiseToRupees } from '@repo/shared-utils';
import {
  ApiError,
  ApparelPurchaseInput,
  ApparelStockRow,
  newIdempotencyKey,
  useAdjustApparelStock,
  useApparelMovements,
  useApparelPurchases,
  useApparelStock,
  useReceiveApparelStock,
  useUpdateApparelStock,
} from '@repo/api-client';
import { QueryState } from '../../components/QueryState';
import { useAuthStore } from '../../stores/authStore';

const errorText = (e: unknown) => (e instanceof ApiError ? e.message : 'Something went wrong. Try again.');
const toPaise = (rupees: string) => Math.round((parseFloat(rupees) || 0) * 100);
const money = (paise: number) => formatPaiseToRupees(paise);
const whole = (text: string) => text.replace(/[^\d-]/g, '').replace(/(?!^)-/g, '');

interface ProductStock {
  productId: string;
  name: string;
  category: string;
  sizes: string[];
  colours: string[];
  cells: Map<string, ApparelStockRow>;
}

/** Stock rows -> one entry per product with its size x colour grid (sizes and colours in the order the server sends). */
function byProduct(rows: ApparelStockRow[]): ProductStock[] {
  const out = new Map<string, ProductStock>();
  for (const r of rows) {
    let p = out.get(r.product_id);
    if (!p) {
      p = { productId: r.product_id, name: r.product_name, category: r.category_name, sizes: [], colours: [], cells: new Map() };
      out.set(r.product_id, p);
    }
    if (!p.sizes.includes(r.size)) p.sizes.push(r.size);
    if (!p.colours.includes(r.colour)) p.colours.push(r.colour);
    p.cells.set(`${r.size}|${r.colour}`, r);
  }
  return Array.from(out.values());
}

/**
 * Dress shop stock for the active branch: each style as a size x colour grid of pieces on hand, with what is low or out;
 * receiving a delivery; and correcting a variant by an adjustment or a count. Costs are shown to owners and managers only (the
 * server leaves them out for everyone else).
 */
export const StockScreen: React.FC = () => {
  const branchId = useAuthStore((s) => s.activeBranchId) ?? undefined;
  const [search, setSearch] = useState('');
  const [lowOnly, setLowOnly] = useState(false);
  const [selected, setSelected] = useState<ApparelStockRow | null>(null);
  const [receiving, setReceiving] = useState(false);
  const stock = useApparelStock({ branch_id: branchId, search: search.trim() || undefined, low_only: lowOnly }, !!branchId);
  const products = useMemo(() => byProduct(stock.data ?? []), [stock.data]);
  const totals = useMemo(() => {
    const rows = stock.data ?? [];
    return {
      pieces: rows.reduce((n, r) => n + Math.max(r.quantity, 0), 0),
      value: rows.reduce((n, r) => n + (r.stock_value_paise ?? 0), 0),
      low: rows.filter((r) => r.is_low || r.is_out).length,
      costs: rows.some((r) => r.cost_price_paise !== undefined),
    };
  }, [stock.data]);

  return (
    <View style={styles.container}>
      <View style={styles.toolbar}>
        <View style={{ flex: 1 }}>
          <Input value={search} onChangeText={setSearch} placeholder="Search a style, style code or scan a barcode" />
        </View>
        <Button title="Receive delivery" onPress={() => setReceiving(true)} />
      </View>
      <View style={styles.summary}>
        <Text style={styles.hint}>{totals.pieces} pieces{totals.costs ? ` · worth ${money(totals.value)} at cost` : ''} · {totals.low} sizes low or out</Text>
        <View style={styles.toggle}>
          <Text style={styles.hint}>Low or out only</Text>
          <Switch value={lowOnly} onValueChange={setLowOnly} />
        </View>
      </View>
      <QueryState query={stock} isEmpty={products.length === 0} emptyText={lowOnly ? 'Nothing is low.' : 'No products yet.'}>
        <ScrollView contentContainerStyle={styles.list}>
          {products.map((p) => (
            <View key={p.productId} style={styles.card}>
              <Text style={styles.cardTitle}>{p.name}</Text>
              <Text style={styles.hint}>{p.category}</Text>
              <ScrollView horizontal>
                <View>
                  <View style={styles.gridRow}>
                    <Text style={[styles.gridHead, styles.colourCell]} />
                    {p.sizes.map((s) => <Text key={s} style={[styles.gridHead, styles.cell]}>{s}</Text>)}
                  </View>
                  {p.colours.map((c) => (
                    <View key={c} style={styles.gridRow}>
                      <Text style={[styles.colourName, styles.colourCell]} numberOfLines={1}>{c}</Text>
                      {p.sizes.map((s) => {
                        const row = p.cells.get(`${s}|${c}`);
                        if (!row) return <Text key={s} style={[styles.cell, styles.empty]}>—</Text>;
                        return (
                          <Pressable key={s} onPress={() => setSelected(row)} accessibilityRole="button"
                            accessibilityLabel={`${p.name} ${s} ${c}: ${row.quantity}`}
                            style={[styles.cell, styles.qtyCell, row.is_out ? styles.out : row.is_low ? styles.low : null]}>
                            <Text style={styles.qty}>{row.quantity}</Text>
                          </Pressable>
                        );
                      })}
                    </View>
                  ))}
                </View>
              </ScrollView>
            </View>
          ))}
          {branchId ? <Deliveries branchId={branchId} /> : null}
        </ScrollView>
      </QueryState>
      {selected ? <VariantSheet branchId={branchId} row={selected} onClose={() => setSelected(null)} /> : null}
      {receiving ? <ReceiveSheet branchId={branchId} rows={stock.data ?? []} onClose={() => setReceiving(false)} /> : null}
    </View>
  );
};

const Deliveries: React.FC<{ branchId: string }> = ({ branchId }) => {
  const purchases = useApparelPurchases(branchId);
  if (purchases.isError) return null; // cashiers do not see deliveries
  const items = purchases.data?.items ?? [];
  if (!items.length) return null;
  return (
    <View style={styles.card}>
      <Text style={styles.cardTitle}>Recent deliveries</Text>
      {items.slice(0, 10).map((p) => (
        <View key={p.id} style={styles.deliveryRow}>
          <Text style={styles.body}>{p.received_on} · {p.supplier_name ?? 'Supplier'}{p.invoice_number ? ` · ${p.invoice_number}` : ''}</Text>
          <Text style={styles.hint}>{p.lines.reduce((n, l) => n + l.quantity, 0)} pieces · {money(p.total_cost_paise)}</Text>
        </View>
      ))}
    </View>
  );
};

const VariantSheet: React.FC<{ branchId?: string; row: ApparelStockRow; onClose: () => void }> = ({ branchId, row, onClose }) => {
  const adjust = useAdjustApparelStock();
  const update = useUpdateApparelStock();
  const movements = useApparelMovements({ branch_id: branchId, variant_id: row.variant_id });
  const [current, setCurrent] = useState(row);
  const [mode, setMode] = useState<'delta' | 'counted'>('delta');
  const [amount, setAmount] = useState('');
  const [reason, setReason] = useState('');
  const [low, setLow] = useState(row.low_stock_level?.toString() ?? '');
  const [cost, setCost] = useState(row.cost_price_paise !== undefined ? (row.cost_price_paise / 100).toFixed(2) : '');
  const [key, setKey] = useState(newIdempotencyKey);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => setKey(newIdempotencyKey()), [mode, amount, reason]);
  const value = parseInt(amount, 10);
  const canAdjust = !Number.isNaN(value) && (mode === 'counted' ? value >= 0 : value !== 0) && reason.trim().length >= 3;

  const save = async () => {
    setError(null);
    try {
      setCurrent(await adjust.mutateAsync({ input: { branch_id: branchId, variant_id: row.variant_id, [mode]: value, reason }, idempotencyKey: key }));
      setAmount('');
      setReason('');
    } catch (e) {
      setError(errorText(e));
    }
  };
  const saveSettings = async () => {
    setError(null);
    try {
      setCurrent(await update.mutateAsync({ variantId: row.variant_id, changes: {
        branch_id: branchId, low_stock_level: low.trim() ? parseInt(low, 10) : null,
        ...(row.cost_price_paise !== undefined || cost.trim() ? { cost_price_paise: cost.trim() ? toPaise(cost) : null } : {}),
      } }));
    } catch (e) {
      setError(errorText(e));
    }
  };

  return (
    <Modal visible onClose={onClose} title={`${row.product_name} · ${row.size} · ${row.colour}`}
      subtitle={`${current.quantity} on hand${row.barcode ? ` · ${row.barcode}` : ''}`}
      footer={<Button title="Close" variant="secondary" onPress={onClose} />}>
      <ScrollView style={{ maxHeight: 520 }} contentContainerStyle={{ gap: spacing.md }}>
        <View style={styles.wrap}>
          <Button title="Add or remove" size="sm" variant={mode === 'delta' ? 'primary' : 'secondary'} onPress={() => setMode('delta')} />
          <Button title="Count" size="sm" variant={mode === 'counted' ? 'primary' : 'secondary'} onPress={() => setMode('counted')} />
        </View>
        <Input label={mode === 'delta' ? 'Pieces (+ to add, - to remove)' : 'Pieces counted on the shelf'} value={amount}
          onChangeText={(v) => setAmount(mode === 'delta' ? whole(v) : v.replace(/\D/g, ''))} keyboardType="numbers-and-punctuation" />
        <Input label="Reason" value={reason} onChangeText={setReason} placeholder={mode === 'delta' ? 'e.g. damaged in the trial room' : 'e.g. monthly count'} />
        <Button title={mode === 'delta' ? 'Save adjustment' : 'Save count'} onPress={save} disabled={!canAdjust} loading={adjust.isPending} />

        <View style={styles.pair}>
          <View style={styles.field}><Input style={styles.fill} label="Low at or below" value={low} onChangeText={(v) => setLow(v.replace(/\D/g, ''))} keyboardType="number-pad" /></View>
          {row.cost_price_paise !== undefined || current.cost_price_paise !== undefined || !!cost ? (
            <View style={styles.field}><Input style={styles.fill} label="Cost per piece (₹)" value={cost} onChangeText={setCost} keyboardType="decimal-pad" /></View>
          ) : null}
          <Button title="Save" size="sm" variant="secondary" onPress={saveSettings} loading={update.isPending} />
        </View>
        {error ? <Text style={styles.error}>{error}</Text> : null}

        <Text style={styles.label}>History</Text>
        {movements.isError ? <Text style={styles.hint}>Only owners and managers see the history.</Text> : null}
        {(movements.data?.items ?? []).slice(0, 15).map((m) => (
          <View key={m.id} style={styles.deliveryRow}>
            <Text style={styles.body}>{m.kind.replace('_', ' ')} {m.quantity > 0 ? `+${m.quantity}` : m.quantity} → {m.balance_after}</Text>
            <Text style={styles.hint}>{formatDateTime(m.created_at)}{m.created_by ? ` · ${m.created_by}` : ''}{m.reason ? ` · ${m.reason}` : ''}</Text>
          </View>
        ))}
      </ScrollView>
    </Modal>
  );
};

/** A delivery: pick a style, type pieces per size and colour (and the cost per piece), add more styles, then receive it all. */
const ReceiveSheet: React.FC<{ branchId?: string; rows: ApparelStockRow[]; onClose: () => void }> = ({ branchId, rows, onClose }) => {
  const receive = useReceiveApparelStock();
  const products = useMemo(() => byProduct(rows), [rows]);
  const [find, setFind] = useState('');
  const [productId, setProductId] = useState<string | null>(null);
  const [cells, setCells] = useState<Record<string, string>>({});
  const [cost, setCost] = useState('');
  const [lines, setLines] = useState<(ApparelPurchaseInput['lines'][number] & { label: string })[]>([]);
  const [supplier, setSupplier] = useState('');
  const [invoice, setInvoice] = useState('');
  const [key, setKey] = useState(newIdempotencyKey);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  useEffect(() => setKey(newIdempotencyKey()), [lines, supplier, invoice]);

  const product = products.find((p) => p.productId === productId);
  const matches = find.trim() ? products.filter((p) => p.name.toLowerCase().includes(find.trim().toLowerCase())).slice(0, 8) : [];

  const addProduct = () => {
    if (!product) return;
    const added = Array.from(product.cells.values()).flatMap((r) => {
      const n = parseInt(cells[r.variant_id] ?? '', 10);
      return n > 0 ? [{ variant_id: r.variant_id, quantity: n, unit_cost_paise: cost.trim() ? toPaise(cost) : undefined,
        label: `${product.name} ${r.size} ${r.colour}` }] : [];
    });
    setLines((ls) => [...ls.filter((l) => !added.some((a) => a.variant_id === l.variant_id)), ...added]);
    setProductId(null);
    setCells({});
    setCost('');
    setFind('');
  };

  const submit = async () => {
    setError(null);
    try {
      const saved = await receive.mutateAsync({ input: { branch_id: branchId, supplier_name: supplier, invoice_number: invoice,
        lines: lines.map(({ label: _label, ...l }) => l) }, idempotencyKey: key });
      setDone(`Received ${saved.lines.reduce((n, l) => n + l.quantity, 0)} pieces (${money(saved.total_cost_paise)}).`);
      setLines([]);
    } catch (e) {
      setError(errorText(e));
    }
  };

  return (
    <Modal visible onClose={onClose} title="Receive a delivery" subtitle="Pieces per size and colour"
      footer={
        <>
          <Button title="Close" variant="secondary" onPress={onClose} />
          <View style={{ flex: 1 }} />
          <Button title={`Receive ${lines.reduce((n, l) => n + l.quantity, 0)} pieces`} onPress={submit} disabled={!lines.length} loading={receive.isPending} />
        </>
      }>
      <ScrollView style={{ maxHeight: 560 }} contentContainerStyle={{ gap: spacing.md }}>
        {done ? <Text style={styles.done}>{done}</Text> : null}
        <View style={styles.pair}>
          <View style={styles.field}><Input style={styles.fill} label="Supplier" value={supplier} onChangeText={setSupplier} /></View>
          <View style={styles.field}><Input style={styles.fill} label="Invoice number" value={invoice} onChangeText={setInvoice} /></View>
        </View>
        {product ? (
          <View style={styles.card}>
            <Text style={styles.cardTitle}>{product.name}</Text>
            {product.colours.map((c) => (
              <View key={c} style={styles.gridRow}>
                <Text style={[styles.colourName, styles.colourCell]} numberOfLines={1}>{c}</Text>
                {product.sizes.map((s) => {
                  const r = product.cells.get(`${s}|${c}`);
                  return r ? (
                    <View key={s} style={styles.inputCell}>
                      <Input style={styles.fill} label={s} value={cells[r.variant_id] ?? ''} keyboardType="number-pad"
                        onChangeText={(v) => setCells({ ...cells, [r.variant_id]: v.replace(/\D/g, '') })} />
                    </View>
                  ) : <View key={s} style={styles.inputCell} />;
                })}
              </View>
            ))}
            <View style={styles.pair}>
              <View style={styles.field}><Input style={styles.fill} label="Cost per piece (₹, optional)" value={cost} onChangeText={setCost} keyboardType="decimal-pad" /></View>
              <Button title="Add to delivery" size="sm" onPress={addProduct} />
            </View>
          </View>
        ) : (
          <>
            <Input label="Add a style" value={find} onChangeText={setFind} placeholder="Type a name" />
            {matches.map((p) => (
              <Pressable key={p.productId} onPress={() => setProductId(p.productId)} style={styles.match}>
                <Text style={styles.body}>{p.name}</Text>
                <Text style={styles.hint}>{p.sizes.join(' · ')} — {p.colours.join(', ')}</Text>
              </Pressable>
            ))}
          </>
        )}
        {lines.length ? <Text style={styles.label}>In this delivery</Text> : null}
        {lines.map((l) => (
          <View key={l.variant_id} style={styles.deliveryRow}>
            <Text style={styles.body}>{l.label} × {l.quantity}</Text>
            <Button title="Remove" size="sm" variant="ghost" onPress={() => setLines(lines.filter((x) => x.variant_id !== l.variant_id))} />
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
  card: { backgroundColor: colors.surface, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.surfaceBorder, padding: spacing.lg, gap: spacing.xs },
  cardTitle: { ...typography.bodyBold, color: colors.text },
  body: { ...typography.body, color: colors.text },
  hint: { ...typography.caption, color: colors.textMuted },
  label: { ...typography.caption, color: colors.textSecondary, fontFamily: fonts.semibold },
  gridRow: { flexDirection: 'row', alignItems: 'center', gap: 4, marginTop: 4 },
  gridHead: { ...typography.caption, color: colors.textSecondary, fontFamily: fonts.semibold, textAlign: 'center' },
  colourCell: { width: 90 },
  colourName: { ...typography.caption, color: colors.text },
  cell: { width: 52, height: 34, textAlign: 'center' },
  qtyCell: { alignItems: 'center', justifyContent: 'center', borderRadius: radii.sm, backgroundColor: colors.surfaceLight },
  qty: { ...typography.bodyBold, color: colors.text },
  low: { backgroundColor: colors.warningBg ?? colors.surfaceLight, borderWidth: 1, borderColor: colors.warning },
  out: { backgroundColor: colors.dangerBg, borderWidth: 1, borderColor: colors.danger },
  empty: { color: colors.textMuted, lineHeight: 34 },
  inputCell: { width: 64 },
  deliveryRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: spacing.sm, flexWrap: 'wrap', paddingVertical: 4 },
  wrap: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  pair: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, alignItems: 'flex-end' },
  field: { flexGrow: 1, flexBasis: 140, minWidth: 0 },
  fill: { width: '100%' as const, minWidth: 0 },
  match: { padding: spacing.md, borderRadius: radii.md, backgroundColor: colors.surfaceLight, gap: 2 },
  error: { ...typography.caption, color: colors.danger },
  done: { ...typography.caption, color: colors.success, fontFamily: fonts.semibold },
});
