import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  type TextInputInstance,
  useWindowDimensions,
  View,
} from 'react-native';
import { Button, colors, fonts, Input, Modal, radii, spacing, typography } from '@repo/shared-ui';
import { formatPaiseToRupees, StaffRole } from '@repo/shared-utils';
import {
  ApiError,
  apparelApi,
  ApparelProduct,
  ApparelVariant,
  newIdempotencyKey,
  toPaise,
  toRupees,
  useApparelSearch,
  useCreateApparelSale,
} from '@repo/api-client';
import { CashDayBanner, cashButtonLabel, useCounterCashDay } from '../components/CashDayBanner';
import { Icon } from '../components/Icon';
import { StepButton } from '../components/ItemOptionsSheet';
import { QueryState } from '../components/QueryState';
import { TopBar } from '../components/TopBar';
import { useDeviceStore } from '../stores/deviceStore';
import { useSessionStore } from '../stores/sessionStore';
import { ApparelCartLine, cartTotals, hasDiscount, lineNet, useApparelCartStore } from './apparelCartStore';
import { VariantPickerSheet } from './VariantPickerSheet';

const money = (paise: number) => formatPaiseToRupees(paise);

export interface SaleScreenProps {
  /** Store credit from an exchange, ready to pay for this sale. */
  credit?: { code: string; amount_paise: number } | null;
  onDropCredit: () => void;
  /** The invoice was made: take it to payment. */
  onSale: (saleId: string) => void;
  onReturns: () => void;
  /** Today's report: shown to managers and owners only. */
  onToday: () => void;
  onPrinter: () => void;
  onDayEnd: () => void;
}

type DiscountTarget = { kind: 'line'; line: ApparelCartLine } | { kind: 'bill' };

/**
 * The dress shop counter, scan-first. A barcode (typed, or sent by a keyboard scanner followed by Enter) adds that exact size
 * and colour. Typing a name lists styles; tapping one opens the size and colour grid. "Make invoice" sends the whole cart; the
 * totals shown here are an estimate and the server's invoice is what is paid.
 */
export const SaleScreen: React.FC<SaleScreenProps> = ({ credit, onDropCredit, onSale, onReturns, onToday, onPrinter, onDayEnd }) => {
  const role = useSessionStore((s) => s.user?.role);
  const device = useDeviceStore((s) => s.device)!;
  const cashDay = useCounterCashDay();
  const createSale = useCreateApparelSale();
  const { lines, billDiscountPaise, discountReason, add, setQuantity, setLineDiscount, setBillDiscount, setDiscountReason, clear } =
    useApparelCartStore();
  const wide = useWindowDimensions().width >= 900;

  const [search, setSearch] = useState('');
  const [term, setTerm] = useState('');
  const [picking, setPicking] = useState<ApparelProduct | null>(null);
  const [cartOpen, setCartOpen] = useState(false);
  const [discountTarget, setDiscountTarget] = useState<DiscountTarget | null>(null);
  const [discountText, setDiscountText] = useState('');
  const [pinOpen, setPinOpen] = useState(false);
  const [pin, setPin] = useState('');
  const [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);
  const [saleKey, setSaleKey] = useState(newIdempotencyKey);
  const searchRef = useRef<TextInputInstance>(null);

  // The list follows typing after a short pause, so each keystroke is not a request.
  useEffect(() => {
    const t = setTimeout(() => setTerm(search.trim()), 250);
    return () => clearTimeout(t);
  }, [search]);
  const products = useApparelSearch({ q: term || undefined, branch_id: device.branchId });

  const totals = cartTotals(lines, billDiscountPaise);
  const inCart = useMemo(() => Object.fromEntries(lines.map((l) => [l.variant_id, l.quantity])), [lines]);
  const discounted = hasDiscount(lines, billDiscountPaise);

  const changed = () => {
    setSaleKey(newIdempotencyKey());
    setMessage(null);
  };

  const addVariant = (product: Pick<ApparelProduct, 'name'>, variant: ApparelVariant) => {
    add({
      variant_id: variant.id, name: product.name, size: variant.size, colour: variant.colour, mrp_paise: variant.mrp_paise,
      price_paise: variant.price_paise, on_hand: variant.on_hand,
    });
    changed();
  };

  /** Enter in the search box: what a keyboard-style barcode scanner sends after the code. */
  const submitSearch = async () => {
    const code = search.trim();
    if (!code) return;
    if (/\s/.test(code)) return; // a name, not a code: the list is already showing
    try {
      const found = await apparelApi.lookup(code, device.branchId);
      addVariant(found.product, found.variant);
      setSearch('');
      setTerm('');
      setMessage({ text: `Added ${found.product.name}, ${found.variant.size} / ${found.variant.colour}.` });
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        // Not a barcode or SKU: if the name matches one style, open it.
        const list = products.data ?? [];
        if (list.length === 1) {
          setPicking(list[0]);
          setSearch('');
        } else if (list.length === 0) setMessage({ text: `Nothing matches ${code}.`, error: true });
      } else setMessage({ text: 'Could not look up the code.', error: true });
    } finally {
      searchRef.current?.focus();
    }
  };

  const openDiscount = (target: DiscountTarget) => {
    setDiscountTarget(target);
    const current = target.kind === 'bill' ? billDiscountPaise : target.line.line_discount_paise;
    setDiscountText(current ? toRupees(current) : '');
  };

  const applyDiscount = () => {
    if (!discountTarget) return;
    const paise = toPaise(discountText || '0');
    if (discountTarget.kind === 'bill') setBillDiscount(paise);
    else setLineDiscount(discountTarget.line.variant_id, Math.min(paise, discountTarget.line.price_paise * discountTarget.line.quantity));
    setDiscountTarget(null);
    changed();
  };

  const makeInvoice = async (approverPin?: string) => {
    if (!lines.length) return;
    if (discounted && !discountReason.trim()) {
      setMessage({ text: 'Enter the reason for the discount first.', error: true });
      setCartOpen(true);
      return;
    }
    setMessage(null);
    const key = approverPin ? newIdempotencyKey() : saleKey;
    try {
      const sale = await createSale.mutateAsync({
        idempotencyKey: key,
        input: {
          branch_id: device.branchId,
          lines: lines.map((l) => ({ variant_id: l.variant_id, quantity: l.quantity, line_discount_paise: l.line_discount_paise })),
          bill_discount_paise: billDiscountPaise,
          discount_reason: discounted ? discountReason : undefined,
          client_sale_id: key,
          approver_pin: approverPin,
        },
      });
      clear();
      setSaleKey(newIdempotencyKey());
      setCartOpen(false);
      setPinOpen(false);
      setPin('');
      onSale(sale.id);
    } catch (e) {
      if (e instanceof ApiError && e.code === 'approval_required') {
        setPinOpen(true);
        setMessage({ text: approverPin ? 'That PIN was not accepted. Ask a manager.' : 'A manager must approve this discount.', error: true });
        setPin('');
        return;
      }
      setPinOpen(false);
      setMessage({ text: e instanceof ApiError ? e.message : 'Could not make the invoice. Try again.', error: true });
    }
  };

  const cartPanel = (
    <View style={styles.cart}>
      <ScrollView contentContainerStyle={{ gap: spacing.sm }}>
        {lines.length === 0 ? <Text style={styles.empty}>Scan a tag or pick a style to start the sale.</Text> : null}
        {lines.map((line) => {
          const saving = line.mrp_paise && line.mrp_paise > line.price_paise;
          return (
            <View key={line.variant_id} style={styles.cartLine}>
              <View style={{ flex: 1 }}>
                <Text style={styles.cartName} numberOfLines={2}>{line.name}</Text>
                <Text style={styles.cartMeta}>
                  {line.size} · {line.colour}
                  {'  '}
                  {saving ? <Text style={styles.struck}>{money(line.mrp_paise!)}</Text> : null}
                  {saving ? '  ' : ''}
                  {money(line.price_paise)}
                </Text>
                <Pressable onPress={() => openDiscount({ kind: 'line', line })} accessibilityRole="button" accessibilityLabel={`Discount on ${line.name}`} hitSlop={6}>
                  <Text style={styles.link}>{line.line_discount_paise ? `Discount −${money(line.line_discount_paise)}` : 'Add discount'}</Text>
                </Pressable>
              </View>
              <View style={styles.stepper}>
                <StepButton icon="minus" label={`One less ${line.name}`} size={36} onPress={() => { setQuantity(line.variant_id, line.quantity - 1); changed(); }} />
                <Text style={styles.qty}>{line.quantity}</Text>
                <StepButton icon="plus" label={`One more ${line.name}`} size={36} onPress={() => { setQuantity(line.variant_id, line.quantity + 1); changed(); }} />
              </View>
              <Text style={styles.lineTotal}>{money(lineNet(line))}</Text>
            </View>
          );
        })}
      </ScrollView>
      <View style={styles.cartFoot}>
        {lines.length ? (
          <>
            <Pressable onPress={() => openDiscount({ kind: 'bill' })} accessibilityRole="button" accessibilityLabel="Discount on the whole bill">
              <Text style={styles.link}>{billDiscountPaise ? `Bill discount −${money(billDiscountPaise)}` : 'Add a discount on the whole bill'}</Text>
            </Pressable>
            {discounted ? (
              <Input label="Reason for the discount" value={discountReason} onChangeText={(v) => { setDiscountReason(v); changed(); }} placeholder="e.g. Festival offer" />
            ) : null}
          </>
        ) : null}
        <View style={styles.totalRow}>
          <Text style={styles.totalLabel}>{totals.pieces} {totals.pieces === 1 ? 'piece' : 'pieces'} · GST included</Text>
          <Text style={styles.total}>{money(totals.total)}</Text>
        </View>
        {message ? <Text style={[styles.message, message.error ? { color: colors.danger } : null]}>{message.text}</Text> : null}
        <View style={{ flexDirection: 'row', gap: spacing.sm }}>
          {lines.length ? <Button title="Clear" variant="secondary" onPress={() => { clear(); changed(); }} /> : null}
          <Button title="Make invoice" onPress={() => makeInvoice()} disabled={!lines.length} loading={createSale.isPending} style={{ flex: 1 }} />
        </View>
      </View>
    </View>
  );

  const list = products.data ?? [];

  return (
    <View style={styles.container}>
      <TopBar
        title="Dress shop"
        subtitle={device.branchName}
        right={
          <View style={styles.topActions}>
            <Button title="Returns" variant="secondary" size="sm" onPress={onReturns} />
            {role === StaffRole.OWNER || role === StaffRole.MANAGER ? <Button title="Today" variant="secondary" size="sm" onPress={onToday} /> : null}
            {wide ? <Button title="Printer" variant="secondary" size="sm" onPress={onPrinter} icon={<Icon name="printer" size={14} color={colors.text} />} /> : null}
            <Button title={cashButtonLabel(cashDay?.status)} variant="secondary" size="sm" onPress={onDayEnd} />
          </View>
        }
      />
      <CashDayBanner onPress={onDayEnd} />
      {credit ? (
        <Pressable onPress={onDropCredit} accessibilityRole="button" accessibilityLabel="Forget the store credit for this sale" style={styles.creditBar}>
          <Text style={styles.creditText}>
            Exchange: store credit {money(credit.amount_paise)} ({credit.code}) will be offered at payment. Tap to drop it.
          </Text>
        </Pressable>
      ) : null}

      <View style={[styles.body, wide ? { flexDirection: 'row' } : null]}>
        <View style={styles.items}>
          <View style={styles.searchBox}>
            <Icon name="maximize" size={18} color={colors.textSecondary} />
            <TextInput
              ref={searchRef}
              value={search}
              onChangeText={(v) => { setSearch(v); setMessage(null); }}
              onSubmitEditing={submitSearch}
              placeholder="Scan a tag, or type a style or code"
              placeholderTextColor={colors.textMuted}
              autoFocus={Platform.OS === 'web'}
              autoCorrect={false}
              autoCapitalize="none"
              blurOnSubmit={false}
              returnKeyType="search"
              accessibilityLabel="Scan a tag, or type a style or code"
              style={styles.searchInput}
            />
            {search ? (
              <Pressable onPress={() => setSearch('')} accessibilityRole="button" accessibilityLabel="Clear search" hitSlop={8}>
                <Icon name="x" size={18} color={colors.textSecondary} />
              </Pressable>
            ) : null}
          </View>
          {!wide && message ? <Text style={[styles.message, message.error ? { color: colors.danger } : null]}>{message.text}</Text> : null}

          <QueryState query={products} isEmpty={list.length === 0} emptyText={term ? 'No styles match.' : 'No styles yet. Add them in the admin app.'}>
            <ScrollView contentContainerStyle={styles.grid} keyboardShouldPersistTaps="handled">
              {list.map((product) => {
                const active = product.variants.filter((v) => v.is_active);
                const from = active.length ? Math.min(...active.map((v) => v.price_paise)) : 0;
                const left = active.reduce((n, v) => n + Math.max(0, v.on_hand ?? 0), 0);
                const picked = active.reduce((n, v) => n + (inCart[v.id] ?? 0), 0);
                return (
                  <Pressable
                    key={product.id}
                    onPress={() => setPicking(product)}
                    accessibilityRole="button"
                    accessibilityLabel={`${product.name}, from ${money(from)}, ${left} in stock`}
                    style={({ pressed }) => [styles.tile, picked ? styles.tilePicked : null, pressed ? styles.tilePicked : null]}
                  >
                    <Text style={styles.tileName} numberOfLines={2}>{product.name}</Text>
                    {product.brand ? <Text style={styles.tileBrand} numberOfLines={1}>{product.brand}</Text> : null}
                    <View style={styles.tileFoot}>
                      <Text style={styles.tilePrice}>{active.length > 1 ? 'from ' : ''}{money(from)}</Text>
                      {picked ? <Text style={styles.tileCount}>{picked}</Text> : null}
                    </View>
                    <Text style={[styles.tileStock, left <= 0 ? { color: colors.danger } : null]}>{left <= 0 ? 'Out of stock' : `${left} in stock`}</Text>
                  </Pressable>
                );
              })}
            </ScrollView>
          </QueryState>
        </View>

        {wide ? (
          <View style={styles.side}>{cartPanel}</View>
        ) : lines.length ? (
          <Pressable onPress={() => setCartOpen(true)} style={styles.cartBar} accessibilityRole="button" accessibilityLabel={`View sale, ${totals.pieces} pieces, ${money(totals.total)}`}>
            <Text style={styles.cartBarText}>{totals.pieces} {totals.pieces === 1 ? 'piece' : 'pieces'}</Text>
            <Text style={styles.cartBarTotal}>{money(totals.total)}</Text>
            <Text style={styles.cartBarText}>View and bill</Text>
          </Pressable>
        ) : null}
      </View>

      {!wide ? (
        <Modal visible={cartOpen} onClose={() => setCartOpen(false)} title="This sale">
          <View style={{ minHeight: 320 }}>{cartPanel}</View>
        </Modal>
      ) : null}

      <VariantPickerSheet
        product={picking}
        inCart={inCart}
        onClose={() => { setPicking(null); searchRef.current?.focus(); }}
        onPick={(product, variant) => addVariant(product, variant)}
      />

      <Modal
        visible={!!discountTarget}
        onClose={() => setDiscountTarget(null)}
        title={discountTarget?.kind === 'bill' ? 'Discount on the whole bill' : `Discount on ${discountTarget?.kind === 'line' ? discountTarget.line.name : ''}`}
        footer={<Button title="Apply" onPress={applyDiscount} />}
      >
        <Input label="Amount off (₹)" value={discountText} onChangeText={setDiscountText} keyboardType="decimal-pad" prefix="₹" autoFocus />
        <Text style={styles.hint}>Enter 0 to remove it. A big discount needs a manager when the invoice is made.</Text>
      </Modal>

      <Modal
        visible={pinOpen}
        onClose={() => setPinOpen(false)}
        title="Manager approval"
        subtitle="This discount needs a manager's PIN."
        footer={<Button title="Approve and make invoice" onPress={() => makeInvoice(pin)} disabled={pin.length < 4} loading={createSale.isPending} />}
      >
        <Input label="Manager PIN" value={pin} onChangeText={(v) => setPin(v.replace(/\D/g, ''))} secureTextEntry keyboardType="number-pad" maxLength={6} autoFocus />
        {message?.error ? <Text style={[styles.message, { color: colors.danger }]}>{message.text}</Text> : null}
      </Modal>
    </View>
  );
};

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  topActions: { flexDirection: 'row', gap: spacing.xs, marginRight: spacing.sm },
  creditBar: { marginHorizontal: spacing.lg, marginTop: spacing.md, padding: spacing.md, borderRadius: radii.md, backgroundColor: colors.primaryGlow },
  creditText: { ...typography.caption, color: colors.primary, fontFamily: fonts.semibold },
  body: { flex: 1 },
  items: { flex: 1, padding: spacing.lg },
  searchBox: {
    flexDirection: 'row', alignItems: 'center', gap: spacing.sm, minHeight: 48, paddingHorizontal: spacing.md,
    borderRadius: radii.md, borderWidth: 1, borderColor: colors.surfaceBorder, backgroundColor: colors.surface,
  },
  searchInput: { flex: 1, ...typography.body, color: colors.text, paddingVertical: spacing.sm, outlineStyle: 'none' } as never,
  message: { ...typography.caption, color: colors.textSecondary, marginTop: spacing.sm },
  hint: { ...typography.caption, color: colors.textMuted, marginTop: spacing.sm },
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, paddingTop: spacing.md, paddingBottom: spacing.xl },
  tile: {
    flexBasis: 160, flexGrow: 1, maxWidth: 220, minHeight: 104, padding: spacing.md, borderRadius: radii.lg,
    backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.surfaceBorder, justifyContent: 'space-between',
  },
  tilePicked: { borderColor: colors.primary, backgroundColor: colors.primaryGlow },
  tileName: { ...typography.bodyBold, color: colors.text },
  tileBrand: { ...typography.caption, color: colors.textMuted },
  tileFoot: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginTop: spacing.xs },
  tilePrice: { ...typography.caption, color: colors.textSecondary },
  tileStock: { ...typography.label, color: colors.textMuted, marginTop: 2 },
  tileCount: { ...typography.caption, color: colors.primary, fontFamily: fonts.bold },
  side: { width: 400, borderLeftWidth: 1, borderLeftColor: colors.surfaceBorder, backgroundColor: colors.surface },
  cart: { flex: 1, padding: spacing.lg, gap: spacing.md },
  empty: { ...typography.body, color: colors.textMuted, textAlign: 'center', marginTop: spacing.xl },
  cartLine: {
    flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingVertical: spacing.sm,
    borderBottomWidth: 1, borderBottomColor: colors.surfaceBorder,
  },
  cartName: { ...typography.bodyBold, color: colors.text },
  cartMeta: { ...typography.caption, color: colors.textSecondary },
  struck: { textDecorationLine: 'line-through', color: colors.textMuted },
  link: { ...typography.caption, color: colors.primary, fontFamily: fonts.semibold, marginTop: 2 },
  stepper: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs },
  qty: { ...typography.bodyBold, minWidth: 24, textAlign: 'center', color: colors.text },
  lineTotal: { ...typography.bodyBold, minWidth: 72, textAlign: 'right', color: colors.text },
  cartFoot: { gap: spacing.sm, paddingTop: spacing.sm },
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
