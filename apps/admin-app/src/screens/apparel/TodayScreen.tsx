import React from 'react';
import { RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';
import { Card, colors, fonts, SectionHeader, spacing, StatCard, typography } from '@repo/shared-ui';
import { formatDateTime, formatPaiseToRupees } from '@repo/shared-utils';
import { useApparelDayReport, useFraudAlerts } from '@repo/api-client';
import { PaymentBreakdownChart, TopSellingRankingChart } from '../../components/SkiaCharts';
import { QueryState } from '../../components/QueryState';
import { useAuthStore } from '../../stores/authStore';

const money = (paise: number) => formatPaiseToRupees(paise, { decimals: 0 });

/**
 * The dress shop's Today tab. Its sales live in the apparel module (apparel_sales, apparel_returns), not in the restaurant
 * orders and bills the live pulse reads, so this is built from the day report. Alerts come from the shared audit log.
 */
export const TodayScreen: React.FC = () => {
  const branchId = useAuthStore((s) => s.activeBranchId) ?? '';
  // No date: the server takes today in the branch's own time zone.
  const query = useApparelDayReport({ branch_id: branchId }, !!branchId);
  const { data: alerts } = useFraudAlerts(branchId);
  const r = query.data;

  const paid = (mode: string) => r?.payments.find((p) => p.mode === mode)?.amount_paise ?? 0;
  const credit = paid('credit_note');
  const breakdown = r
    ? { cash_paise: paid('cash'), card_paise: paid('card'), upi_paise: paid('upi'), total_paise: paid('cash') + paid('card') + paid('upi') }
    : null;

  return (
    <ScrollView
      style={styles.container}
      contentContainerStyle={styles.content}
      refreshControl={<RefreshControl refreshing={query.isRefetching} onRefresh={() => void query.refetch()} tintColor={colors.primary} />}
    >
      <QueryState query={query}>
        {r ? (
          <>
            <View style={styles.hero}>
              <Text style={styles.heroLabel}>Today's sales</Text>
              <Text style={styles.heroValue} accessibilityRole="summary">{money(r.sales.total_paise)}</Text>
              <Text style={styles.heroMeta}>Net cash in the drawer: {money(r.net_cash_paise)}</Text>
            </View>

            <View style={styles.grid}>
              <StatCard title="Invoices" value={`${r.sales.count}`} subtitle={`${r.sales.units} pieces sold`} />
              <StatCard title="Average bill" value={r.sales.count ? money(Math.round(r.sales.total_paise / r.sales.count)) : '—'} />
            </View>
            <View style={styles.grid}>
              <StatCard title="Discounts" value={money(r.sales.discount_paise)} />
              <StatCard title="Returns" value={money(r.returns.total_paise)} subtitle={`${r.returns.count} taken back`} />
            </View>

            {breakdown && breakdown.total_paise > 0 ? <PaymentBreakdownChart breakdown={breakdown} /> : null}
            {credit > 0 ? <Text style={styles.note}>Also paid with store credit: {money(credit)}</Text> : null}

            {r.top_products.length > 0 ? (
              <TopSellingRankingChart
                title="Top products"
                items={r.top_products.map((p) => ({ name: p.name, quantity: p.units, total_sales_paise: p.value_paise, category_name: '' }))}
              />
            ) : null}

            {r.units_by_size.length > 0 ? (
              <Card style={styles.block}>
                <SectionHeader title="Pieces by size" />
                <Text style={styles.sizes}>{r.units_by_size.map((z) => `${z.size}: ${z.units}`).join('   ')}</Text>
              </Card>
            ) : null}
          </>
        ) : null}
      </QueryState>

      <View style={styles.section}>
        <SectionHeader title="Alerts" />
        {alerts?.length ? (
          <Card padded={false}>
            {alerts.map((alert, index) => (
              <View key={alert.id} style={[styles.alertRow, index > 0 ? styles.alertDivider : null]}>
                <View style={[styles.alertMarker, { backgroundColor: alert.risk_level === 'HIGH' ? colors.occupied : colors.billed }]} />
                <View style={{ flex: 1 }}>
                  <Text style={styles.alertDetails}>{alert.details}</Text>
                  <Text style={styles.alertMeta}>{alert.actor_name} · {formatDateTime(alert.timestamp)}</Text>
                </View>
                {alert.amount_paise ? <Text style={styles.alertAmount}>{money(alert.amount_paise)}</Text> : null}
              </View>
            ))}
          </Card>
        ) : (
          <Card><Text style={styles.note}>No alerts.</Text></Card>
        )}
      </View>
    </ScrollView>
  );
};

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  content: { paddingHorizontal: spacing.xl, paddingTop: spacing.sm, paddingBottom: spacing.xxxl },
  hero: { paddingVertical: spacing.lg },
  heroLabel: { ...typography.caption, color: colors.textSecondary },
  heroValue: { ...typography.display, fontSize: 40, lineHeight: 48, color: colors.text, marginTop: 2 },
  heroMeta: { ...typography.caption, color: colors.primary, fontFamily: fonts.semibold, marginTop: 4 },
  grid: { flexDirection: 'row', gap: spacing.md, marginBottom: spacing.md },
  block: { marginBottom: spacing.md },
  sizes: { ...typography.body, color: colors.textSecondary },
  note: { ...typography.caption, color: colors.textSecondary, marginBottom: spacing.md },
  section: { marginTop: spacing.lg, gap: spacing.sm },
  alertRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.md, padding: spacing.lg },
  alertDivider: { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.surfaceBorder },
  alertMarker: { width: 4, alignSelf: 'stretch', borderRadius: 2 },
  alertDetails: { ...typography.body, color: colors.text },
  alertMeta: { ...typography.caption, color: colors.textSecondary, marginTop: 2 },
  alertAmount: { ...typography.bodyBold, color: colors.text },
});
