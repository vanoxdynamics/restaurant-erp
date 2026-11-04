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
  VoidApprovalItem,
  useAdvanceTicket,
  useCancelOrderLine,
  useFloorLayout,
  useOrder,
  useOrderTickets,
  useOrderVoidRequests,
  useRequestVoid,
  useSendToKitchen,
  useTransferTable,
  useVoidTicket,
} from '@repo/api-client';
import { ItemOptionsSheet, StepButton } from '../components/ItemOptionsSheet';
import { CartBar } from '../components/CartBar';
import { MenuPanel } from '../components/MenuPanel';
import { NoteSheet, TransferSheet, VoidRequestSheet } from '../components/Sheets';
import { QueryState } from '../components/QueryState';
import { TopBar } from '../components/TopBar';
import { Icon } from '../components/Icon';
import { useCartStore } from '../stores/cartStore';
import { useDeviceStore } from '../stores/deviceStore';
import { useSessionStore } from '../stores/sessionStore';
import { useKitchenPlan } from '../plan';
import { useKotPrinting } from '../printing/useKotPrinting';

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
  /** Opens the bill for this order; only offered to staff who may bill. */
  onBill?: () => void;
}

export const OrderScreen: React.FC<OrderScreenProps> = ({ orderId, title, onBack, onBill }) => {
  const { width } = useWindowDimensions();
  const wide = width >= 900;
  const device = useDeviceStore((s) => s.device)!;
  const role = useSessionStore((s) => s.user?.role);
  const plan = useKitchenPlan();
  const kotPrint = useKotPrinting(orderId);
  const slips = kotPrint.dialog;
  // A waiter's phone has no printer: paper KOTs print at the counter.
  const atCounter = kotPrint.mode === 'counter';

  const orderQuery = useOrder(orderId);
  const ticketsQuery = useOrderTickets(orderId);
  const floorQuery = useFloorLayout(device.branchId);
  const order = orderQuery.data;
  const tickets = ticketsQuery.data ?? [];

  const cart = useCartStore((s) => s.carts[orderId] ?? EMPTY_CART);
  const { add, setQuantity, setNotes, clear } = useCartStore();

  // The kitchen's newest answer per ticket, so the waiter sees "waiting" or why it was declined.
  const voidRequestsQuery = useOrderVoidRequests(orderId);
  const latestRequest = useMemo(() => {
    const byTicket = new Map<string, VoidApprovalItem>();
    for (const request of voidRequestsQuery.data ?? []) {
      if (!byTicket.has(request.kot_id)) byTicket.set(request.kot_id, request);
    }
    return byTicket;
  }, [voidRequestsQuery.data]);

  const send = useSendToKitchen();
  const cancelLine = useCancelOrderLine();
  const advanceTicket = useAdvanceTicket();
  const requestVoid = useRequestVoid();
  const voidTicketNow = useVoidTicket();
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
  const pendingTotal = cartTotal + unsentLines.reduce((n, l) => n + l.line_total_paise, 0);
  // Per item, how many are in the order being built, so the menu can show them and step them down.
  const pickedQuantities = useMemo(() => {
    const byItem: Record<string, number> = {};
    for (const line of cart) byItem[line.item_id] = (byItem[line.item_id] ?? 0) + line.quantity;
    return byItem;
  }, [cart]);
  const sentTickets = useMemo(
    () => tickets.slice().sort((a, b) => b.created_at.localeCompare(a.created_at)),
    [tickets],
  );
  const lineById = useMemo(() => new Map((order?.lines ?? []).map((l) => [l.id, l])), [order]);
  const editable = order?.status === 'active';

  const unpick = (item: MenuItem) => {
    const line = [...cart].reverse().find((c) => c.item_id === item.id);
    if (line) setQuantity(orderId, line.key, line.quantity - 1);
  };

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
      const kots = fired.map((t) => `KOT ${t.kot_number.split('-').pop()}`).join(', ');
      const onPaper = fired.filter((t) => t.fulfilment === 'slip');
      setNotice(
        onPaper.length === 0
          ? `Sent to kitchen: ${kots}`
          : atCounter
            ? `${kots} sent. It prints at the counter.`
            : `${kots} printing. Hand it to the kitchen.`,
      );
      if (!wide) setTab('order');
      // The order is already with the kitchen; a KOT that fails to print stays "not printed" with a way to retry.
      kotPrint.printNew(onPaper);
    } catch (e) {
      setError(errorText(e));
    }
  };

  const markServed = async (ticket: KitchenTicket) => {
    setError(null);
    try {
      await advanceTicket.mutateAsync({ kotId: ticket.id, status: 'served' });
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

  const submitVoid = async (reason: string, approverPin?: string) => {
    if (!voidTicket) return;
    setSheetError(null);
    try {
      if (voidTicket.fulfilment === 'slip') {
        const voided = await voidTicketNow.mutateAsync({ kotId: voidTicket.id, reason, approverPin: approverPin ?? '', orderId });
        const label = `KOT ${voided.kot_number.split('-').pop()}`;
        setNotice(
          atCounter
            ? `${label} cancelled. The VOID KOT prints at the counter; take the original back from the kitchen.`
            : `${label} cancelled. Take the original KOT back from the kitchen.`,
        );
        setVoidTicket(null);
        kotPrint.printVoid(voided);
        return;
      }
      await requestVoid.mutateAsync({ kotId: voidTicket.id, reason, orderId });
      setNotice(`Cancel request sent to the kitchen for KOT ${voidTicket.kot_number.split('-').pop()}`);
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
        {slips.error ? <Text style={styles.error}>{slips.error}</Text> : null}
        {notice ? <Text style={styles.notice}>{notice}</Text> : null}
        {slips.awaiting ? (
          <View style={styles.printCheck}>
            <Text style={styles.printCheckTitle}>
              Did the KOT print? {slips.awaiting.map((t) => `KOT ${t.kot_number.split('-').pop()}`).join(', ')}
            </Text>
            <View style={styles.printCheckActions}>
              <Button title="Yes, printed" variant="success" size="sm" onPress={slips.confirmPrinted} loading={slips.confirming} />
              <Button title="Print again" variant="secondary" size="sm" onPress={() => slips.print(slips.awaiting ?? [])} loading={slips.printing} />
              <Button title="Not printed" variant="ghost" size="sm" onPress={slips.notPrinted} />
            </View>
          </View>
        ) : null}

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
        <Text style={[styles.sectionTitle, { marginTop: spacing.xl }]}>{plan.slip ? 'KOTs' : 'Sent to kitchen'}</Text>
        <QueryState query={ticketsQuery} isEmpty={sentTickets.length === 0} emptyText="Nothing sent yet.">
          {sentTickets.map((ticket) => {
            const voidable = editable && (ticket.status === 'queued' || ticket.status === 'preparing' || ticket.status === 'ready');
            const request = latestRequest.get(ticket.id);
            const onPaper = ticket.fulfilment === 'slip';
            // Paper tickets skip the kitchen stages: the floor marks them served straight from queued.
            const servable = ticket.status === 'ready' || (onPaper && ticket.status === 'queued');
            const unprinted = onPaper && ticket.status === 'queued' && ticket.print_count === 0;
            const job = kotPrint.jobFor(ticket.id);
            const voidOwed = onPaper && ticket.status === 'voided' && ticket.print_count > 0 && !ticket.void_printed;
            return (
              <View key={ticket.id} style={[styles.ticket, ticket.status === 'voided' ? { opacity: 0.55 } : null]}>
                <View style={styles.ticketHead}>
                  <Text style={styles.ticketTitle}>
                    KOT {ticket.kot_number.split('-').pop()} · {humanizeLabel(ticket.station)}
                  </Text>
                  <Badge
                    label={
                      ticket.status === 'ready'
                        ? 'Ready, pick up'
                        : onPaper && ticket.status === 'queued'
                          ? 'On paper'
                          : humanizeLabel(ticket.status.toUpperCase())
                    }
                    variant={TICKET_BADGE[ticket.status]}
                    size="sm"
                  />
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
                {job?.state === 'printing' || job?.state === 'waiting' ? (
                  <Text style={styles.printing}>Printing…</Text>
                ) : job?.state === 'failed' ? (
                  <View style={styles.printFailed}>
                    <Text style={styles.notPrinted}>Not printed: {job.error}</Text>
                    <Button title="Retry" variant="secondary" size="sm" onPress={() => kotPrint.retry(job.key)} />
                  </View>
                ) : atCounter && onPaper && ticket.status === 'queued' ? (
                  ticket.print_count > 0 ? (
                    <Text style={styles.printedAtCounter}>Printed at counter ✓</Text>
                  ) : (
                    <Text style={styles.notPrinted}>Waiting for counter printer</Text>
                  )
                ) : atCounter && voidOwed ? (
                  <Text style={styles.notPrinted}>VOID KOT waiting for counter printer</Text>
                ) : unprinted ? (
                  <Text style={styles.notPrinted}>KOT not printed</Text>
                ) : null}
                {servable || (onPaper && !atCounter) ? (
                  <View style={styles.ticketActions}>
                    {servable ? (
                      <Button
                        title="Mark served"
                        variant="success"
                        onPress={() => markServed(ticket)}
                        loading={advanceTicket.isPending && advanceTicket.variables?.kotId === ticket.id}
                        style={{ flex: 1 }}
                      />
                    ) : null}
                    {atCounter ? null : onPaper && ticket.status === 'voided' ? (
                      <Button title="Print VOID KOT" variant="secondary" onPress={() => kotPrint.printVoid(ticket)} disabled={slips.printing} />
                    ) : onPaper && ticket.status !== 'served' ? (
                      <Button
                        title={unprinted ? 'Print KOT' : 'Reprint'}
                        variant={unprinted ? 'primary' : 'secondary'}
                        onPress={() => kotPrint.reprint(ticket)}
                        disabled={slips.printing}
                        icon={<Icon name="printer" size={16} color={unprinted ? colors.textInverse : colors.text} />}
                      />
                    ) : null}
                  </View>
                ) : null}
                {voidable && onPaper ? (
                  <Button
                    title="Cancel ticket"
                    variant="ghost"
                    size="sm"
                    onPress={() => {
                      setSheetError(null);
                      setVoidTicket(ticket);
                    }}
                    textStyle={{ color: colors.danger }}
                    style={{ alignSelf: 'flex-start', marginTop: spacing.xs, paddingHorizontal: 0 }}
                  />
                ) : voidable && request?.status === 'PENDING' ? (
                  <Text style={styles.voidRequested}>Cancel requested · waiting for kitchen</Text>
                ) : voidable ? (
                  <>
                    {request?.status === 'REJECTED' ? (
                      <Text style={styles.voidDeclined}>
                        Kitchen declined{request.review_note ? `: ${request.review_note}` : ''}
                      </Text>
                    ) : null}
                    <Button
                      title="Request cancel"
                      variant="ghost"
                      size="sm"
                      onPress={() => {
                        setSheetError(null);
                        setVoidTicket(ticket);
                      }}
                      textStyle={{ color: colors.danger }}
                      style={{ alignSelf: 'flex-start', marginTop: spacing.xs, paddingHorizontal: 0 }}
                    />
                  </>
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
          {onBill && plan.canBill(role) && order && ['active', 'billed'].includes(order.status) && order.lines.length > 0 ? (
            <Button
              title={order.status === 'billed' ? 'Payment' : 'Bill'}
              variant="secondary"
              onPress={onBill}
              disabled={cartCount > 0}
            />
          ) : null}
          <Button
            // Without a kitchen screen the order goes to the kitchen on paper: the waiter prints the KOT and hands it over.
            title={`${plan.slip ? (atCounter ? 'Send KOT' : 'Print KOT') : 'Send to kitchen'}${cartCount ? ` · ${cartCount}` : ''}`}
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
              <MenuPanel onPick={pick} quantities={pickedQuantities} onDecrement={unpick} />
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
            {tab === 'menu' ? (
              <>
                <View style={{ flex: 1 }}>
                  <MenuPanel onPick={pick} quantities={pickedQuantities} onDecrement={unpick} />
                </View>
                {editable && cartCount > 0 ? (
                  <CartBar count={cartCount} totalPaise={pendingTotal} onPress={() => setTab('order')} />
                ) : null}
              </>
            ) : (
              orderPanel
            )}
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
        withManagerPin={voidTicket?.fulfilment === 'slip'}
        loading={requestVoid.isPending || voidTicketNow.isPending}
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
  voidDeclined: {
    ...typography.caption,
    color: colors.danger,
    fontFamily: fonts.semibold,
    marginTop: spacing.sm,
  },
  printing: {
    ...typography.caption,
    color: colors.textSecondary,
    fontFamily: fonts.semibold,
    marginTop: spacing.sm,
  },
  printedAtCounter: {
    ...typography.caption,
    color: colors.success,
    fontFamily: fonts.semibold,
    marginTop: spacing.sm,
  },
  printFailed: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: spacing.sm,
  },
  notPrinted: {
    ...typography.caption,
    color: colors.danger,
    fontFamily: fonts.semibold,
    marginTop: spacing.sm,
  },
  ticketActions: {
    flexDirection: 'row',
    gap: spacing.sm,
    marginTop: spacing.sm,
  },
  printCheck: {
    borderWidth: 1,
    borderColor: colors.primary,
    backgroundColor: colors.primaryGlow,
    borderRadius: radii.md,
    padding: spacing.md,
    marginBottom: spacing.md,
    gap: spacing.sm,
  },
  printCheckTitle: {
    ...typography.bodyBold,
    color: colors.text,
  },
  printCheckActions: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.sm,
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
