import React, { useMemo, useState } from 'react';
import { Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';
import { Badge, Button, colors, fonts, Input, Modal, radii, spacing, typography } from '@repo/shared-ui';
import { formatDateTime, formatPaiseToRupees } from '@repo/shared-utils';
import {
  ApiError,
  ItemUnit,
  MenuItem,
  newIdempotencyKey,
  StockItem,
  StockMovement,
  useAdjustStock,
  useCatalog,
  useReceiveStock,
  useStock,
  useStockMovements,
  useStockPurchases,
  useUpdateStockSettings,
} from '@repo/api-client';
import { Icon } from '../components/Icon';
import { QueryState } from '../components/QueryState';
import { useAuthStore } from '../stores/authStore';

const errorText = (e: unknown) => (e instanceof ApiError ? e.message : 'Something went wrong. Try again.');

/** 12 -> "12", 0.75 kg -> "0.75 kg". */
const qty = (quantity: number, unit: ItemUnit) => {
  const text = String(Math.round(quantity * 1000) / 1000);
  return unit === 'pcs' ? text : `${text} ${unit}`;
};
const toNumber = (text: string) => {
  const n = Number(text.trim());
  return text.trim() !== '' && Number.isFinite(n) ? n : null;
};
const toPaise = (rupees: string) => Math.round((parseFloat(rupees) || 0) * 100);

type View_ = 'stock' | 'deliveries';
type Filter = 'all' | 'low';

/**
 * Stock on hand for the branch (the `inventory` module): what is low or out, receiving a delivery, and correcting an item by
 * hand. An item is tracked from its first delivery or count; paid bills take tracked items out of stock by themselves.
 */
export const StockScreen: React.FC = () => {
  const branchId = useAuthStore((s) => s.activeBranchId);
  const stockQuery = useStock(branchId);
  const catalog = useCatalog();
  const [view, setView] = useState<View_>('stock');
  const [filter, setFilter] = useState<Filter>('all');
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<{ itemId: string; name: string; unit: ItemUnit } | null>(null);
  const [receiveOpen, setReceiveOpen] = useState(false);
  const [trackOpen, setTrackOpen] = useState(false);

  const rows = stockQuery.data ?? [];
  const low = rows.filter((r) => r.is_low || r.is_out);
  const value = rows.reduce((n, r) => n + (r.stock_value_paise ?? 0), 0);
  const hasCost = rows.some((r) => r.cost_price_paise !== undefined);
  const items = useMemo(() => (catalog.data ?? []).flatMap((c) => c.items).filter((i) => i.is_active), [catalog.data]);
  const untracked = useMemo(() => {
    const tracked = new Set(rows.map((r) => r.item_id));
    return items.filter((i) => !tracked.has(i.id));
  }, [items, rows]);

  const visible = useMemo(() => {
    const term = search.trim().toLowerCase();
    return (filter === 'low' ? low : rows).filter(
      (r) => !term || r.name.toLowerCase().includes(term) || r.barcode === search.trim() || r.sku?.toLowerCase() === term,
    );
  }, [rows, low, filter, search]);

  const selectedRow = selected ? rows.find((r) => r.item_id === selected.itemId) : undefined;

  return (
    <View style={styles.container}>
      <ScrollView
        contentContainerStyle={styles.content}
        refreshControl={<RefreshControl refreshing={stockQuery.isRefetching} onRefresh={() => void stockQuery.refetch()} />}
        keyboardShouldPersistTaps="handled"
      >
        <View style={styles.summary}>
          <Summary label="Tracked" value={String(rows.length)} />
          <Summary label="Low or out" value={String(low.length)} tone={low.length ? 'warn' : undefined} />
          {hasCost ? <Summary label="Stock value" value={formatPaiseToRupees(value)} /> : null}
        </View>

        <View style={styles.actions}>
          <Button title="Receive stock" onPress={() => setReceiveOpen(true)} icon={<Icon name="truck" size={16} color={colors.textInverse} />} style={{ flex: 1 }} />
          <Button title="Track an item" variant="secondary" onPress={() => setTrackOpen(true)} style={{ flex: 1 }} />
        </View>

        <View style={styles.chips} accessibilityRole="tablist">
          <Chip label="On hand" active={view === 'stock' && filter === 'all'} onPress={() => { setView('stock'); setFilter('all'); }} />
          <Chip label={`Low or out (${low.length})`} active={view === 'stock' && filter === 'low'} onPress={() => { setView('stock'); setFilter('low'); }} />
          <Chip label="Deliveries" active={view === 'deliveries'} onPress={() => setView('deliveries')} />
        </View>

        {view === 'deliveries' ? (
          <Deliveries branchId={branchId} />
        ) : (
          <>
            <Input value={search} onChangeText={setSearch} placeholder="Search by name, barcode or SKU" autoCapitalize="none" autoCorrect={false} />
            <QueryState
              query={stockQuery}
              isEmpty={visible.length === 0}
              emptyText={
                rows.length === 0
                  ? 'No items are tracked yet. Receive a delivery or track an item to start.'
                  : filter === 'low' ? 'Nothing is low or out.' : 'No items match.'
              }
            >
              <View style={styles.list}>
                {visible.map((row) => (
                  <Pressable
                    key={row.item_id}
                    onPress={() => setSelected({ itemId: row.item_id, name: row.name, unit: row.unit })}
                    accessibilityRole="button"
                    accessibilityLabel={`${row.name}, ${qty(row.quantity, row.unit)} in stock${row.is_out ? ', out of stock' : row.is_low ? ', low' : ''}`}
                    style={({ pressed }) => [styles.row, pressed ? { backgroundColor: colors.surfaceLight } : null]}
                  >
                    <View style={{ flex: 1 }}>
                      <Text style={styles.name} numberOfLines={1}>{row.name}</Text>
                      <Text style={styles.meta} numberOfLines={1}>
                        {[row.category_name, row.low_stock_level !== undefined ? `alert at ${qty(row.low_stock_level, row.unit)}` : null].filter(Boolean).join(' · ')}
                      </Text>
                    </View>
                    {row.is_out ? <Badge label="Out" variant="danger" size="sm" /> : row.is_low ? <Badge label="Low" variant="warning" size="sm" /> : null}
                    <Text style={[styles.quantity, row.is_out ? { color: colors.danger } : null]}>{qty(row.quantity, row.unit)}</Text>
                    <Icon name="chevron-right" size={18} color={colors.textMuted} />
                  </Pressable>
                ))}
              </View>
            </QueryState>
          </>
        )}
      </ScrollView>

      <StockItemSheet branchId={branchId} target={selected} row={selectedRow} onClose={() => setSelected(null)} />
      <ReceiveSheet branchId={branchId} items={items} visible={receiveOpen} onClose={() => setReceiveOpen(false)} />
      <ItemPicker
        title="Track an item"
        subtitle="Pick the item, then enter how many you have"
        items={untracked}
        visible={trackOpen}
        onClose={() => setTrackOpen(false)}
        onPick={(item) => {
          setTrackOpen(false);
          setSelected({ itemId: item.id, name: item.name, unit: item.unit });
        }}
      />
    </View>
  );
};

const Summary: React.FC<{ label: string; value: string; tone?: 'warn' }> = ({ label, value, tone }) => (
  <View style={styles.summaryCard}>
    <Text style={styles.summaryLabel}>{label}</Text>
    <Text style={[styles.summaryValue, tone === 'warn' ? { color: colors.warning } : null]}>{value}</Text>
  </View>
);

const Chip: React.FC<{ label: string; active: boolean; onPress: () => void }> = ({ label, active, onPress }) => (
  <Pressable onPress={onPress} accessibilityRole="tab" accessibilityState={{ selected: active }} style={[styles.chip, active ? styles.chipActive : null]}>
    <Text style={[styles.chipText, active ? { color: colors.textInverse } : null]}>{label}</Text>
  </Pressable>
);

// ---- deliveries ---------------------------------------------------------------------------------------------------------

const Deliveries: React.FC<{ branchId: string }> = ({ branchId }) => {
  const query = useStockPurchases(branchId);
  const purchases = query.data ?? [];
  return (
    <QueryState query={query} isEmpty={purchases.length === 0} emptyText="No deliveries received yet.">
      <View style={styles.list}>
        {purchases.map((p) => (
          <View key={p.id} style={styles.delivery}>
            <View style={styles.deliveryHead}>
              <Text style={styles.name} numberOfLines={1}>{p.supplier_name ?? 'Delivery'}</Text>
              {p.total_cost_paise ? <Text style={styles.quantity}>{formatPaiseToRupees(p.total_cost_paise)}</Text> : null}
            </View>
            <Text style={styles.meta}>
              {[p.received_on, p.invoice_number ? `Invoice ${p.invoice_number}` : null, p.created_by].filter(Boolean).join(' · ')}
            </Text>
            <Text style={styles.deliveryLines}>{p.lines.map((l) => `${qty(l.quantity, l.unit)} × ${l.name}`).join(', ')}</Text>
          </View>
        ))}
      </View>
    </QueryState>
  );
};

// ---- one item: count, add or remove, alert level, history ---------------------------------------------------------------

type Mode = 'count' | 'add' | 'remove';
const MODES: { mode: Mode; label: string }[] = [
  { mode: 'count', label: 'Counted' },
  { mode: 'add', label: 'Add' },
  { mode: 'remove', label: 'Remove' },
];
const KIND_LABEL: Record<StockMovement['kind'], string> = { purchase: 'Delivery', sale: 'Sale', adjustment: 'Adjustment', count: 'Count' };

const StockItemSheet: React.FC<{
  branchId: string;
  target: { itemId: string; name: string; unit: ItemUnit } | null;
  /** The item's stock, when it is already tracked. */
  row?: StockItem;
  onClose: () => void;
}> = ({ branchId, target, row, onClose }) => {
  const adjust = useAdjustStock(branchId);
  const settings = useUpdateStockSettings(branchId);
  const movements = useStockMovements(branchId, target?.itemId, !!target && !!row);
  const [mode, setMode] = useState<Mode>('count');
  const [amount, setAmount] = useState('');
  const [reason, setReason] = useState('');
  const [level, setLevel] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [key, setKey] = useState(newIdempotencyKey);

  const close = () => {
    setAmount('');
    setReason('');
    setLevel(null);
    setError(null);
    setMode('count');
    onClose();
  };
  if (!target) return null;
  const unit = target.unit;
  const levelText = level ?? (row?.low_stock_level !== undefined ? String(row.low_stock_level) : '');

  const save = async () => {
    const n = toNumber(amount);
    if (n === null || n < 0 || (mode !== 'count' && n === 0)) return setError('Enter a quantity.');
    const why = reason.trim() || (mode === 'count' ? 'Stock count' : '');
    if (why.length < 3) return setError('Say why (for example: damaged, expired, found).');
    setError(null);
    try {
      await adjust.mutateAsync({
        adjustment: { item_id: target.itemId, reason: why, ...(mode === 'count' ? { counted: n } : { delta: mode === 'add' ? n : -n }) },
        idempotencyKey: key,
      });
      setKey(newIdempotencyKey());
      setAmount('');
      setReason('');
    } catch (e) {
      setError(errorText(e));
    }
  };

  const saveLevel = async () => {
    const n = levelText.trim() === '' ? null : toNumber(levelText);
    if (levelText.trim() !== '' && (n === null || n < 0)) return setError('Enter the alert level as a number.');
    setError(null);
    try {
      await settings.mutateAsync({ itemId: target.itemId, settings: { low_stock_level: n } });
      setLevel(null);
    } catch (e) {
      setError(errorText(e));
    }
  };

  const stopTracking = async () => {
    try {
      await settings.mutateAsync({ itemId: target.itemId, settings: { is_tracked: false } });
      close();
    } catch (e) {
      setError(errorText(e));
    }
  };

  return (
    <Modal
      visible
      onClose={close}
      title={target.name}
      subtitle={row ? `${qty(row.quantity, unit)} in stock` : 'Not tracked yet'}
      footer={<Button title="Done" variant="secondary" onPress={close} style={{ flex: 1 }} />}
    >
      <View style={styles.segmented} accessibilityRole="tablist">
        {MODES.map((m) => (
          <Pressable key={m.mode} onPress={() => { setMode(m.mode); setError(null); }} accessibilityRole="tab" accessibilityState={{ selected: mode === m.mode }} style={[styles.segment, mode === m.mode ? styles.segmentActive : null]}>
            <Text style={[styles.segmentText, mode === m.mode ? styles.segmentTextActive : null]}>{m.label}</Text>
          </Pressable>
        ))}
      </View>
      <Input
        label={mode === 'count' ? 'How many are there now' : mode === 'add' ? 'How many to add' : 'How many to remove'}
        value={amount}
        onChangeText={setAmount}
        keyboardType="decimal-pad"
        suffix={unit === 'pcs' ? undefined : unit}
        placeholder="0"
      />
      <Input
        label={mode === 'count' ? 'Note (optional)' : 'Reason'}
        value={reason}
        onChangeText={setReason}
        placeholder={mode === 'remove' ? 'Damaged, expired, lost…' : mode === 'add' ? 'Found, returned by customer…' : 'Stock count'}
      />
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <Button title={mode === 'count' ? 'Save count' : mode === 'add' ? 'Add to stock' : 'Remove from stock'} onPress={save} loading={adjust.isPending} />

      {row ? (
        <>
          <Text style={styles.section}>Low-stock alert</Text>
          <View style={styles.levelRow}>
            <Input
              value={levelText}
              onChangeText={setLevel}
              keyboardType="decimal-pad"
              placeholder="No alert"
              suffix={unit === 'pcs' ? undefined : unit}
              containerStyle={{ flex: 1, marginBottom: 0 }}
              accessibilityLabel="Alert when stock is at or below"
            />
            <Button title="Save" variant="secondary" onPress={saveLevel} loading={settings.isPending && settings.variables?.settings.low_stock_level !== undefined} disabled={level === null} />
          </View>
          <Text style={styles.meta}>The item shows as low when stock is at or below this. Leave empty for no alert.</Text>

          <Text style={styles.section}>History</Text>
          <QueryState query={movements} isEmpty={(movements.data ?? []).length === 0} emptyText="No changes yet.">
            <View>
              {(movements.data ?? []).slice(0, 15).map((m) => (
                <View key={m.id} style={styles.move}>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.moveKind}>{KIND_LABEL[m.kind]}{m.reason ? ` · ${m.reason}` : ''}</Text>
                    <Text style={styles.meta}>{[formatDateTime(m.created_at), m.created_by].filter(Boolean).join(' · ')}</Text>
                  </View>
                  <Text style={[styles.moveQty, { color: m.quantity < 0 ? colors.danger : colors.success }]}>
                    {m.quantity > 0 ? '+' : ''}{qty(m.quantity, unit)}
                  </Text>
                  <Text style={styles.moveBalance}>{qty(m.balance_after, unit)}</Text>
                </View>
              ))}
            </View>
          </QueryState>
          <Button title="Stop tracking this item" variant="ghost" size="sm" onPress={stopTracking} />
        </>
      ) : null}
    </Modal>
  );
};

// ---- receiving a delivery -----------------------------------------------------------------------------------------------

interface Line { item: MenuItem; quantity: string; cost: string }

const ReceiveSheet: React.FC<{ branchId: string; items: MenuItem[]; visible: boolean; onClose: () => void }> = ({ branchId, items, visible, onClose }) => {
  const receive = useReceiveStock(branchId);
  const [supplier, setSupplier] = useState('');
  const [invoice, setInvoice] = useState('');
  const [lines, setLines] = useState<Line[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [key, setKey] = useState(newIdempotencyKey);

  const close = () => {
    setSupplier('');
    setInvoice('');
    setLines([]);
    setError(null);
    setKey(newIdempotencyKey());
    onClose();
  };
  const edit = (index: number, change: Partial<Line>) => {
    setLines((all) => all.map((l, i) => (i === index ? { ...l, ...change } : l)));
    setKey(newIdempotencyKey());
  };
  const total = lines.reduce((n, l) => n + Math.round((toNumber(l.quantity) ?? 0) * toPaise(l.cost)), 0);

  const save = async () => {
    const bad = lines.find((l) => !((toNumber(l.quantity) ?? 0) > 0));
    if (bad) return setError(`Enter how many ${bad.item.name} arrived.`);
    setError(null);
    try {
      await receive.mutateAsync({
        purchase: {
          supplier_name: supplier,
          invoice_number: invoice,
          lines: lines.map((l) => ({ item_id: l.item.id, quantity: toNumber(l.quantity)!, unit_cost_paise: l.cost.trim() ? toPaise(l.cost) : undefined })),
        },
        idempotencyKey: key,
      });
      close();
    } catch (e) {
      setError(errorText(e));
    }
  };

  return (
    <>
      <Modal
        visible={visible && !pickerOpen}
        onClose={close}
        title="Receive stock"
        subtitle="What arrived from a supplier"
        footer={
          <>
            <Button title="Cancel" variant="secondary" onPress={close} />
            <View style={{ flex: 1 }} />
            <Button title={total ? `Receive · ${formatPaiseToRupees(total)}` : 'Receive'} onPress={save} disabled={lines.length === 0} loading={receive.isPending} />
          </>
        }
      >
        <View style={styles.pair}>
          <Input label="Supplier" value={supplier} onChangeText={setSupplier} placeholder="Optional" containerStyle={{ flex: 1 }} />
          <Input label="Invoice no." value={invoice} onChangeText={setInvoice} placeholder="Optional" autoCapitalize="characters" containerStyle={{ flex: 1 }} />
        </View>
        {lines.map((line, index) => (
          <View key={line.item.id} style={styles.line}>
            <View style={styles.lineHead}>
              <Text style={[styles.name, { flex: 1 }]} numberOfLines={1}>{line.item.name}</Text>
              <Pressable onPress={() => { setLines((all) => all.filter((_, i) => i !== index)); setKey(newIdempotencyKey()); }} accessibilityRole="button" accessibilityLabel={`Remove ${line.item.name}`} hitSlop={8}>
                <Icon name="x" size={18} color={colors.textSecondary} />
              </Pressable>
            </View>
            <View style={styles.pair}>
              <Input label="Quantity" value={line.quantity} onChangeText={(v) => edit(index, { quantity: v })} keyboardType="decimal-pad" suffix={line.item.unit === 'pcs' ? undefined : line.item.unit} containerStyle={{ flex: 1, marginBottom: 0 }} />
              <Input label={`Cost per ${line.item.unit === 'pcs' ? 'piece' : line.item.unit}`} value={line.cost} onChangeText={(v) => edit(index, { cost: v })} keyboardType="decimal-pad" prefix="₹" placeholder="Optional" containerStyle={{ flex: 1, marginBottom: 0 }} />
            </View>
          </View>
        ))}
        <Button title={lines.length ? 'Add another item' : 'Add an item'} variant="outline" onPress={() => setPickerOpen(true)} icon={<Icon name="plus" size={16} color={colors.primary} />} />
        {error ? <Text style={styles.error}>{error}</Text> : null}
      </Modal>
      <ItemPicker
        title="Add an item"
        items={items.filter((i) => !lines.some((l) => l.item.id === i.id))}
        visible={visible && pickerOpen}
        onClose={() => setPickerOpen(false)}
        onPick={(item) => {
          setLines((all) => [...all, { item, quantity: '', cost: '' }]);
          setKey(newIdempotencyKey());
          setPickerOpen(false);
        }}
      />
    </>
  );
};

const ItemPicker: React.FC<{
  title: string;
  subtitle?: string;
  items: MenuItem[];
  visible: boolean;
  onClose: () => void;
  onPick: (item: MenuItem) => void;
}> = ({ title, subtitle, items, visible, onClose, onPick }) => {
  const [search, setSearch] = useState('');
  const term = search.trim().toLowerCase();
  const matches = items
    .filter((i) => !term || i.name.toLowerCase().includes(term) || i.barcode === search.trim() || i.sku?.toLowerCase() === term)
    .slice(0, 40);
  return (
    <Modal visible={visible} onClose={() => { setSearch(''); onClose(); }} title={title} subtitle={subtitle}>
      <Input value={search} onChangeText={setSearch} placeholder="Search by name, barcode or SKU" autoCapitalize="none" autoCorrect={false} autoFocus />
      <ScrollView style={{ maxHeight: 320 }} keyboardShouldPersistTaps="handled">
        {matches.length === 0 ? <Text style={styles.meta}>No items match.</Text> : null}
        {matches.map((item) => (
          <Pressable key={item.id} onPress={() => { setSearch(''); onPick(item); }} accessibilityRole="button" style={({ pressed }) => [styles.pick, pressed ? { backgroundColor: colors.surfaceLight } : null]}>
            <Text style={styles.name} numberOfLines={1}>{item.name}</Text>
            <Text style={styles.meta}>{formatPaiseToRupees(item.base_price_paise)}{item.unit !== 'pcs' ? `/${item.unit}` : ''}</Text>
          </Pressable>
        ))}
      </ScrollView>
    </Modal>
  );
};

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  content: { padding: spacing.xl, gap: spacing.md, paddingBottom: spacing.xxxl },
  summary: { flexDirection: 'row', gap: spacing.sm },
  summaryCard: {
    flex: 1, padding: spacing.md, borderRadius: radii.lg, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.surfaceBorder,
  },
  summaryLabel: { ...typography.label, color: colors.textMuted },
  summaryValue: { ...typography.h2, color: colors.text, marginTop: 2 },
  actions: { flexDirection: 'row', gap: spacing.sm },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  chip: {
    minHeight: 36, paddingHorizontal: spacing.lg, borderRadius: radii.full, borderWidth: 1, borderColor: colors.surfaceBorder,
    backgroundColor: colors.surface, justifyContent: 'center',
  },
  chipActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  chipText: { ...typography.label, color: colors.textSecondary },
  list: { borderRadius: radii.lg, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.surfaceBorder, overflow: 'hidden' },
  row: {
    flexDirection: 'row', alignItems: 'center', gap: spacing.sm, minHeight: 60, paddingHorizontal: spacing.lg, paddingVertical: spacing.sm,
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.surfaceBorder,
  },
  name: { ...typography.bodyBold, color: colors.text },
  meta: { ...typography.caption, color: colors.textSecondary },
  quantity: { ...typography.bodyBold, color: colors.text, minWidth: 56, textAlign: 'right' },
  delivery: { padding: spacing.lg, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.surfaceBorder, gap: 2 },
  deliveryHead: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: spacing.sm },
  deliveryLines: { ...typography.caption, color: colors.text, marginTop: spacing.xs },
  segmented: { flexDirection: 'row', backgroundColor: colors.surfaceLight, borderRadius: radii.md, padding: 3, marginBottom: spacing.md },
  segment: { flex: 1, minHeight: 36, alignItems: 'center', justifyContent: 'center', borderRadius: radii.sm },
  segmentActive: { backgroundColor: colors.surface },
  segmentText: { fontSize: 13, fontFamily: fonts.medium, color: colors.textSecondary },
  segmentTextActive: { color: colors.text, fontFamily: fonts.semibold },
  error: { ...typography.caption, color: colors.danger, marginVertical: spacing.sm },
  section: { ...typography.label, color: colors.textMuted, marginTop: spacing.xl, marginBottom: spacing.sm },
  levelRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, marginBottom: spacing.xs },
  move: {
    flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingVertical: spacing.sm,
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.surfaceBorder,
  },
  moveKind: { ...typography.caption, color: colors.text, fontFamily: fonts.semibold },
  moveQty: { ...typography.caption, fontFamily: fonts.bold, minWidth: 60, textAlign: 'right' },
  moveBalance: { ...typography.caption, color: colors.textSecondary, minWidth: 56, textAlign: 'right' },
  pair: { flexDirection: 'row', gap: spacing.sm },
  line: { padding: spacing.md, borderRadius: radii.md, backgroundColor: colors.surfaceLight, marginBottom: spacing.md, gap: spacing.sm },
  lineHead: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  pick: {
    minHeight: 52, paddingVertical: spacing.sm, paddingHorizontal: spacing.sm, borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.surfaceBorder, borderRadius: radii.sm,
  },
});
