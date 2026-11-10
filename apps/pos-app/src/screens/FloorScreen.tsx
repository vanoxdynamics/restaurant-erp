import React, { useMemo, useState } from 'react';
import { Pressable, RefreshControl, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { Button, colors, fonts, radii, spacing, TableStatusBadge, typography } from '@repo/shared-ui';
import { formatPaiseToRupees, getOccupancyTime, TableStatus } from '@repo/shared-utils';
import { ApiError, DiningTable, useCreateOrder, useFloorLayout, useKitchenBoard, useOpenTakeaways } from '@repo/api-client';
import { GuestCountSheet } from '../components/GuestCountSheet';
import { Icon } from '../components/Icon';
import { QueryState } from '../components/QueryState';
import { TopBar } from '../components/TopBar';
import { useDeviceStore } from '../stores/deviceStore';
import { useKitchenPlan } from '../plan';

const STATUS_TINT: Record<string, string> = {
  [TableStatus.AVAILABLE]: colors.available,
  [TableStatus.OCCUPIED]: colors.occupied,
  [TableStatus.BILLED]: colors.billed,
  [TableStatus.RESERVED]: colors.dirty,
};

const ReadyChip: React.FC<{ count: number; onPaper?: boolean }> = ({ count, onPaper }) => (
  <View style={styles.readyChip} accessibilityLabel={`${count} ready to serve`}>
    <Icon name="check-circle" size={14} color={colors.textInverse} />
    <Text style={styles.readyChipText}>{count} {onPaper ? 'to serve' : 'ready'}</Text>
  </View>
);

const UnprintedChip: React.FC<{ count: number }> = ({ count }) => (
  <View style={[styles.readyChip, { backgroundColor: colors.danger }]} accessibilityLabel={`${count} KOTs not printed`}>
    <Icon name="printer" size={14} color={colors.textInverse} />
    <Text style={styles.readyChipText}>{count} not printed</Text>
  </View>
);

/** The chips a table or takeaway tile shows for its open tickets. */
const TicketChips: React.FC<{ ready: number; unprinted: number; onPaper: boolean }> = ({ ready, unprinted, onPaper }) => (
  <>
    {unprinted > 0 ? <UnprintedChip count={unprinted} /> : null}
    {ready > 0 ? <ReadyChip count={ready} onPaper={onPaper} /> : null}
  </>
);

export interface FloorScreenProps {
  onOpenOrder: (orderId: string, title: string) => void;
  /** On the counter device the floor is opened from the bills list, so it has a way back. */
  onBack?: () => void;
}

export const FloorScreen: React.FC<FloorScreenProps> = ({ onOpenOrder, onBack }) => {
  const device = useDeviceStore((s) => s.device)!;
  const floorQuery = useFloorLayout(device.branchId);
  const takeawayQuery = useOpenTakeaways(device.branchId);
  const createOrder = useCreateOrder();

  // Tickets waiting for the floor, counted per order, so a waiter can see food to pick up without opening each table: screen
  // tickets once the kitchen marks them ready. Paper KOTs have no stages, so only those that never printed are flagged (the
  // kitchen does not know about those orders yet).
  const plan = useKitchenPlan();
  const board = useKitchenBoard(device.branchId);
  const { readyByOrder, unprintedByOrder } = useMemo(() => {
    const ready = new Map<string, number>();
    const unprinted = new Map<string, number>();
    const bump = (m: Map<string, number>, id: string) => m.set(id, (m.get(id) ?? 0) + 1);
    for (const ticket of board.data ?? []) {
      const onPaper = ticket.fulfilment === 'slip' && ticket.status === 'queued';
      if (ticket.status === 'ready' && ticket.fulfilment !== 'slip') bump(ready, ticket.order_id);
      if (onPaper && ticket.print_count === 0) bump(unprinted, ticket.order_id);
    }
    return { readyByOrder: ready, unprintedByOrder: unprinted };
  }, [board.data]);

  const floors = floorQuery.data ?? [];
  const [area, setArea] = useState<string>('ALL');
  const [seating, setSeating] = useState<DiningTable | null>(null);
  const [error, setError] = useState<string | null>(null);

  const tables = useMemo(
    () => floors.flatMap((f) => f.tables).filter((t) => area === 'ALL' || t.section === area),
    [floors, area],
  );

  const openTable = async (guests: number) => {
    if (!seating) return;
    setError(null);
    try {
      const order = await createOrder.mutateAsync({ tableId: seating.id, orderType: 'dine_in', guestCount: guests });
      const title = `Table ${seating.table_number}`;
      setSeating(null);
      onOpenOrder(order.id, title);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not open the table.');
    }
  };

  const newTakeaway = async () => {
    setError(null);
    try {
      const order = await createOrder.mutateAsync({ orderType: 'takeaway', guestCount: 1 });
      onOpenOrder(order.id, 'Takeaway');
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not start a takeaway order.');
    }
  };

  const onTablePress = (table: DiningTable) => {
    if (table.merged_into_table_id) return;
    if (table.running_order_id) onOpenOrder(table.running_order_id, `Table ${table.table_number}`);
    else if (table.status === TableStatus.AVAILABLE || table.status === TableStatus.RESERVED) {
      setError(null);
      setSeating(table);
    }
  };

  return (
    <View style={styles.container}>
      <TopBar
        title="Tables"
        subtitle={device.branchName}
        onBack={onBack}
        right={<Button title="Takeaway" size="sm" variant="secondary" onPress={newTakeaway} icon={<Icon name="shopping-bag" size={15} color={colors.text} />} />}
      />

      <ScrollView
        contentContainerStyle={styles.content}
        refreshControl={<RefreshControl refreshing={floorQuery.isRefetching} onRefresh={floorQuery.refetch} tintColor={colors.primary} />}
      >
        {error && !seating ? <Text style={styles.error}>{error}</Text> : null}

        {floors.length > 1 ? (
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chips}>
            {['ALL', ...floors.map((f) => f.name)].map((name) => (
              <TouchableOpacity
                key={name}
                onPress={() => setArea(name)}
                style={[styles.chip, area === name ? styles.chipActive : null]}
                accessibilityRole="tab"
                accessibilityState={{ selected: area === name }}
              >
                <Text style={[styles.chipText, area === name ? styles.chipTextActive : null]}>
                  {name === 'ALL' ? 'All areas' : name}
                </Text>
              </TouchableOpacity>
            ))}
          </ScrollView>
        ) : null}

        <QueryState query={floorQuery} isEmpty={tables.length === 0} emptyText="No tables set up for this branch.">
          <View style={styles.grid}>
            {tables.map((table) => {
              const busy = !!table.running_order_id;
              const merged = !!table.merged_into_table_id;
              const ready = table.running_order_id ? readyByOrder.get(table.running_order_id) ?? 0 : 0;
              const unprinted = table.running_order_id ? unprintedByOrder.get(table.running_order_id) ?? 0 : 0;
              return (
                <Pressable
                  key={table.id}
                  onPress={() => onTablePress(table)}
                  disabled={merged}
                  accessibilityRole="button"
                  accessibilityLabel={`Table ${table.table_number}, ${merged ? 'merged' : table.status.toLowerCase()}`}
                  style={({ pressed }) => [
                    styles.tile,
                    { borderTopColor: STATUS_TINT[table.status] ?? colors.surfaceBorder },
                    merged ? { opacity: 0.5 } : null,
                    pressed ? styles.tilePressed : null,
                  ]}
                >
                  <View style={styles.tileTop}>
                    <Text style={styles.tableNumber}>{table.table_number}</Text>
                    {merged ? null : <TableStatusBadge status={table.status} size="sm" />}
                  </View>
                  <Text style={styles.seats}>{merged ? 'Merged' : `${table.capacity} seats`}</Text>
                  {busy ? (
                    <View style={{ marginTop: spacing.sm }}>
                      <Text style={styles.amount}>{formatPaiseToRupees(table.running_bill_paise ?? 0, { decimals: 0 })}</Text>
                      <Text style={styles.meta}>
                        {[table.occupied_at ? getOccupancyTime(table.occupied_at) : null, table.item_count ? `${table.item_count} items` : null]
                          .filter(Boolean)
                          .join(' · ')}
                      </Text>
                    </View>
                  ) : null}
                  <TicketChips ready={ready} unprinted={unprinted} onPaper={plan.slip} />
                </Pressable>
              );
            })}
          </View>
        </QueryState>

        {(takeawayQuery.data ?? []).length > 0 ? (
          <View style={styles.section}>
            <Text style={styles.sectionTitle}>Takeaway</Text>
            <View style={styles.grid}>
              {(takeawayQuery.data ?? []).map((order) => {
                const label = 'Takeaway';
                const items = order.lines.filter((l) => l.status !== 'cancelled').reduce((n, l) => n + l.quantity, 0);
                const ready = readyByOrder.get(order.id) ?? 0;
                const unprinted = unprintedByOrder.get(order.id) ?? 0;
                return (
                  <Pressable
                    key={order.id}
                    onPress={() => onOpenOrder(order.id, label)}
                    accessibilityRole="button"
                    style={({ pressed }) => [styles.tile, { borderTopColor: colors.accent }, pressed ? styles.tilePressed : null]}
                  >
                    <Text style={styles.tableNumber}>{order.order_number.split('-').pop()}</Text>
                    <Text style={styles.seats}>Takeaway</Text>
                    <View style={{ marginTop: spacing.sm }}>
                      <Text style={styles.amount}>{formatPaiseToRupees(order.subtotal_paise, { decimals: 0 })}</Text>
                      <Text style={styles.meta}>
                        {getOccupancyTime(order.created_at)} · {items} items
                      </Text>
                    </View>
                    <TicketChips ready={ready} unprinted={unprinted} onPaper={plan.slip} />
                  </Pressable>
                );
              })}
            </View>
          </View>
        ) : null}
      </ScrollView>

      <GuestCountSheet
        visible={seating !== null}
        title={seating ? `Table ${seating.table_number}` : ''}
        defaultGuests={2}
        loading={createOrder.isPending}
        error={error}
        onClose={() => setSeating(null)}
        onConfirm={openTable}
      />
    </View>
  );
};

const styles = StyleSheet.create({
  readyChip: {
    flexDirection: 'row',
    alignItems: 'center',
    alignSelf: 'flex-start',
    gap: spacing.xs,
    marginTop: spacing.sm,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
    borderRadius: radii.full,
    backgroundColor: colors.success,
  },
  readyChipText: {
    ...typography.caption,
    color: colors.textInverse,
    fontFamily: fonts.bold,
  },
  container: {
    flex: 1,
    backgroundColor: colors.background,
  },
  content: {
    padding: spacing.lg,
    paddingBottom: spacing.xxxl,
  },
  error: {
    ...typography.caption,
    color: colors.danger,
    marginBottom: spacing.md,
  },
  chips: {
    gap: spacing.sm,
    paddingBottom: spacing.lg,
  },
  chip: {
    minHeight: 40,
    paddingHorizontal: spacing.lg,
    borderRadius: radii.full,
    borderWidth: 1,
    borderColor: colors.surfaceBorder,
    backgroundColor: colors.surface,
    justifyContent: 'center',
  },
  chipActive: {
    backgroundColor: colors.primaryGlow,
    borderColor: colors.primary,
  },
  chipText: {
    ...typography.caption,
    color: colors.textSecondary,
  },
  chipTextActive: {
    color: colors.primary,
    fontFamily: fonts.semibold,
  },
  grid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.md,
  },
  tile: {
    width: 156,
    minHeight: 132,
    padding: spacing.md,
    borderRadius: radii.lg,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.surfaceBorder,
    borderTopWidth: 4,
  },
  tilePressed: {
    backgroundColor: colors.surfaceLight,
  },
  tileTop: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    gap: spacing.xs,
  },
  tableNumber: {
    ...typography.h2,
    color: colors.text,
  },
  seats: {
    ...typography.caption,
    color: colors.textMuted,
  },
  amount: {
    ...typography.h3,
    color: colors.text,
  },
  meta: {
    ...typography.caption,
    color: colors.textSecondary,
  },
  section: {
    marginTop: spacing.xxl,
  },
  sectionTitle: {
    ...typography.h3,
    color: colors.text,
    marginBottom: spacing.md,
  },
});
