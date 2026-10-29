import React, { useMemo, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, TouchableOpacity, useWindowDimensions, View } from 'react-native';
import { Badge, BadgeProps, Button, colors, fonts, humanizeLabel, radii, spacing, typography } from '@repo/shared-ui';
import { formatPaiseToRupees } from '@repo/shared-utils';
import {
  ApiError,
  CartLine,
  KitchenTicket,
  MenuItem,
  OrderLine,
  useCancelOrderLine,
  useFloorLayout,
  useOrder,
  useOrderTickets,
  useRequestVoid,
  useSendToKitchen,
  useTransferTable,
} from '@repo/api-client';
import { ItemOptionsSheet, StepButton } from '../components/ItemOptionsSheet';
import { MenuPanel } from '../components/MenuPanel';
import { NoteSheet, TransferSheet, VoidRequestSheet } from '../components/Sheets';
import { QueryState } from '../components/QueryState';
import { TopBar } from '../components/TopBar';
import { Icon } from '../components/Icon';
import { useCartStore } from '../stores/cartStore';
import { useDeviceStore } from '../stores/deviceStore';

const EMPTY_CART: CartLine[] = [];
const errorText = (e: unknown) => (e instanceof ApiError ? e.message : 'Something went wrong. Try again.');
const money = (paise: number) => formatPaiseToRupees(paise, { decimals: 0 });

const TICKET_BADGE: Record<KitchenTicket['status'], BadgeProps['variant']> = {
  queued: 'neutral',
  preparing: 'warning',
  ready: 'available',
  served: 'primary',
  voided: 'danger',
};

export interface OrderScreenProps {
  orderId: string;
  title: string;
  onBack: () => void;
}

export const OrderScreen: React.FC<OrderScreenProps> = ({ orderId, title, onBack }) => {
  const { width } = useWindowDimensions();
  const wide = width >= 900;
  const device = useDeviceStore((s) => s.device)!;

  const orderQuery = useOrder(orderId);
  const ticketsQuery = useOrderTickets(orderId);
  const floorQuery = useFloorLayout(device.branchId);
  const order = orderQuery.data;
  const tickets = ticketsQuery.data ?? [];

  const cart = useCartStore((s) => s.carts[orderId] ?? EMPTY_CART);
  const { add, setQuantity, setNotes, clear, markVoidRequested } = useCartStore();
  const voidRequested = useCartStore((s) => s.voidRequested);

  const send = useSendToKitchen();
  const cancelLine = useCancelOrderLine();
  const requestVoid = useRequestVoid();
  const transfer = useTransferTable();

  const [tab, setTab] = useState<'menu' | 'order'>('menu');
  const [optionsItem, setOptionsItem] = useState<MenuItem | null>(null);
  const [noteFor, setNoteFor] = useState<CartLine | null>(null);
  const [voidTicket, setVoidTicket] = useState<KitchenTicket | null>(null);
  const [transferOpen, setTransferOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sheetError, setSheetError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Lines saved on the order but not yet fired (e.g. a send that failed half way).
  const unsentLines = (order?.lines ?? []).filter((l) => l.status === 'ordered');
  const cartTotal = cart.reduce((n, c) => n + c.unit_price_paise * c.quantity, 0);
  const cartCount = cart.reduce((n, c) => n + c.quantity, 0) + unsentLines.reduce((n, l) => n + l.quantity, 0);
  const sentTickets = useMemo(
    () => tickets.slice().sort((a, b) => b.created_at.localeCompare(a.created_at)),
    [tickets],
  );
  const lineById = useMemo(() => new Map((order?.lines ?? []).map((l) => [l.id, l])), [order]);
  const editable = order?.status === 'active';

  const pick = (item: MenuItem) => {
    setNotice(null);
    if (item.variants.length > 0 || (item.modifier_groups ?? []).length > 0) {
      setOptionsItem(item);
      return;
    }
    add(orderId, {
      item_id: item.id,
      name: item.name,
      modifier_ids: [],
      modifier_names: [],
      unit_price_paise: item.base_price_paise,
      quantity: 1,
    });
  };

  const sendToKitchen = async () => {
    if (!order || cartCount === 0) return;
    setError(null);
    try {
      const fired = await send.mutateAsync({ orderId, version: order.version, cart });
      clear(orderId);
      setNotice(`Sent to kitchen: ${fired.map((t) => `KOT ${t.kot_number.split('-').pop()}`).join(', ')}`);
      if (!wide) setTab('order');
    } catch (e) {
      setError(errorText(e));
    }
  };

  const removeUnsent = async (line: OrderLine) => {
    setError(null);
    try {
      await cancelLine.mutateAsync({ orderId, lineId: line.id, reason: 'Removed before sending' });
    } catch (e) {
      setError(errorText(e));
    }
  };

  const submitVoid = async (reason: string) => {
    if (!voidTicket) return;
    setSheetError(null);
    try {
      await requestVoid.mutateAsync({ kotId: voidTicket.id, reason, orderId });
      markVoidRequested(voidTicket.id);
      setNotice(`Void request sent for KOT ${voidTicket.kot_number.split('-').pop()}`);
      setVoidTicket(null);
    } catch (e) {
      setSheetError(errorText(e));
    }
  };

  const submitTransfer = async (targetTableId: string) => {
    if (!order?.table_id) return;
    setSheetError(null);
    try {
      await transfer.mutateAsync({ sourceTableId: order.table_id, targetTableId, orderId });
      setTransferOpen(false);
      onBack();
    } catch (e) {
      setSheetError(errorText(e));
    }
  };

  const allTables = (floorQuery.data ?? []).flatMap((f) => f.tables);
  const subtitle = order
    ? [`#${order.order_number.split('-').pop()}`, order.order_type === 'dine_in' ? `${order.guest_count} guests` : null, humanizeLabel(order.status.toUpperCase())]
        .filter(Boolean)
        .join(' · ')
    : undefined;

  const orderPanel = (
    <View style={styles.orderPanel}>
      <ScrollView contentContainerStyle={styles.orderScroll}>
        {error ? <Text style={styles.error}>{error}</Text> : null}
        {notice ? <Text style={styles.notice}>{notice}</Text> : null}

        {/* Not yet sent */}
        <Text style={styles.sectionTitle}>New items</Text>
        {cart.length === 0 && unsentLines.length === 0 ? (
          <Text style={styles.empty}>Tap menu items to add them.</Text>
        ) : null}

        {unsentLines.map((line) => (
          <View key={line.id} style={styles.cartRow}>
            <View style={{ flex: 1 }}>
              <Text style={styles.lineName}>
                {line.quantity} × {line.name}
                {line.variant_name ? ` (${line.variant_name})` : ''}
              </Text>
              <Text style={styles.lineMeta}>Saved, not sent yet</Text>
            </View>
            <Pressable onPress={() => removeUnsent(line)} hitSlop={8} accessibilityRole="button" accessibilityLabel={`Remove ${line.name}`}>
              <Icon name="trash-2" size={18} color={colors.danger} />
            </Pressable>
          </View>
        ))}

        {cart.map((line) => (
          <View key={line.key} style={styles.cartRow}>
            <View style={{ flex: 1 }}>
              <Text style={styles.lineName}>
                {line.name}
                {line.variant_name ? ` (${line.variant_name})` : ''}
              </Text>
              {line.modifier_names.length ? <Text style={styles.lineMeta}>{line.modifier_names.join(', ')}</Text> : null}
              <Pressable onPress={() => setNoteFor(line)} hitSlop={6} accessibilityRole="button">
                <Text style={line.notes ? styles.lineNote : styles.addNote}>{line.notes ? `Note: ${line.notes}` : 'Add note'}</Text>
              </Pressable>
            </View>
            <View style={styles.qtyRow}>
              <StepButton icon="minus" label={`One less ${line.name}`} size={36} onPress={() => setQuantity(orderId, line.key, line.quantity - 1)} />
              <Text style={styles.qty}>{line.quantity}</Text>
              <StepButton icon="plus" label={`One more ${line.name}`} size={36} onPress={() => setQuantity(orderId, line.key, line.quantity + 1)} />
            </View>
            <Text style={styles.lineTotal}>{money(line.unit_price_paise * line.quantity)}</Text>
          </View>
        ))}

        {/* Sent */}
        <Text style={[styles.sectionTitle, { marginTop: spacing.xl }]}>Sent to kitchen</Text>
        <QueryState query={ticketsQuery} isEmpty={sentTickets.length === 0} emptyText="Nothing sent yet.">
          {sentTickets.map((ticket) => {
            const voidable = editable && (ticket.status === 'queued' || ticket.status === 'preparing');
            return (
              <View key={ticket.id} style={[styles.ticket, ticket.status === 'voided' ? { opacity: 0.55 } : null]}>
                <View style={styles.ticketHead}>
                  <Text style={styles.ticketTitle}>
                    KOT {ticket.kot_number.split('-').pop()} · {humanizeLabel(ticket.station)}
                  </Text>
                  <Badge label={humanizeLabel(ticket.status.toUpperCase())} variant={TICKET_BADGE[ticket.status]} size="sm" />
                </View>
                {ticket.items.map((item) => {
                  const line = lineById.get(item.order_item_id);
                  return (
                    <View key={item.order_item_id} style={styles.ticketLine}>
                      <View style={{ flex: 1 }}>
                        <Text style={[styles.ticketItem, ticket.status === 'voided' ? styles.struck : null]}>
                          {item.quantity} × {item.name}
                        </Text>
                        {item.notes ? <Text style={styles.lineMeta}>{item.notes}</Text> : null}
                      </View>
                      {line ? <Text style={styles.ticketPrice}>{money(line.line_total_paise)}</Text> : null}
                    </View>
                  );
                })}
                {voidable && voidRequested[ticket.id] ? (
                  <Text style={styles.voidRequested}>Void requested · waiting for manager</Text>
                ) : voidable ? (
                  <Button
                    title="Request void"
                    variant="ghost"
                    size="sm"
                    onPress={() => {
                      setSheetError(null);
                      setVoidTicket(ticket);
                    }}
                    textStyle={{ color: colors.danger }}
                    style={{ alignSelf: 'flex-start', marginTop: spacing.xs, paddingHorizontal: 0 }}
                  />
                ) : null}
              </View>
            );
          })}
        </QueryState>
      </ScrollView>

      <View style={styles.footer}>
        <View style={styles.totals}>
          <Text style={styles.totalLabel}>Order total</Text>
          <Text style={styles.totalValue}>{money((order?.subtotal_paise ?? 0) + cartTotal)}</Text>
        </View>
        <View style={styles.footerActions}>
          {order?.order_type === 'dine_in' && editable ? (
            <Button
              title="Transfer"
              variant="secondary"
              onPress={() => {
                setSheetError(null);
                setTransferOpen(true);
              }}
              icon={<Icon name="shuffle" size={16} color={colors.text} />}
            />
          ) : null}
          <Button
            title={cartCount ? `Send to kitchen · ${cartCount}` : 'Send to kitchen'}
            onPress={sendToKitchen}
            disabled={!editable || cartCount === 0}
            loading={send.isPending}
            style={{ flex: 1 }}
            size="lg"
          />
        </View>
        {!editable && order ? <Text style={styles.lockedNote}>This order is {order.status}; it can no longer be changed.</Text> : null}
      </View>
    </View>
  );

  return (
    <View style={styles.container}>
      <TopBar title={title} subtitle={subtitle} onBack={onBack} />

      <QueryState query={orderQuery}>
        {wide ? (
          <View style={styles.split}>
            <View style={styles.menuSide}>
              <MenuPanel onPick={pick} />
            </View>
            {orderPanel}
          </View>
        ) : (
          <View style={{ flex: 1 }}>
            <View style={styles.segment}>
              {(['menu', 'order'] as const).map((key) => (
                <TouchableOpacity
                  key={key}
                  onPress={() => setTab(key)}
                  style={[styles.segmentBtn, tab === key ? styles.segmentActive : null]}
                  accessibilityRole="tab"
                  accessibilityState={{ selected: tab === key }}
                >
                  <Text style={[styles.segmentText, tab === key ? styles.segmentTextActive : null]}>
                    {key === 'menu' ? 'Menu' : cartCount ? `Order (${cartCount} new)` : 'Order'}
                  </Text>
                </TouchableOpacity>
              ))}
            </View>
            {tab === 'menu' ? <MenuPanel onPick={pick} /> : orderPanel}
          </View>
        )}
      </QueryState>

      <ItemOptionsSheet item={optionsItem} onClose={() => setOptionsItem(null)} onAdd={(line) => add(orderId, line)} />
      <NoteSheet
        visible={noteFor !== null}
        itemName={noteFor?.name ?? ''}
        initial={noteFor?.notes}
        onClose={() => setNoteFor(null)}
        onSave={(notes) => {
          if (noteFor) setNotes(orderId, noteFor.key, notes);
          setNoteFor(null);
        }}
      />
      <VoidRequestSheet
        ticket={voidTicket}
        loading={requestVoid.isPending}
        error={sheetError}
        onClose={() => setVoidTicket(null)}
        onSubmit={submitVoid}
      />
      <TransferSheet
        visible={transferOpen}
        fromLabel={title}
        tables={allTables}
        loading={transfer.isPending}
        error={sheetError}
        onClose={() => setTransferOpen(false)}
        onSubmit={submitTransfer}
      />
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.background,
  },
  split: {
    flex: 1,
    flexDirection: 'row',
  },
  menuSide: {
    flex: 3,
    borderRightWidth: StyleSheet.hairlineWidth,
    borderRightColor: colors.surfaceBorder,
  },
  orderPanel: {
    flex: 2,
    minWidth: 340,
    backgroundColor: colors.surface,
  },
  orderScroll: {
    padding: spacing.lg,
    paddingBottom: spacing.xl,
  },
  segment: {
    flexDirection: 'row',
    margin: spacing.lg,
    marginBottom: 0,
    padding: 3,
    borderRadius: radii.md,
    backgroundColor: colors.surfaceLight,
  },
  segmentBtn: {
    flex: 1,
    minHeight: 40,
    borderRadius: radii.sm,
    alignItems: 'center',
    justifyContent: 'center',
  },
  segmentActive: {
    backgroundColor: colors.surface,
  },
  segmentText: {
    ...typography.caption,
    color: colors.textSecondary,
    fontFamily: fonts.semibold,
  },
  segmentTextActive: {
    color: colors.text,
  },
  error: {
    ...typography.caption,
    color: colors.danger,
    backgroundColor: colors.dangerBg,
    padding: spacing.md,
    borderRadius: radii.md,
    marginBottom: spacing.md,
  },
  notice: {
    ...typography.caption,
    color: colors.primary,
    backgroundColor: colors.primaryGlow,
    padding: spacing.md,
    borderRadius: radii.md,
    marginBottom: spacing.md,
  },
  sectionTitle: {
    ...typography.h3,
    color: colors.text,
    marginBottom: spacing.sm,
  },
  empty: {
    ...typography.body,
    color: colors.textMuted,
  },
  cartRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    paddingVertical: spacing.md,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.surfaceBorder,
  },
  lineName: {
    ...typography.bodyBold,
    color: colors.text,
  },
  lineMeta: {
    ...typography.caption,
    color: colors.textSecondary,
  },
  lineNote: {
    ...typography.caption,
    color: colors.text,
    marginTop: 2,
  },
  addNote: {
    ...typography.caption,
    color: colors.primary,
    fontFamily: fonts.semibold,
    marginTop: 2,
  },
  qtyRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
  },
  qty: {
    ...typography.bodyBold,
    minWidth: 20,
    textAlign: 'center',
    color: colors.text,
  },
  lineTotal: {
    ...typography.bodyBold,
    minWidth: 64,
    textAlign: 'right',
    color: colors.text,
  },
  ticket: {
    borderWidth: 1,
    borderColor: colors.surfaceBorder,
    borderRadius: radii.lg,
    padding: spacing.md,
    marginBottom: spacing.sm,
    backgroundColor: colors.background,
  },
  ticketHead: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: spacing.xs,
  },
  ticketTitle: {
    ...typography.caption,
    color: colors.textSecondary,
    fontFamily: fonts.semibold,
  },
  ticketLine: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: 2,
  },
  ticketItem: {
    ...typography.body,
    color: colors.text,
  },
  ticketPrice: {
    ...typography.body,
    color: colors.textSecondary,
  },
  voidRequested: {
    ...typography.caption,
    color: colors.billed,
    fontFamily: fonts.semibold,
    marginTop: spacing.sm,
  },
  struck: {
    textDecorationLine: 'line-through',
    color: colors.textMuted,
  },
  footer: {
    padding: spacing.lg,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.surfaceBorder,
    backgroundColor: colors.surface,
    gap: spacing.md,
  },
  totals: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'baseline',
  },
  totalLabel: {
    ...typography.body,
    color: colors.textSecondary,
  },
  totalValue: {
    ...typography.h2,
    color: colors.text,
  },
  footerActions: {
    flexDirection: 'row',
    gap: spacing.md,
  },
  lockedNote: {
    ...typography.caption,
    color: colors.textMuted,
  },
});
