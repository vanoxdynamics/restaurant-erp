import React, { useMemo } from 'react';
import { Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';
import { Badge, Button, colors, fonts, radii, spacing, typography } from '@repo/shared-ui';
import { formatPaiseToRupees, getOccupancyTime, TableStatus } from '@repo/shared-utils';
import { useFloorLayout, useOpenTakeaways } from '@repo/api-client';
import { Icon } from '../components/Icon';
import { QueryState } from '../components/QueryState';
import { TopBar } from '../components/TopBar';
import { usePrintQueueStore } from '../printing/printQueue';
import { useDeviceStore } from '../stores/deviceStore';

interface OpenBill {
  orderId: string;
  title: string;
  amountPaise: number;
  items: number;
  since?: string;
  billed: boolean;
}

export interface CounterHomeProps {
  onOpenOrder: (orderId: string, title: string) => void;
  onOpenBill: (orderId: string, title: string) => void;
  onNewOrder: () => void;
  onPrinter: () => void;
  onDayEnd: () => void;
}

/**
 * The counter device's home: every open table and takeaway with what it owes, so the cashier can bill whatever comes to the
 * counter. Waiters fill these from their phones.
 */
export const CounterHomeScreen: React.FC<CounterHomeProps> = ({ onOpenOrder, onOpenBill, onNewOrder, onPrinter, onDayEnd }) => {
  const device = useDeviceStore((s) => s.device)!;
  const floorQuery = useFloorLayout(device.branchId);
  const takeawayQuery = useOpenTakeaways(device.branchId);
  const failedPrints = usePrintQueueStore((s) => s.jobs.filter((j) => j.state === 'failed').length);

  const bills = useMemo<OpenBill[]>(() => {
    const out: OpenBill[] = [];
    for (const floor of floorQuery.data ?? []) {
      for (const table of floor.tables) {
        if (!table.running_order_id || table.merged_into_table_id) continue;
        out.push({
          orderId: table.running_order_id,
          title: `Table ${table.table_number}`,
          amountPaise: table.running_bill_paise ?? 0,
          items: table.item_count ?? 0,
          since: table.occupied_at,
          billed: table.status === TableStatus.BILLED,
        });
      }
    }
    for (const order of takeawayQuery.data ?? []) {
      out.push({
        orderId: order.id,
        title: `Takeaway #${order.order_number.split('-').pop()}`,
        amountPaise: order.subtotal_paise,
        items: order.lines.filter((l) => l.status !== 'cancelled').reduce((n, l) => n + l.quantity, 0),
        since: order.created_at,
        billed: order.status === 'billed',
      });
    }
    // Bills waiting for payment first, then the longest-running orders.
    return out.sort((a, b) => Number(b.billed) - Number(a.billed) || (a.since ?? '').localeCompare(b.since ?? ''));
  }, [floorQuery.data, takeawayQuery.data]);

  const total = bills.reduce((n, b) => n + b.amountPaise, 0);

  return (
    <View style={styles.container}>
      <TopBar title="Counter" subtitle={device.branchName} />
      <View style={styles.actions}>
        <Button title="New order" onPress={onNewOrder} icon={<Icon name="plus" size={16} color={colors.textInverse} />} style={{ flex: 1 }} />
        <Button title="KOT printer" variant="secondary" onPress={onPrinter} icon={<Icon name="printer" size={16} color={colors.text} />} />
        <Button title="Day end" variant="secondary" onPress={onDayEnd} icon={<Icon name="moon" size={16} color={colors.text} />} />
      </View>

      {failedPrints > 0 ? (
        <Pressable onPress={onPrinter} style={styles.alert} accessibilityRole="button">
          <Icon name="alert-triangle" size={16} color={colors.danger} />
          <Text style={styles.alertText}>
            {failedPrints} KOT{failedPrints === 1 ? '' : 's'} did not print. Tap to see why and retry.
          </Text>
        </Pressable>
      ) : null}

      <ScrollView
        contentContainerStyle={styles.scroll}
        refreshControl={<RefreshControl refreshing={floorQuery.isRefetching} onRefresh={() => { void floorQuery.refetch(); void takeawayQuery.refetch(); }} />}
      >
        <View style={styles.summary}>
          <Text style={styles.summaryText}>{bills.length} open</Text>
          <Text style={styles.summaryText}>{formatPaiseToRupees(total, { decimals: 0 })}</Text>
        </View>
        <QueryState query={floorQuery} isEmpty={bills.length === 0} emptyText="No open tables or takeaways.">
          {bills.map((b) => (
            <Pressable
              key={b.orderId}
              onPress={() => onOpenOrder(b.orderId, b.title)}
              accessibilityRole="button"
              accessibilityLabel={`${b.title}, ${formatPaiseToRupees(b.amountPaise)}`}
              style={({ pressed }) => [styles.row, pressed ? styles.rowPressed : null]}
            >
              <View style={{ flex: 1 }}>
                <View style={styles.rowHead}>
                  <Text style={styles.rowTitle}>{b.title}</Text>
                  {b.billed ? <Badge label="Bill printed" variant="warning" size="sm" /> : null}
                </View>
                <Text style={styles.meta}>
                  {[b.items ? `${b.items} items` : null, b.since ? getOccupancyTime(b.since) : null].filter(Boolean).join(' · ')}
                </Text>
              </View>
              <Text style={styles.amount}>{formatPaiseToRupees(b.amountPaise, { decimals: 0 })}</Text>
              <Button title="Bill" size="sm" onPress={() => onOpenBill(b.orderId, b.title)} />
            </Pressable>
          ))}
        </QueryState>
      </ScrollView>
    </View>
  );
};

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  actions: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, padding: spacing.lg, paddingBottom: 0 },
  alert: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    margin: spacing.lg,
    marginBottom: 0,
    padding: spacing.md,
    borderRadius: radii.md,
    backgroundColor: colors.dangerBg,
  },
  alertText: { ...typography.caption, color: colors.danger, fontFamily: fonts.semibold, flex: 1 },
  scroll: { padding: spacing.lg, gap: spacing.sm, maxWidth: 640, width: '100%', alignSelf: 'center' },
  summary: { flexDirection: 'row', justifyContent: 'space-between', paddingHorizontal: spacing.xs },
  summaryText: { ...typography.caption, color: colors.textSecondary, fontFamily: fonts.semibold },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    padding: spacing.md,
    borderRadius: radii.lg,
    borderWidth: 1,
    borderColor: colors.surfaceBorder,
    backgroundColor: colors.surface,
  },
  rowPressed: { backgroundColor: colors.surfaceLight },
  rowHead: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  rowTitle: { ...typography.bodyBold, color: colors.text },
  meta: { ...typography.caption, color: colors.textSecondary },
  amount: { ...typography.bodyBold, color: colors.text },
});
