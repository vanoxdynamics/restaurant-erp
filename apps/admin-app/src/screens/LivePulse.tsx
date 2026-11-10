import React from 'react';
import { View, Text, ScrollView, StyleSheet, Pressable, RefreshControl } from 'react-native';
import { colors, radii, spacing, typography, StatCard, Card, SectionHeader, fonts } from '@repo/shared-ui';
import { formatPaiseToRupees, formatDateTime } from '@repo/shared-utils';
import {
  useLivePulse,
  useFraudAlerts,
  useVoidApprovals,
  useZReport,
  useTodayPayments,
} from '@repo/api-client';
import {
  LiveSalesPulseChart,
  PaymentBreakdownChart,
  TopSellingRankingChart,
} from '../components/SkiaCharts';
import { QueryState } from '../components/QueryState';
import { useAuthStore } from '../stores/authStore';
import { Icon } from '../components/Icon';

export interface LivePulseScreenProps {
  onNavigateToTab?: (tabName: string) => void;
  /** Cancel requests (and their banner) only exist with the kitchen display. */
  hasKitchenScreen?: boolean;
}

const money = (paise: number) => formatPaiseToRupees(paise, { decimals: 0 });

export const LivePulseScreen: React.FC<LivePulseScreenProps> = ({ onNavigateToTab, hasKitchenScreen = true }) => {
  const { activeBranchId } = useAuthStore();
  const kpiQuery = useLivePulse(activeBranchId);
  const { data: kpis } = kpiQuery;
  const { data: alerts } = useFraudAlerts(activeBranchId);
  const { data: pending = [] } = useVoidApprovals(activeBranchId, 'pending', hasKitchenScreen);
  const { data: payments } = useTodayPayments(activeBranchId);
  const { data: zReport } = useZReport(activeBranchId);

  const pendingVoidCount = pending.length;
  const totalTables = kpis ? kpis.active_tables_count + kpis.vacant_tables_count : 0;
  const occupancy = totalTables ? Math.round((kpis!.active_tables_count / totalTables) * 100) : 0;

  return (
    <ScrollView
      style={styles.container}
      contentContainerStyle={styles.contentContainer}
      refreshControl={
        <RefreshControl refreshing={kpiQuery.isRefetching} onRefresh={() => void kpiQuery.refetch()} tintColor={colors.primary} />
      }
    >
      <QueryState query={kpiQuery}>
        {kpis ? (
          <>
            <View style={styles.hero}>
              <Text style={styles.heroLabel}>Today's sales</Text>
              <Text style={styles.heroValue} accessibilityRole="summary">
                {money(kpis.total_sales_today_paise)}
              </Text>
            </View>

            {hasKitchenScreen && pendingVoidCount > 0 && (
              <Pressable
                style={({ pressed }) => [styles.approvalBanner, pressed ? { opacity: 0.85 } : null]}
                onPress={() => onNavigateToTab && onNavigateToTab('VoidApprovals')}
                accessibilityRole="button"
                accessibilityLabel={`${pendingVoidCount} void requests pending`}
              >
                <View style={styles.approvalIcon}>
                  <Icon name="alert-circle" size={18} color={colors.billed} />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={styles.approvalTitle}>
                    {pendingVoidCount === 1 ? '1 void request pending' : `${pendingVoidCount} void requests pending`}
                  </Text>
                </View>
                <Icon name="chevron-right" size={20} color={colors.billed} />
              </Pressable>
            )}

            <View style={styles.kpiGrid}>
              <StatCard
                title="Occupied tables"
                value={`${kpis.active_tables_count} / ${totalTables}`}
                subtitle={`${occupancy}% occupancy`}
                onPress={() => onNavigateToTab && onNavigateToTab('FloorObserver')}
              />
              <StatCard title="Average bill" value={money(kpis.average_ticket_size_paise)} />
            </View>

            <View style={styles.kpiGrid}>
              <StatCard title="Open orders" value={`${kpis.open_orders_count}`} />
              <StatCard title="Kitchen queue" value={`${kpis.pending_kots_count} tickets`} />
            </View>

            {kpis.hourly_sales.length > 0 && <LiveSalesPulseChart data={kpis.hourly_sales} />}
            {payments && payments.total_paise > 0 && <PaymentBreakdownChart breakdown={payments} />}
            {zReport && zReport.top_selling_items.length > 0 && (
              <TopSellingRankingChart items={zReport.top_selling_items} />
            )}
          </>
        ) : null}
      </QueryState>

      <View style={styles.section}>
        <SectionHeader
          title="Alerts"
          actionLabel={hasKitchenScreen ? 'Approvals' : undefined}
          onAction={onNavigateToTab && hasKitchenScreen ? () => onNavigateToTab('VoidApprovals') : undefined}
        />

        {alerts?.length ? (
          <Card padded={false}>
            {alerts.map((alert, index) => (
              <View key={alert.id} style={[styles.alertRow, index > 0 ? styles.alertDivider : null]}>
                <View
                  style={[
                    styles.alertMarker,
                    { backgroundColor: alert.risk_level === 'HIGH' ? colors.occupied : colors.billed },
                  ]}
                />
                <View style={{ flex: 1 }}>
                  <Text style={styles.alertDetails}>{alert.details}</Text>
                  <Text style={styles.alertMeta}>
                    {alert.actor_name} · {formatDateTime(alert.timestamp)}
                  </Text>
                </View>
                {alert.amount_paise ? <Text style={styles.alertAmount}>{money(alert.amount_paise)}</Text> : null}
              </View>
            ))}
          </Card>
        ) : (
          <Card>
            <Text style={styles.emptyText}>No alerts.</Text>
          </Card>
        )}
      </View>
    </ScrollView>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.background,
  },
  contentContainer: {
    paddingHorizontal: spacing.xl,
    paddingTop: spacing.sm,
    paddingBottom: spacing.xxxl,
  },
  hero: {
    paddingVertical: spacing.lg,
  },
  heroLabel: {
    ...typography.caption,
    color: colors.textSecondary,
  },
  heroValue: {
    ...typography.display,
    fontSize: 40,
    lineHeight: 48,
    color: colors.text,
    marginTop: 2,
  },
  heroMetaRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    marginTop: 4,
  },
  heroMeta: {
    ...typography.caption,
    color: colors.primary,
    fontFamily: fonts.semibold,
  },
  approvalBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    backgroundColor: colors.billedBg,
    borderRadius: radii.lg,
    paddingVertical: spacing.md,
    paddingHorizontal: spacing.lg,
    marginBottom: spacing.lg,
  },
  approvalIcon: {
    width: 36,
    height: 36,
    borderRadius: 18,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: 'rgba(255,255,255,0.6)',
  },
  approvalTitle: {
    ...typography.bodyBold,
    color: colors.text,
  },
  approvalSubtitle: {
    ...typography.caption,
    color: colors.textSecondary,
  },
  kpiGrid: {
    flexDirection: 'row',
    gap: spacing.md,
    marginBottom: spacing.md,
  },
  section: {
    marginTop: spacing.xl,
  },
  alertRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: spacing.md,
    paddingVertical: spacing.md,
    paddingHorizontal: spacing.lg,
  },
  alertDivider: {
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.surfaceBorder,
  },
  alertMarker: {
    width: 8,
    height: 8,
    borderRadius: 4,
    marginTop: 7,
  },
  alertDetails: {
    ...typography.body,
    color: colors.text,
  },
  alertMeta: {
    ...typography.caption,
    color: colors.textMuted,
    marginTop: 2,
  },
  alertAmount: {
    ...typography.bodyBold,
    color: colors.text,
  },
  emptyText: {
    ...typography.body,
    color: colors.textSecondary,
  },
});
