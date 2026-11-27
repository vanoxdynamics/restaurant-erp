import React, { useEffect, useMemo, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { Button, colors, fonts, Input, Modal, radii, spacing, Switch, typography } from '@repo/shared-ui';
import { formatPaiseToRupees, NEGATIVE_STOCK_LIMIT, stockState, StockState } from '@repo/shared-utils';
import {
  ApiError,
  ApparelProduct,
  ApparelPurchaseInput,
  ApparelStockRow,
  newIdempotencyKey,
  useAdjustApparelStock,
  useApparelProducts,
  useApparelPurchases,
  useApparelStock,
  useReceiveApparelStock,
  useUpdateApparelStock,
} from '@repo/api-client';
import { QueryState } from '../../components/QueryState';
import { useAuthStore } from '../../stores/authStore';
import { ProductEditor } from './ProductsScreen';

const errorText = (e: unknown) => (e instanceof ApiError ? e.message : 'Something went wrong. Try again.');
const toPaise = (rupees: string) => Math.round((parseFloat(rupees) || 0) * 100);
const money = (paise: number) => formatPaiseToRupees(paise);

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
  const [selected, setSelected] = useState<string | null>(null);
  const [receiving, setReceiving] = useState(false);
  const stock = useApparelStock({ branch_id: branchId, search: search.trim() || undefined, low_only: lowOnly }, !!branchId);
  const products = useMemo(() => byProduct(stock.data ?? []), [stock.data]);
  const selectedProduct = products.find((p) => p.productId === selected);
  const totals = useMemo(() => {
    const rows = stock.data ?? [];
    return {
      pieces: rows.reduce((n, r) => n + Math.max(r.quantity, 0), 0),
      value: rows.reduce((n, r) => n + (r.stock_value_paise ?? 0), 0),
      low: rows.filter((r) => stockState(r.quantity, r.low_stock_level) === 'low').length,
      minus: rows.filter((r) => stockState(r.quantity, r.low_stock_level) === 'minus').length,
      locked: rows.filter((r) => stockState(r.quantity, r.low_stock_level) === 'locked').length,
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
        <Text style={styles.hint}>{totals.pieces} pieces{totals.costs ? ` · worth ${money(totals.value)} at cost` : ''}</Text>
        <View style={styles.toggle}>
          <Text style={styles.hint}>Low or out only</Text>
          <Switch value={lowOnly} onValueChange={setLowOnly} />
        </View>
      </View>
      <View style={styles.legend}>
        <LegendChip state="low" label={`Low stock (${totals.low})`} />
        <LegendChip state="minus" label={`Below zero (${totals.minus})`} />
        <LegendChip state="locked" label={`Locked at ${NEGATIVE_STOCK_LIMIT}, cannot bill (${totals.locked})`} />
      </View>
      <QueryState query={stock} isEmpty={products.length === 0} emptyText={lowOnly ? 'Nothing is low.' : 'No products yet.'}>
        <ScrollView contentContainerStyle={styles.list}>
          {products.map((p) => (
            <View key={p.productId} style={styles.card}>
              <View style={styles.deliveryRow}>
                <View>
                  <Text style={styles.cardTitle}>{p.name}</Text>
                  <Text style={styles.hint}>{p.category}</Text>
                </View>
                <View style={styles.cardActions}>
                  <Text style={styles.productTotal}>{available(Array.from(p.cells.values()))} in stock</Text>
                  <Button title="Update stock" size="sm" variant="secondary" onPress={() => setSelected(p.productId)} />
                </View>
              </View>
              <ScrollView horizontal>
                <View>
                  <View style={styles.gridRow}>
                    <Text style={[styles.gridHead, styles.colourCell]} />
                    {p.sizes.map((s) => <Text key={s} style={[styles.gridHead, styles.cell]}>{s}</Text>)}
                    <Text style={[styles.gridHead, styles.cell]}>Total</Text>
                  </View>
                  {p.colours.map((c) => (
                    <View key={c} style={styles.gridRow}>
                      <Text style={[styles.colourName, styles.colourCell]} numberOfLines={1}>{c}</Text>
                      {p.sizes.map((s) => {
                        const row = p.cells.get(`${s}|${c}`);
                        if (!row) return <Text key={s} style={[styles.cell, styles.empty]}>—</Text>;
                        return (
                          <Pressable key={s} onPress={() => setSelected(p.productId)} accessibilityRole="button"
                            accessibilityLabel={`${p.name} ${s} ${c}: ${row.quantity}`}
                            style={[styles.cell, styles.qtyCell, STATE_STYLE[stockState(row.quantity, row.low_stock_level)]]}>
                            <Text style={[styles.qty, stockState(row.quantity, row.low_stock_level) === 'locked' ? styles.qtyLocked : null]}>{row.quantity}</Text>
                          </Pressable>
                        );
                      })}
                      <Text style={[styles.cell, styles.rowTotal]}>
                        {available(p.sizes.map((s) => p.cells.get(`${s}|${c}`)).filter((r): r is ApparelStockRow => !!r))}
                      </Text>
                    </View>
                  ))}
                </View>
              </ScrollView>
            </View>
          ))}
          {branchId ? <Deliveries branchId={branchId} /> : null}
        </ScrollView>
      </QueryState>
      {selectedProduct ? <ProductStockSheet branchId={branchId} product={selectedProduct} onClose={() => setSelected(null)} /> : null}
      {receiving ? <ReceiveSheet branchId={branchId} onClose={() => setReceiving(false)} /> : null}
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

const ADD_REASONS = ['New delivery', 'Found on the shelf', 'Damaged / missing'];
const COUNT_REASONS = ['Shelf count', 'Monthly count'];

const STATE_STYLE: Record<StockState, object | null> = {
  ok: null,
  low: { backgroundColor: colors.warningBg, borderWidth: 1, borderColor: colors.warning },
  minus: { backgroundColor: colors.dangerBg, borderWidth: 1, borderColor: colors.danger },
  locked: { backgroundColor: colors.danger, borderWidth: 1, borderColor: colors.danger },
};

const NOTE_COLOUR: Record<StockState, object | null> = {
  ok: null, low: { color: colors.warning }, minus: { color: colors.danger }, locked: { color: colors.danger },
};

/** Pieces that can be sold: below-zero counts are not stock. */
const available = (rows: ApparelStockRow[]) => rows.reduce((n, r) => n + Math.max(r.quantity, 0), 0);

const LegendChip: React.FC<{ state: StockState; label: string }> = ({ state, label }) => (
  <View style={styles.legendItem}>
    <View style={[styles.legendSwatch, STATE_STYLE[state]]} />
    <Text style={styles.hint}>{label}</Text>
  </View>
);

/**
 * Update a whole style at once: every size and colour is listed with what is on hand, and the manager types only the ones
 * that changed, either pieces to add or the exact count on the shelf. One adjustment is saved per size typed.
 */
const ProductStockSheet: React.FC<{ branchId?: string; product: ProductStock; onClose: () => void }> = ({ branchId, product, onClose }) => {
  const adjust = useAdjustApparelStock();
  const update = useUpdateApparelStock();
  const rows = Array.from(product.cells.values());
  const anyMinus = rows.some((r) => r.quantity < 0);
  const [mode, setMode] = useState<'add' | 'count'>(anyMinus ? 'count' : 'add');
  const [reason, setReason] = useState((anyMinus ? COUNT_REASONS : ADD_REASONS)[0]);
  const [values, setValues] = useState<Record<string, string>>({});
  const lows = new Set(rows.map((r) => r.low_stock_level ?? null));
  const [low, setLow] = useState(lows.size === 1 && rows[0]?.low_stock_level != null ? String(rows[0].low_stock_level) : '');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const removing = mode === 'add' && reason === 'Damaged / missing';
  const typed = rows.filter((r) => (values[r.variant_id] ?? '') !== '' && (mode === 'count' || parseInt(values[r.variant_id], 10) > 0));
  const pieces = typed.reduce((n, r) => n + parseInt(values[r.variant_id], 10), 0);

  const switchMode = (m: 'add' | 'count') => {
    setMode(m);
    setReason((m === 'add' ? ADD_REASONS : COUNT_REASONS)[0]);
    setValues({});
    setDone(null);
  };

  /** Each typed size is saved on its own; the ones saved are cleared, so pressing again retries only what failed. */
  const save = async () => {
    setError(null);
    setDone(null);
    setSaving(true);
    let ok = 0;
    for (const r of typed) {
      const n = parseInt(values[r.variant_id], 10);
      const change = mode === 'count' ? { counted: n } : { delta: removing ? -n : n };
      try {
        await adjust.mutateAsync({ input: { branch_id: branchId, variant_id: r.variant_id, ...change, reason }, idempotencyKey: newIdempotencyKey() });
        ok += 1;
        setValues((v) => ({ ...v, [r.variant_id]: '' }));
      } catch (e) {
        setError(`${r.size} / ${r.colour}: ${errorText(e)}`);
      }
    }
    setSaving(false);
    if (ok) setDone(`Updated ${ok} size${ok === 1 ? '' : 's'}.`);
  };

  const saveLow = async () => {
    setError(null);
    setDone(null);
    try {
      for (const r of rows) {
        await update.mutateAsync({ variantId: r.variant_id, changes: { branch_id: branchId, low_stock_level: low.trim() ? parseInt(low, 10) : null } });
      }
      setDone('Low-stock alert saved for every size.');
    } catch (e) {
      setError(errorText(e));
    }
  };

  const reasons = mode === 'add' ? ADD_REASONS : COUNT_REASONS;
  const saveTitle = !typed.length ? (mode === 'count' ? 'Type the counts to save' : 'Type pieces to save')
    : mode === 'count' ? `Save ${typed.length} count${typed.length === 1 ? '' : 's'}`
      : `${removing ? 'Remove' : 'Add'} ${pieces} piece${pieces === 1 ? '' : 's'}`;

  return (
    <Modal visible onClose={onClose} title={`Update stock · ${product.name}`}
      subtitle={`${available(rows)} in stock · ${product.sizes.length} sizes × ${product.colours.length} colours`}
      footer={
        <>
          <Button title="Close" variant="secondary" onPress={onClose} />
          <View style={{ flex: 1 }} />
          <Button title={saveTitle} onPress={save} disabled={!typed.length} loading={saving} />
        </>
      }>
      <ScrollView style={{ maxHeight: 560 }} contentContainerStyle={{ gap: spacing.md }}>
        <View style={styles.wrap}>
          <Button title="Add pieces" size="sm" variant={mode === 'add' ? 'primary' : 'secondary'} onPress={() => switchMode('add')} />
          <Button title="Set exact count" size="sm" variant={mode === 'count' ? 'primary' : 'secondary'} onPress={() => switchMode('count')} />
        </View>
        <View style={styles.reasonRow}>
          <Text style={styles.label}>Reason</Text>
          {reasons.map((r) => (
            <Pressable key={r} onPress={() => setReason(r)} accessibilityRole="radio" accessibilityState={{ selected: reason === r }}
              style={[styles.reasonChip, reason === r ? styles.reasonChipOn : null]}>
              <Text style={[styles.reasonText, reason === r ? styles.reasonTextOn : null]}>{r}</Text>
            </Pressable>
          ))}
        </View>
        <Text style={styles.hint}>
          {mode === 'count' ? 'Type what is on the shelf for the sizes you counted. Leave the rest blank.'
            : removing ? 'Type how many to take off for each size. Leave the rest blank.'
              : 'Type how many arrived for each size. Leave the rest blank.'}
        </Text>

        <ScrollView horizontal>
          <View>
            <View style={styles.gridRow}>
              <Text style={[styles.gridHead, styles.colourCell]} />
              {product.sizes.map((s) => <Text key={s} style={[styles.gridHead, styles.inputCell]}>{s}</Text>)}
            </View>
            {product.colours.map((c) => (
              <View key={c} style={[styles.gridRow, { alignItems: 'flex-start' }]}>
                <Text style={[styles.colourName, styles.colourCell, { marginTop: 12 }]} numberOfLines={1}>{c}</Text>
                {product.sizes.map((s) => {
                  const r = product.cells.get(`${s}|${c}`);
                  if (!r) return <Text key={s} style={[styles.inputCell, styles.empty, { textAlign: 'center' }]}>—</Text>;
                  const state = stockState(r.quantity, r.low_stock_level);
                  const n = parseInt(values[r.variant_id] ?? '', 10);
                  const after = Number.isNaN(n) ? null : mode === 'count' ? n : removing ? r.quantity - n : r.quantity + n;
                  return (
                    <View key={s} style={styles.inputCell}>
                      <Input style={styles.fill}
                        value={values[r.variant_id] ?? ''} placeholder={String(r.quantity)} keyboardType="number-pad"
                        accessibilityLabel={`${s} ${c}, ${r.quantity} now`}
                        onChangeText={(v) => setValues({ ...values, [r.variant_id]: v.replace(/\D/g, '') })} />
                      <Text style={[styles.cellNote, NOTE_COLOUR[after === null ? state : stockState(after, r.low_stock_level)]]}>
                        {after === null ? `now ${r.quantity}` : `→ ${after}`}
                      </Text>
                    </View>
                  );
                })}
              </View>
            ))}
          </View>
        </ScrollView>
        {done ? <Text style={styles.done}>{done}</Text> : null}
        {error ? <Text style={styles.error}>{error}</Text> : null}

        <View style={{ gap: spacing.xs }}>
          <Text style={styles.label}>Warn me when any size is at or below</Text>
          <View style={[styles.pair, { alignItems: 'center' }]}>
            <View style={styles.field}>
              <Input style={styles.fill} containerStyle={{ marginBottom: 0 }} value={low} placeholder={lows.size > 1 ? 'differs by size' : 'e.g. 2'}
                accessibilityLabel="Warn me when any size is at or below"
                onChangeText={(v) => setLow(v.replace(/\D/g, ''))} keyboardType="number-pad" />
            </View>
            <Button title="Save" variant="secondary" onPress={saveLow} loading={update.isPending} />
          </View>
        </View>
      </ScrollView>
    </Modal>
  );
};

/** A catalogue style -> its size x colour grid of active variants, in the order the server sends them. */
function gridOf(p: ApparelProduct) {
  const variants = p.variants.filter((v) => v.is_active);
  return {
    name: p.name,
    sizes: Array.from(new Set(variants.map((v) => v.size))),
    colours: Array.from(new Set(variants.map((v) => v.colour))),
    cells: new Map(variants.map((v) => [`${v.size}|${v.colour}`, { variant_id: v.id, size: v.size, colour: v.colour }])),
  };
}

/**
 * A delivery: pick a style from the dropdown (or make a new one), type pieces per size and colour (and the cost per piece), add
 * more styles, then receive it all. The dropdown lists the catalogue, so styles with no stock yet can be received too.
 */
const ReceiveSheet: React.FC<{ branchId?: string; onClose: () => void }> = ({ branchId, onClose }) => {
  const receive = useReceiveApparelStock();
  const [find, setFind] = useState('');
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [picked, setPicked] = useState<ApparelProduct | null>(null);
  const catalogue = useApparelProducts({ search: find.trim() || undefined });
  const styles_ = catalogue.data?.items ?? [];
  const product = useMemo(() => (picked ? gridOf(picked) : null), [picked]);
  const [cells, setCells] = useState<Record<string, string>>({});
  const [cost, setCost] = useState('');
  const [lines, setLines] = useState<(ApparelPurchaseInput['lines'][number] & { label: string })[]>([]);
  const [supplier, setSupplier] = useState('');
  const [invoice, setInvoice] = useState('');
  const [key, setKey] = useState(newIdempotencyKey);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  useEffect(() => setKey(newIdempotencyKey()), [lines, supplier, invoice]);

  const pick = (p: ApparelProduct) => {
    setPicked(p);
    setCells({});
    setCost('');
    setOpen(false);
  };

  const addProduct = () => {
    if (!product) return;
    const added = Array.from(product.cells.values()).flatMap((r) => {
      const n = parseInt(cells[r.variant_id] ?? '', 10);
      return n > 0 ? [{ variant_id: r.variant_id, quantity: n, unit_cost_paise: cost.trim() ? toPaise(cost) : undefined,
        label: `${product.name} ${r.size} ${r.colour}` }] : [];
    });
    setLines((ls) => [...ls.filter((l) => !added.some((a) => a.variant_id === l.variant_id)), ...added]);
    setPicked(null);
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
            <View style={styles.deliveryRow}>
              <Text style={styles.cardTitle}>{product.name}</Text>
              <Button title="Change style" size="sm" variant="ghost" onPress={() => setPicked(null)} />
            </View>
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
          <View>
            <Text style={styles.label}>Add a style</Text>
            <View style={[styles.pair, { marginTop: 4, alignItems: 'center' }]}>
              <View style={{ flex: 1 }}>
                <Input style={styles.fill} value={find} placeholder="Search or pick from the list"
                  onChangeText={(v) => { setFind(v); setOpen(true); }} onFocus={() => setOpen(true)} />
              </View>
              <Button title={open ? 'Hide ▴' : 'All styles ▾'} variant="secondary" size="sm" onPress={() => setOpen(!open)} />
            </View>
            {open ? (
              <View style={styles.dropdown}>
                <ScrollView style={{ maxHeight: 240 }} nestedScrollEnabled keyboardShouldPersistTaps="handled">
                  {catalogue.isLoading ? <Text style={[styles.hint, styles.option]}>Loading styles…</Text> : null}
                  {!catalogue.isLoading && !styles_.length ? (
                    <Text style={[styles.hint, styles.option]}>{find.trim() ? `No style matches "${find.trim()}".` : 'No styles yet.'}</Text>
                  ) : null}
                  {styles_.map((p) => {
                    const g = gridOf(p);
                    return (
                      <Pressable key={p.id} onPress={() => pick(p)} style={styles.option} accessibilityRole="button">
                        <Text style={styles.body}>{p.name}</Text>
                        <Text style={styles.hint}>
                          {[p.style_code, p.category_name].filter(Boolean).join(' · ')}{p.style_code || p.category_name ? ' — ' : ''}
                          {g.sizes.join(' · ')} — {g.colours.join(', ')}
                        </Text>
                      </Pressable>
                    );
                  })}
                </ScrollView>
                <Pressable onPress={() => setCreating(true)} style={[styles.option, styles.newOption]} accessibilityRole="button">
                  <Text style={styles.newOptionText}>+ New style{find.trim() ? ` "${find.trim()}"` : ''}</Text>
                </Pressable>
              </View>
            ) : null}
          </View>
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
      {creating ? (
        <ProductEditor product={null} initialName={find.trim()} onClose={() => setCreating(false)}
          onCreated={(p) => { setCreating(false); setFind(''); pick(p); }} />
      ) : null}
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
  empty: { color: colors.textMuted, lineHeight: 34 },
  inputCell: { width: 64 },
  deliveryRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: spacing.sm, flexWrap: 'wrap', paddingVertical: 4 },
  wrap: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  pair: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, alignItems: 'flex-end' },
  field: { flexGrow: 1, flexBasis: 140, minWidth: 0 },
  fill: { width: '100%' as const, minWidth: 0 },
  reasonRow: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: 6 },
  reasonChip: { paddingVertical: 3, paddingHorizontal: 10, borderRadius: 999, borderWidth: 1, borderColor: colors.surfaceBorder },
  reasonChipOn: { borderColor: colors.primary, backgroundColor: colors.primaryGlow },
  reasonText: { ...typography.caption, color: colors.textSecondary },
  reasonTextOn: { color: colors.primary, fontFamily: fonts.semibold },
  cardActions: { alignItems: 'flex-end', gap: spacing.xs },
  cellNote: { ...typography.caption, color: colors.textMuted, textAlign: 'center', marginTop: 2 },
  legend: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.md, paddingHorizontal: spacing.xl, paddingBottom: spacing.sm },
  legendItem: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  legendSwatch: { width: 14, height: 14, borderRadius: 4 },
  productTotal: { ...typography.bodyBold, color: colors.text },
  rowTotal: { ...typography.bodyBold, color: colors.text, textAlign: 'center', lineHeight: 34 },
  qtyLocked: { color: colors.textInverse },
  stockBox: { alignItems: 'center', paddingVertical: spacing.md, borderRadius: radii.lg, backgroundColor: colors.surfaceLight, gap: 2 },
  stockNumber: { fontSize: 36, fontFamily: fonts.bold, color: colors.text },
  dropdown: { marginTop: spacing.xs, borderWidth: 1, borderColor: colors.surfaceBorder, borderRadius: radii.md, backgroundColor: colors.surface, overflow: 'hidden' },
  option: { paddingVertical: spacing.sm, paddingHorizontal: spacing.md, gap: 2, borderBottomWidth: 1, borderBottomColor: colors.surfaceBorder },
  newOption: { borderBottomWidth: 0, backgroundColor: colors.surfaceLight },
  newOptionText: { ...typography.bodyBold, color: colors.primary },
  error: { ...typography.caption, color: colors.danger },
  done: { ...typography.caption, color: colors.success, fontFamily: fonts.semibold },
});
