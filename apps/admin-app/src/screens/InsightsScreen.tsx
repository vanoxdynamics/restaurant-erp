import React, { useMemo, useState } from 'react';
import { Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';
import { Card, colors, fonts, radii, SectionHeader, spacing, StatCard, typography } from '@repo/shared-ui';
import { formatPaiseToRupees, StaffRole } from '@repo/shared-utils';
import { ApiError, OverviewPeriod, OverviewPoint, OverviewSlice, useEntitlements, useOverview } from '@repo/api-client';
import { PaymentBreakdownChart } from '../components/SkiaCharts';
import { QueryState } from '../components/QueryState';
import { useAuthStore } from '../stores/authStore';

const PERIODS: { key: OverviewPeriod; label: string; versus: string }[] = [
  { key: 'today', label: 'Today', versus: 'yesterday' },
  { key: 'yesterday', label: 'Yesterday', versus: 'the day before' },
  { key: '7d', label: '7 days', versus: 'the 7 days before' },
  { key: '30d', label: '30 days', versus: 'the 30 days before' },
  { key: '90d', label: '90 days', versus: 'the 90 days before' },
];

const money = (paise: number) => formatPaiseToRupees(paise, { decimals: 0 });
const compact = (paise: number) => formatPaiseToRupees(paise, { compact: true, decimals: 0 });
const BAR_HEIGHT = 140;

/** "12% vs the 7 days before"; the tiles draw their own up/down arrow from `positive`. */
function changeText(change: number | null | undefined, versus: string): { text?: string; positive?: boolean; arrow?: string } {
  if (change === null || change === undefined) return {};
  return { text: `${Math.abs(change)}% vs ${versus}`, positive: change >= 0, arrow: change > 0 ? '▲' : change < 0 ? '▼' : '' };
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** "2026-11-09" -> "9 Nov"; hours and weekdays pass through. */
function shortLabel(label: string): string {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(label)) return label;
  const d = new Date(`${label}T00:00:00`);
  return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
}

/** More than a month of days becomes weeks, so each bar stays wide enough to read and tap on a phone. */
function byWeek(points: OverviewPoint[]): OverviewPoint[] {
  if (points.length <= 31) return points;
  const weeks: OverviewPoint[] = [];
  for (let i = 0; i < points.length; i += 7) {
    const chunk = points.slice(i, i + 7);
    weeks.push({
      label: `w/c ${shortLabel(chunk[0].label)}`,
      sales_paise: chunk.reduce((n, p) => n + p.sales_paise, 0),
      bills: chunk.reduce((n, p) => n + p.bills, 0),
    });
  }
  return weeks;
}

/**
 * Vertical bars, one hue. Tap a bar to read its exact value; the biggest bar is named in the subtitle. With many bars only
 * every few labels are drawn so they never collide.
 */
const BarChart: React.FC<{ title: string; points: OverviewPoint[]; note?: string }> = ({ title, points, note }) => {
  const [selected, setSelected] = useState<number | null>(null);
  const max = Math.max(...points.map((p) => p.sales_paise), 1);
  const peak = points.reduce((best, p, i) => (p.sales_paise > points[best].sales_paise ? i : best), 0);
  const labelEvery = points.length > 14 ? Math.ceil(points.length / 7) : points.length > 8 ? 2 : 1;
  const shown = selected ?? peak;
  const point = points[shown];

  return (
    <Card style={styles.block}>
      <SectionHeader
        title={title}
        subtitle={point && point.sales_paise > 0
          ? `${selected === null ? 'Best: ' : ''}${shortLabel(point.label)} · ${money(point.sales_paise)} · ${plural(point.bills, 'bill', 'bills')}`
          : note}
      />
      <View style={styles.bars} accessibilityRole="image" accessibilityLabel={`${title}: ${points.map((p) => `${shortLabel(p.label)} ${money(p.sales_paise)}`).join(', ')}`}>
        {points.map((p, i) => {
          const height = p.sales_paise > 0 ? Math.max((p.sales_paise / max) * BAR_HEIGHT, 4) : 2;
          const active = i === shown && p.sales_paise > 0;
          return (
            <Pressable
              key={`${p.label}-${i}`}
              onPress={() => setSelected(selected === i ? null : i)}
              style={styles.barColumn}
              hitSlop={{ top: 8, bottom: 8 }}
              accessibilityLabel={`${shortLabel(p.label)}: ${money(p.sales_paise)}, ${p.bills} bills`}
            >
              <View style={[styles.bar, { height, backgroundColor: active ? colors.primary : colors.primaryLight }]} />
              <Text style={[styles.barLabel, active ? styles.barLabelActive : null]} numberOfLines={1}>
                {i % labelEvery === 0 || i === points.length - 1 ? shortLabel(p.label).replace(':00', '').replace('w/c ', '') : ''}
              </Text>
            </Pressable>
          );
        })}
      </View>
    </Card>
  );
};

/** Horizontal ranked bars: what earned the most, with its share of the total. */
const RankedBars: React.FC<{ title: string; rows: OverviewSlice[]; countLabel: [string, string]; total: number }> = ({ title, rows, countLabel, total }) => {
  if (!rows.length) return null;
  const max = Math.max(...rows.map((r) => r.amount_paise), 1);
  return (
    <Card style={styles.block}>
      <SectionHeader title={title} />
      <View style={{ gap: spacing.md }}>
        {rows.map((row) => (
          <View key={row.name} accessibilityLabel={`${row.name}: ${money(row.amount_paise)}`}>
            <View style={styles.rankHead}>
              <Text style={styles.rankName} numberOfLines={1}>{row.name}</Text>
              <Text style={styles.rankValue}>{money(row.amount_paise)}</Text>
            </View>
            <View style={styles.track}>
              <View style={[styles.fill, { width: `${Math.max((row.amount_paise / max) * 100, 2)}%` }]} />
            </View>
            <Text style={styles.rankMeta}>
              {total > 0 ? `${Math.round((row.amount_paise / total) * 100)}% · ` : ''}{plural(row.count, countLabel[0], countLabel[1])}
            </Text>
          </View>
        ))}
      </View>
    </Card>
  );
};

/**
 * The owner's view of the business: how a period went against the one before, when the money comes in, and what it comes
 * from. Owners with several branches can look at all of them together.
 */
export const InsightsScreen: React.FC = () => {
  const { currentUser, activeBranchId, activeBranchName, branches } = useAuthStore();
  const entitlements = useEntitlements();
  const [period, setPeriod] = useState<OverviewPeriod>('7d');
  const [allBranches, setAllBranches] = useState(false);
  const owner = currentUser?.role === StaffRole.OWNER;
  const query = useOverview(activeBranchId, period, owner && allBranches);
  const data = query.data;
  const versus = PERIODS.find((p) => p.key === period)!.versus;
  const noAnalytics = entitlements.data && !entitlements.data.modules.includes('analytics');

  const sales = data?.totals.net_sales_paise ?? 0;
  const salesChange = useMemo(() => changeText(data?.changes.net_sales, versus), [data, versus]);
  const billsChange = changeText(data?.changes.bills, versus);
  const avgChange = changeText(data?.changes.average_bill, versus);
  const forbidden = query.error instanceof ApiError && query.error.status === 403;

  return (
    <ScrollView
      style={styles.container}
      contentContainerStyle={styles.content}
      refreshControl={<RefreshControl refreshing={query.isRefetching} onRefresh={() => void query.refetch()} tintColor={colors.primary} />}
    >
      <Text style={[typography.h1, { color: colors.text }]}>Insights</Text>
      <Text style={styles.scope}>
        {owner && allBranches ? `All ${branches.length} branches` : activeBranchName}
        {data ? ` · ${shortLabel(data.start)}${data.start !== data.end ? ` – ${shortLabel(data.end)}` : ''}` : ''}
      </Text>

      <View style={styles.filters}>
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: spacing.sm }}>
          {PERIODS.map((p) => (
            <Pressable key={p.key} onPress={() => setPeriod(p.key)} accessibilityRole="radio" accessibilityState={{ selected: period === p.key }}
              style={[styles.chip, period === p.key ? styles.chipActive : null]}>
              <Text style={[styles.chipText, period === p.key ? styles.chipTextActive : null]}>{p.label}</Text>
            </Pressable>
          ))}
          {owner && branches.length > 1 ? (
            <Pressable onPress={() => setAllBranches(!allBranches)} accessibilityRole="switch" accessibilityState={{ checked: allBranches }}
              style={[styles.chip, allBranches ? styles.chipActive : null]}>
              <Text style={[styles.chipText, allBranches ? styles.chipTextActive : null]}>All branches</Text>
            </Pressable>
          ) : null}
        </ScrollView>
      </View>

      {noAnalytics || forbidden ? (
        <Card style={styles.block}>
          <Text style={styles.empty}>
            {noAnalytics ? 'Insights are part of the Analytics module. Ask Vanox to switch it on for your business.' : 'Sign in with your password to see insights.'}
          </Text>
        </Card>
      ) : (
        <QueryState query={query}>
          {data ? (
            <>
              <View style={styles.hero}>
                <Text style={styles.heroLabel}>Sales</Text>
                <Text style={styles.heroValue}>{money(sales)}</Text>
                {salesChange.text ? (
                  <Text style={[styles.heroChange, { color: salesChange.positive ? colors.success : colors.danger }]}>{salesChange.arrow} {salesChange.text}</Text>
                ) : (
                  <Text style={styles.heroChangeMuted}>Nothing to compare with yet</Text>
                )}
              </View>

              <View style={styles.grid}>
                <StatCard title="Bills" value={`${data.totals.bills}`} trend={billsChange.text} trendPositive={billsChange.positive} style={styles.tile} />
                <StatCard title="Average bill" value={money(data.totals.average_bill_paise)} trend={avgChange.text} trendPositive={avgChange.positive} style={styles.tile} />
              </View>
              <View style={styles.grid}>
                {data.totals.guests > 0 ? (
                  <StatCard title="Dine-in guests" value={`${data.totals.guests}`}
                    subtitle={data.totals.guests ? `${money(Math.round(data.totals.net_sales_paise / data.totals.guests))} per guest` : undefined} style={styles.tile} />
                ) : (
                  <StatCard title="Tax collected" value={money(data.totals.tax_paise)} style={styles.tile} />
                )}
                <StatCard title="Discounts given" value={money(data.totals.discounts_paise)}
                  subtitle={data.totals.gross_sales_paise ? `${((data.totals.discounts_paise / data.totals.gross_sales_paise) * 100).toFixed(1)}% of sales` : undefined} style={styles.tile} />
              </View>
              <StatCard title="Cancelled and voided" value={`${data.totals.cancellations}`}
                subtitle={`${money(data.totals.cancelled_paise)} taken off orders and bills`} style={styles.block} />

              {data.totals.bills === 0 ? (
                <Card style={styles.block}><Text style={styles.empty}>No paid bills in this period yet.</Text></Card>
              ) : (
                <>
                  <BarChart
                    title={data.trend.length && data.trend[0].label.includes(':') ? 'Sales by hour' : data.trend.length > 31 ? 'Sales by week' : 'Sales by day'}
                    points={byWeek(data.trend)}
                  />
                  {period !== 'today' && period !== 'yesterday' && data.by_hour.length > 1 ? (
                    <BarChart title="Busiest hours" points={data.by_hour} />
                  ) : null}
                  {data.by_weekday.length ? <BarChart title="By day of the week" points={data.by_weekday} /> : null}
                  <RankedBars title="Categories" rows={data.categories} countLabel={['line sold', 'lines sold']} total={data.categories.reduce((n, c) => n + c.amount_paise, 0)} />
                  <RankedBars
                    title="Best sellers"
                    rows={data.top_items.map((i) => ({ name: i.name, amount_paise: i.total_paise, count: i.quantity }))}
                    countLabel={['sold', 'sold']}
                    total={data.categories.reduce((n, c) => n + c.amount_paise, 0)}
                  />
                  <RankedBars title="Sales by staff" rows={data.staff} countLabel={['bill', 'bills']} total={sales} />
                  {data.order_types.length > 1 ? <RankedBars title="Order types" rows={data.order_types} countLabel={['bill', 'bills']} total={sales} /> : null}
                  {data.payments.total_paise > 0 ? <PaymentBreakdownChart breakdown={data.payments} /> : null}
                  <Text style={styles.footnote}>
                    Sales are paid bills including tax, by business day. Compared with {versus}: {compact(data.previous.net_sales_paise)} from {plural(data.previous.bills, 'bill', 'bills')}.
                  </Text>
                </>
              )}
            </>
          ) : null}
        </QueryState>
      )}
    </ScrollView>
  );
};

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  content: { paddingHorizontal: spacing.xl, paddingTop: spacing.md, paddingBottom: spacing.xxxl, gap: spacing.md },
  scope: { ...typography.caption, color: colors.textSecondary, marginTop: -spacing.sm },
  filters: { marginBottom: spacing.xs },
  chip: {
    minHeight: 36, paddingHorizontal: spacing.md, borderRadius: radii.full, borderWidth: 1, borderColor: colors.surfaceBorder,
    backgroundColor: colors.surface, justifyContent: 'center',
  },
  chipActive: { backgroundColor: colors.primaryGlow, borderColor: colors.primary },
  chipText: { ...typography.caption, color: colors.textSecondary },
  chipTextActive: { color: colors.primary, fontFamily: fonts.semibold },
  hero: { backgroundColor: colors.surface, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.surfaceBorder, padding: spacing.lg },
  heroLabel: { ...typography.caption, color: colors.textSecondary },
  heroValue: { ...typography.display, color: colors.text, fontFamily: fonts.bold },
  heroChange: { ...typography.bodyBold, marginTop: spacing.xs },
  heroChangeMuted: { ...typography.caption, color: colors.textMuted, marginTop: spacing.xs },
  grid: { flexDirection: 'row', gap: spacing.md },
  tile: { flex: 1 },
  block: { marginTop: 0 },
  bars: { flexDirection: 'row', alignItems: 'flex-end', height: BAR_HEIGHT + 24, gap: 2, marginTop: spacing.sm },
  barColumn: { flex: 1, alignItems: 'center', justifyContent: 'flex-end' },
  bar: { width: '80%', maxWidth: 28, borderTopLeftRadius: 4, borderTopRightRadius: 4 },
  barLabel: { ...typography.caption, fontSize: 10, color: colors.textMuted, marginTop: 4, height: 14 },
  barLabelActive: { color: colors.text, fontFamily: fonts.semibold },
  rankHead: { flexDirection: 'row', justifyContent: 'space-between', gap: spacing.md },
  rankName: { ...typography.body, color: colors.text, flex: 1 },
  rankValue: { ...typography.bodyBold, color: colors.text },
  track: { height: 8, borderRadius: 4, backgroundColor: colors.surfaceLight, marginTop: 4, overflow: 'hidden' },
  fill: { height: 8, borderRadius: 4, backgroundColor: colors.primary },
  rankMeta: { ...typography.caption, color: colors.textSecondary, marginTop: 2 },
  empty: { ...typography.body, color: colors.textSecondary, textAlign: 'center', padding: spacing.lg },
  footnote: { ...typography.caption, color: colors.textMuted, textAlign: 'center' },
});
