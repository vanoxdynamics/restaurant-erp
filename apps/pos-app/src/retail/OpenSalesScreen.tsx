import React from 'react';
import { Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';
import { Badge, colors, radii, spacing, typography } from '@repo/shared-ui';
import { formatPaiseToRupees, getOccupancyTime } from '@repo/shared-utils';
import { useOpenTakeaways } from '@repo/api-client';
import { QueryState } from '../components/QueryState';
import { TopBar } from '../components/TopBar';
import { useDeviceStore } from '../stores/deviceStore';
import { saleTitle } from './QuickSaleScreen';

export interface OpenSalesProps {
  onBack: () => void;
  onOpenBill: (orderId: string, title: string) => void;
}

/** Sales saved but not paid yet (the customer stepped away, or a bill is waiting): tap one to finish it. */
export const OpenSalesScreen: React.FC<OpenSalesProps> = ({ onBack, onOpenBill }) => {
  const device = useDeviceStore((s) => s.device)!;
  const query = useOpenTakeaways(device.branchId);
  const sales = (query.data ?? []).filter((o) => o.order_type === 'counter_sale');

  return (
    <View style={styles.container}>
      <TopBar title="Open bills" subtitle={device.branchName} onBack={onBack} />
      <ScrollView
        contentContainerStyle={styles.scroll}
        refreshControl={<RefreshControl refreshing={query.isRefetching} onRefresh={() => void query.refetch()} />}
      >
        <QueryState query={query} isEmpty={sales.length === 0} emptyText="No open bills.">
          {sales.map((order) => (
            <Pressable
              key={order.id}
              onPress={() => onOpenBill(order.id, saleTitle(order))}
              accessibilityRole="button"
              style={({ pressed }) => [styles.row, pressed ? { backgroundColor: colors.surfaceLight } : null]}
            >
              <View style={{ flex: 1 }}>
                <Text style={styles.title}>{saleTitle(order)}</Text>
                <Text style={styles.meta}>
                  {order.lines.filter((l) => l.status !== 'cancelled').length} lines · {getOccupancyTime(order.created_at)}
                </Text>
              </View>
              {order.status === 'billed' ? <Badge label="Bill printed" variant="warning" size="sm" /> : null}
              <Text style={styles.amount}>{formatPaiseToRupees(order.subtotal_paise)}</Text>
            </Pressable>
          ))}
        </QueryState>
      </ScrollView>
    </View>
  );
};

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  scroll: { padding: spacing.lg, gap: spacing.sm, maxWidth: 640, width: '100%', alignSelf: 'center' },
  row: {
    flexDirection: 'row', alignItems: 'center', gap: spacing.md, padding: spacing.md, borderRadius: radii.lg,
    borderWidth: 1, borderColor: colors.surfaceBorder, backgroundColor: colors.surface,
  },
  title: { ...typography.bodyBold, color: colors.text },
  meta: { ...typography.caption, color: colors.textSecondary },
  amount: { ...typography.bodyBold, color: colors.text },
});
