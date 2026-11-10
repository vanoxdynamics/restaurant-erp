import React, { useMemo, useRef, useState } from 'react';
import {
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  useWindowDimensions,
  View,
} from 'react-native';
import { Button, colors, fonts, Modal, radii, spacing, typography } from '@repo/shared-ui';
import { formatPaiseToRupees } from '@repo/shared-utils';
import { ApiError, apiClient, CartLine, MenuItem, newIdempotencyKey, useCatalog, useCreateOrder, useOpenTakeaways } from '@repo/api-client';
import { formatQuantity, roundQuantity, useBusiness } from '../business';
import { Icon } from '../components/Icon';
import { CashDayBanner, cashButtonLabel, useCounterCashDay } from '../components/CashDayBanner';
import { ItemOptionsSheet, StepButton } from '../components/ItemOptionsSheet';
import { QueryState } from '../components/QueryState';
import { TopBar } from '../components/TopBar';
import { WeightSheet } from './WeightSheet';
import { usePrintQueueStore } from '../printing/printQueue';
import { useCartStore } from '../stores/cartStore';
import { useDeviceStore } from '../stores/deviceStore';

/** The quick sale's cart lives in the shared cart store under this key, so it survives a trip to another screen. */
const CART = 'quick-sale';

export interface QuickSaleProps {
  /** The sale was saved as an order: take it to the bill screen. */
  onBill: (orderId: string, title: string) => void;
  onOpenBills: () => void;
  onPrinter: () => void;
  onDayEnd: () => void;
}

export const saleTitle = (order: { order_number: string; token_number?: number }) =>
  order.token_number ? `Token ${order.token_number}` : `Sale #${order.order_number.split('-').pop()}`;

const lineTotal = (l: CartLine) => Math.round(l.unit_price_paise * l.quantity);

/**
 * The counter's selling screen for cafés and shops: search or scan, tap to add, weigh loose items, then bill and pay. One order
 * per sale, created with its whole cart when the cashier taps "Bill"; nothing is sent anywhere before that.
 */
export const QuickSaleScreen: React.FC<QuickSaleProps> = ({ onBill, onOpenBills, onPrinter, onDayEnd }) => {
  const device = useDeviceStore((s) => s.device)!;
  const { settings, words } = useBusiness();
  const catalogQuery = useCatalog();
  const openQuery = useOpenTakeaways(device.branchId);
  const createOrder = useCreateOrder();
  const failedPrints = usePrintQueueStore((s) => s.jobs.filter((j) => j.state === 'failed').length);
  const cashDay = useCounterCashDay();
  const cart = useCartStore((s) => s.carts[CART]) ?? [];
  const { add, setQuantity, remove, clear } = useCartStore();
  const wide = useWindowDimensions().width >= 900;

  const [search, setSearch] = useState('');
  const [categoryId, setCategoryId] = useState<string | null>(null);
  const [optionsItem, setOptionsItem] = useState<MenuItem | null>(null);
  const [weighItem, setWeighItem] = useState<MenuItem | null>(null);
  const [cartOpen, setCartOpen] = useState(false);
  const [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);
  const [saleKey, setSaleKey] = useState(newIdempotencyKey);
  const searchRef = useRef<TextInput>(null);

  const categories = catalogQuery.data ?? [];
  const allItems = useMemo(() => categories.flatMap((c) => c.items).filter((i) => i.is_active), [categories]);
  const activeCategoryId = categoryId ?? categories[0]?.id;
  const items = useMemo(() => {
    const term = search.trim().toLowerCase();
    if (!term) return (categories.find((c) => c.id === activeCategoryId)?.items ?? []).filter((i) => i.is_active);
    return allItems
      .filter((i) => i.name.toLowerCase().includes(term) || i.barcode === search.trim() || i.sku?.toLowerCase() === term)
      .slice(0, 60);
  }, [categories, allItems, activeCategoryId, search]);

  const count = cart.length;
  const total = cart.reduce((n, l) => n + lineTotal(l), 0);
  const openSales = (openQuery.data ?? []).length;

  const changed = () => {
    setSaleKey(newIdempotencyKey());
    setMessage(null);
  };

  const addPlain = (item: MenuItem, quantity = 1) => {
    add(CART, {
      item_id: item.id,
      name: item.name,
      modifier_ids: [],
      modifier_names: [],
      unit_price_paise: item.base_price_paise,
      quantity,
    });
    changed();
  };

  const pick = (item: MenuItem) => {
    if (!item.is_available_86) return;
    if (item.variants.length > 0 || (item.modifier_groups ?? []).length > 0) setOptionsItem(item);
    else if (item.sold_by_weight && settings.weighed_items) setWeighItem(item);
    else addPlain(item);
  };

  /** Enter in the search box: what a keyboard-style barcode scanner sends after the code. */
  const submitSearch = async () => {
    const code = search.trim();
    if (!code) return;
    const local = allItems.find((i) => i.barcode === code || i.sku === code);
    if (local) {
      pick(local);
      setSearch('');
      return;
    }
    if (items.length === 1) {
      pick(items[0]);
      setSearch('');
      return;
    }
    if (!settings.barcode || /\s/.test(code)) return;
    try {
      const found = await apiClient.lookupBarcode(code);
      pick(found);
      setSearch('');
    } catch (e) {
      setMessage({ text: e instanceof ApiError && e.status === 404 ? `No item has the barcode ${code}.` : 'Could not look up the barcode.', error: true });
    } finally {
      searchRef.current?.focus();
    }
  };

  const bill = async () => {
    if (!count) return;
    setMessage(null);
    try {
      const order = await createOrder.mutateAsync({ orderType: 'counter_sale', guestCount: 1, items: cart, idempotencyKey: saleKey });
      clear(CART);
      setSaleKey(newIdempotencyKey());
      setCartOpen(false);
      onBill(order.id, saleTitle(order));
    } catch (e) {
      setMessage({ text: e instanceof ApiError ? e.message : 'Could not save the sale. Try again.', error: true });
    }
  };

  const step = (line: CartLine, delta: number) => {
    setQuantity(CART, line.key, roundQuantity(line.quantity + delta));
    changed();
  };

  const cartPanel = (
    <View style={styles.cart}>
      <ScrollView contentContainerStyle={{ gap: spacing.sm }}>
        {cart.length === 0 ? <Text style={styles.empty}>Tap items to add them to the sale.</Text> : null}
        {cart.map((line) => {
          const item = allItems.find((i) => i.id === line.item_id);
          const weighed = !!item?.sold_by_weight && settings.weighed_items;
          return (
            <View key={line.key} style={styles.cartLine}>
              <View style={{ flex: 1 }}>
                <Text style={styles.cartName} numberOfLines={2}>
                  {line.variant_name ? `${line.name} (${line.variant_name})` : line.name}
                </Text>
                <Text style={styles.cartMeta}>
                  {weighed
                    ? `${formatQuantity(line.quantity, item?.unit)} × ${formatPaiseToRupees(line.unit_price_paise)}/${item?.unit}`
                    : [formatPaiseToRupees(line.unit_price_paise), ...line.modifier_names].join(' · ')}
                </Text>
              </View>
              {weighed ? (
                <Pressable onPress={() => { remove(CART, line.key); changed(); }} accessibilityRole="button" accessibilityLabel={`Remove ${line.name}`} hitSlop={8}>
                  <Icon name="x" size={18} color={colors.textSecondary} />
                </Pressable>
              ) : (
                <View style={styles.stepper}>
                  <StepButton icon="minus" label={`One less ${line.name}`} size={36} onPress={() => step(line, -1)} />
                  <Text style={styles.qty}>{line.quantity}</Text>
                  <StepButton icon="plus" label={`One more ${line.name}`} size={36} onPress={() => step(line, 1)} />
                </View>
              )}
              <Text style={styles.lineTotal}>{formatPaiseToRupees(lineTotal(line))}</Text>
            </View>
          );
        })}
      </ScrollView>
      <View style={styles.cartFoot}>
        <View style={styles.totalRow}>
          <Text style={styles.totalLabel}>{count} {count === 1 ? 'line' : 'lines'} · before GST</Text>
          <Text style={styles.total}>{formatPaiseToRupees(total)}</Text>
        </View>
        <View style={{ flexDirection: 'row', gap: spacing.sm }}>
          {count ? <Button title="Clear" variant="secondary" onPress={() => { clear(CART); changed(); }} /> : null}
          <Button title="Bill and pay" onPress={bill} disabled={!count} loading={createOrder.isPending} style={{ flex: 1 }} />
        </View>
      </View>
    </View>
  );

  return (
    <View style={styles.container}>
      <TopBar
        title="Quick sale"
        subtitle={device.branchName}
        right={
          <View style={styles.topActions}>
            <Button title={openSales ? `Open bills (${openSales})` : 'Open bills'} variant="secondary" size="sm" onPress={onOpenBills} />
            {wide ? (
              <Button title="Printer" variant="secondary" size="sm" onPress={onPrinter} icon={<Icon name="printer" size={14} color={colors.text} />} />
            ) : null}
            <Button title={cashButtonLabel(cashDay?.status)} variant="secondary" size="sm" onPress={onDayEnd} />
          </View>
        }
      />
      <CashDayBanner onPress={onDayEnd} />
      {failedPrints > 0 ? (
        <Pressable onPress={onPrinter} style={styles.alert} accessibilityRole="button">
          <Icon name="alert-triangle" size={16} color={colors.danger} />
          <Text style={styles.alertText}>{failedPrints} KOT{failedPrints === 1 ? '' : 's'} did not print. Tap to retry.</Text>
        </Pressable>
      ) : null}

      <View style={[styles.body, wide ? { flexDirection: 'row' } : null]}>
        <View style={styles.items}>
          <View style={styles.searchBox}>
            <Icon name={settings.barcode ? 'maximize' : 'search'} size={18} color={colors.textSecondary} />
            <TextInput
              ref={searchRef}
              value={search}
              onChangeText={(v) => {
                setSearch(v);
                setMessage(null);
              }}
              onSubmitEditing={submitSearch}
              placeholder={words.searchPlaceholder}
              placeholderTextColor={colors.textMuted}
              autoFocus={Platform.OS === 'web'}
              autoCorrect={false}
              autoCapitalize="none"
              blurOnSubmit={false}
              returnKeyType="search"
              accessibilityLabel={words.searchPlaceholder}
              style={styles.searchInput}
            />
            {search ? (
              <Pressable onPress={() => setSearch('')} accessibilityRole="button" accessibilityLabel="Clear search" hitSlop={8}>
                <Icon name="x" size={18} color={colors.textSecondary} />
              </Pressable>
            ) : null}
          </View>
          {message ? <Text style={[styles.message, message.error ? { color: colors.danger } : null]}>{message.text}</Text> : null}

          {!search ? (
            <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.tabsRow} contentContainerStyle={{ gap: spacing.sm }}>
              {categories.map((c) => (
                <TouchableOpacity
                  key={c.id}
                  onPress={() => setCategoryId(c.id)}
                  accessibilityRole="tab"
                  accessibilityState={{ selected: activeCategoryId === c.id }}
                  style={[styles.tab, activeCategoryId === c.id ? styles.tabActive : null]}
                >
                  <Text style={[styles.tabText, activeCategoryId === c.id ? { color: colors.textInverse } : null]}>{c.name}</Text>
                </TouchableOpacity>
              ))}
            </ScrollView>
          ) : null}

          <QueryState query={catalogQuery} isEmpty={items.length === 0} emptyText={search ? 'No items match.' : `No ${words.items.toLowerCase()} yet.`}>
            <ScrollView contentContainerStyle={styles.grid} keyboardShouldPersistTaps="handled">
              {items.map((item) => {
                const soldOut = !item.is_available_86;
                const weighed = item.sold_by_weight && settings.weighed_items;
                const price = item.variants.length ? Math.min(...item.variants.map((v) => v.price_paise)) : item.base_price_paise;
                const inCart = cart.filter((l) => l.item_id === item.id).reduce((n, l) => n + (weighed ? 1 : l.quantity), 0);
                return (
                  <Pressable
                    key={item.id}
                    onPress={() => pick(item)}
                    disabled={soldOut}
                    accessibilityRole="button"
                    accessibilityLabel={soldOut ? `${item.name}, sold out` : `Add ${item.name}`}
                    style={({ pressed }) => [styles.tile, inCart ? styles.tilePicked : null, soldOut ? styles.tileSoldOut : null, pressed ? styles.tilePicked : null]}
                  >
                    <Text style={[styles.tileName, soldOut ? { color: colors.textMuted } : null]} numberOfLines={2}>
                      {item.name}
                    </Text>
                    <View style={styles.tileFoot}>
                      <Text style={styles.tilePrice}>
                        {soldOut
                          ? 'Sold out'
                          : `${item.variants.length ? 'from ' : ''}${formatPaiseToRupees(price)}${item.unit !== 'pcs' ? `/${item.unit}` : ''}`}
                      </Text>
                      {inCart ? <Text style={styles.tileCount}>{weighed ? '✓' : inCart}</Text> : null}
                    </View>
                  </Pressable>
                );
              })}
            </ScrollView>
          </QueryState>
        </View>

        {wide ? (
          <View style={styles.side}>{cartPanel}</View>
        ) : count ? (
          <Pressable onPress={() => setCartOpen(true)} style={styles.cartBar} accessibilityRole="button" accessibilityLabel={`View sale, ${count} lines, ${formatPaiseToRupees(total)}`}>
            <Text style={styles.cartBarText}>{count} {count === 1 ? 'line' : 'lines'}</Text>
            <Text style={styles.cartBarTotal}>{formatPaiseToRupees(total)}</Text>
            <Text style={styles.cartBarText}>View and bill</Text>
          </Pressable>
        ) : null}
      </View>

      {!wide ? (
        <Modal visible={cartOpen} onClose={() => setCartOpen(false)} title="This sale">
          <View style={{ minHeight: 280 }}>{cartPanel}</View>
        </Modal>
      ) : null}

      <ItemOptionsSheet
        item={optionsItem}
        onClose={() => setOptionsItem(null)}
        onAdd={(line) => {
          add(CART, line);
          changed();
          setOptionsItem(null);
        }}
      />
      <WeightSheet
        item={weighItem}
        onClose={() => setWeighItem(null)}
        onAdd={(quantity) => {
          if (weighItem) {
            // Every weighing is its own line: two bags of onions are two weights, not one.
            add(CART, { item_id: weighItem.id, name: weighItem.name, modifier_ids: [], modifier_names: [], unit_price_paise: weighItem.base_price_paise, quantity, notes: undefined }, { merge: false });
            changed();
          }
          setWeighItem(null);
          searchRef.current?.focus();
        }}
      />
    </View>
  );
};

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  topActions: { flexDirection: 'row', gap: spacing.xs, marginRight: spacing.sm },
  alert: {
    flexDirection: 'row', alignItems: 'center', gap: spacing.sm, marginHorizontal: spacing.lg, marginTop: spacing.md,
    padding: spacing.md, borderRadius: radii.md, backgroundColor: colors.dangerBg,
  },
  alertText: { ...typography.caption, color: colors.danger, fontFamily: fonts.semibold, flex: 1 },
  body: { flex: 1 },
  items: { flex: 1, padding: spacing.lg },
  searchBox: {
    flexDirection: 'row', alignItems: 'center', gap: spacing.sm, minHeight: 48, paddingHorizontal: spacing.md,
    borderRadius: radii.md, borderWidth: 1, borderColor: colors.surfaceBorder, backgroundColor: colors.surface,
  },
  searchInput: { flex: 1, ...typography.body, color: colors.text, paddingVertical: spacing.sm, outlineStyle: 'none' } as never,
  message: { ...typography.caption, color: colors.textSecondary, marginTop: spacing.sm },
  tabsRow: { flexGrow: 0, marginTop: spacing.md },
  tab: {
    minHeight: 40, paddingHorizontal: spacing.lg, borderRadius: radii.full, borderWidth: 1, borderColor: colors.surfaceBorder,
    backgroundColor: colors.surface, justifyContent: 'center',
  },
  tabActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  tabText: { ...typography.caption, color: colors.textSecondary, fontFamily: fonts.semibold },
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, paddingTop: spacing.md, paddingBottom: spacing.xl },
  tile: {
    flexBasis: 140, flexGrow: 1, maxWidth: 200, minHeight: 84, padding: spacing.md, borderRadius: radii.lg,
    backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.surfaceBorder, justifyContent: 'space-between',
  },
  tilePicked: { borderColor: colors.primary, backgroundColor: colors.primaryGlow },
  tileSoldOut: { backgroundColor: colors.surfaceLight },
  tileName: { ...typography.bodyBold, color: colors.text },
  tileFoot: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginTop: spacing.xs },
  tilePrice: { ...typography.caption, color: colors.textSecondary },
  tileCount: { ...typography.caption, color: colors.primary, fontFamily: fonts.bold },
  side: { width: 380, borderLeftWidth: 1, borderLeftColor: colors.surfaceBorder, backgroundColor: colors.surface },
  cart: { flex: 1, padding: spacing.lg, gap: spacing.md },
  empty: { ...typography.body, color: colors.textMuted, textAlign: 'center', marginTop: spacing.xl },
  cartLine: {
    flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingVertical: spacing.sm,
    borderBottomWidth: 1, borderBottomColor: colors.surfaceBorder,
  },
  cartName: { ...typography.bodyBold, color: colors.text },
  cartMeta: { ...typography.caption, color: colors.textSecondary },
  stepper: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs },
  qty: { ...typography.bodyBold, minWidth: 24, textAlign: 'center', color: colors.text },
  lineTotal: { ...typography.bodyBold, minWidth: 72, textAlign: 'right', color: colors.text },
  cartFoot: { gap: spacing.md, paddingTop: spacing.sm },
  totalRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline' },
  totalLabel: { ...typography.caption, color: colors.textSecondary },
  total: { ...typography.h2, fontFamily: fonts.bold, color: colors.text },
  cartBar: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', margin: spacing.md, padding: spacing.lg,
    borderRadius: radii.lg, backgroundColor: colors.primary,
  },
  cartBarText: { ...typography.bodyBold, color: colors.textInverse },
  cartBarTotal: { ...typography.h3, fontFamily: fonts.bold, color: colors.textInverse },
});
