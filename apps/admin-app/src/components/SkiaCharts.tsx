import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { Card, colors, radii, SectionHeader, spacing, typography, fonts } from '@repo/shared-ui';
import { formatPaiseToRupees } from '@repo/shared-utils';

export interface HourlySalesPoint {
  hour: string;
  sales_paise: number;
  orders_count: number;
}

export interface PaymentBreakdownData {
  cash_paise: number;
  card_paise: number;
  upi_paise: number;
  total_paise: number;
}

export interface TopItemData {
  name: string;
  quantity: number;
  total_sales_paise: number;
  category_name: string;
}

const BAR_MAX_HEIGHT = 120;

/** One distinct, colour-blind-friendly hue per tender. */
const TENDER_COLORS = {
  upi: colors.primary,
  card: colors.accent,
  cash: '#64748b',
};

/** How many of the latest hours the chart shows. */
const HOURS_SHOWN = 7;

/**
 * Hourly sales for the latest 7 hours (up to the current hour). Every hour is listed so the day reads left to right, but only
 * hours with sales get bars: money in one colour, number of orders in another. The busiest hour is called out in words.
 */
export const LiveSalesPulseChart: React.FC<{
  data: HourlySalesPoint[];
  velocityPaisePerHour?: number;
  /** "Orders" in a restaurant, "Bills" in a shop. */
  countLabel?: string;
}> = ({ data, countLabel = 'Orders' }) => {
  // The window ends at the current hour; early in the day it starts at midnight instead of reaching into yesterday.
  const start = Math.max(0, new Date().getHours() - (HOURS_SHOWN - 1));
  const slots: HourlySalesPoint[] = Array.from({ length: HOURS_SHOWN }, (_, i) => {
    const label = `${String(start + i).padStart(2, '0')}:00`;
    return data.find((d) => d.hour === label) ?? { hour: label, sales_paise: 0, orders_count: 0 };
  });

  const maxSales = Math.max(...slots.map((d) => d.sales_paise), 1);
  const maxOrders = Math.max(...slots.map((d) => d.orders_count), 1);
  const peak = slots.find((d) => d.sales_paise > 0 && d.sales_paise === maxSales);

  return (
    <Card style={styles.block}>
      <SectionHeader
        title="Sales by hour"
        subtitle={peak ? `Peak: ${peak.hour} · ${formatPaiseToRupees(peak.sales_paise, { decimals: 0 })}` : 'Last 7 hours'}
      />

      <View style={styles.legendRow}>
        <View style={styles.legendLabelRow}>
          <View style={[styles.legendDot, { backgroundColor: colors.primary }]} />
          <Text style={styles.legendLabel}>Sales (₹)</Text>
        </View>
        <View style={styles.legendLabelRow}>
          <View style={[styles.legendDot, { backgroundColor: colors.accent }]} />
          <Text style={styles.legendLabel}>{countLabel}</Text>
        </View>
      </View>

      <View style={styles.bars} accessibilityRole="image" accessibilityLabel={`Bar chart of sales and ${countLabel.toLowerCase()} by hour, last 7 hours`}>
        {slots.map((point) => {
          const isPeak = point === peak;
          const hasSales = point.sales_paise > 0 || point.orders_count > 0;
          const salesHeight = Math.max((point.sales_paise / maxSales) * BAR_MAX_HEIGHT, 4);
          const ordersHeight = Math.max((point.orders_count / maxOrders) * BAR_MAX_HEIGHT, 4);

          return (
            <View key={point.hour} style={styles.barColumn}>
              <View style={styles.barPair}>
                {hasSales ? (
                  <>
                    <View style={styles.barWithValue}>
                      <Text style={[styles.barValue, isPeak ? styles.barValuePeak : null]}>
                        {formatPaiseToRupees(point.sales_paise, { compact: true, showSymbol: false, decimals: 0 })}
                      </Text>
                      <View style={[styles.bar, { height: salesHeight, backgroundColor: colors.primary }]} />
                    </View>
                    <View style={styles.barWithValue}>
                      <Text style={styles.barValue}>{point.orders_count}</Text>
                      <View style={[styles.bar, { height: ordersHeight, backgroundColor: colors.accent }]} />
                    </View>
                  </>
                ) : null}
              </View>
              <Text style={[styles.hourLabel, isPeak ? styles.hourLabelPeak : null]}>{point.hour}</Text>
            </View>
          );
        })}
      </View>
    </Card>
  );
};

/**
 * How guests paid today, as one stacked bar plus a legend.
 */
export const PaymentBreakdownChart: React.FC<{ breakdown: PaymentBreakdownData }> = ({ breakdown }) => {
  const total = breakdown.total_paise || 1;
  const rows = [
    { key: 'upi', label: 'UPI', paise: breakdown.upi_paise, color: TENDER_COLORS.upi },
    { key: 'card', label: 'Card', paise: breakdown.card_paise, color: TENDER_COLORS.card },
    { key: 'cash', label: 'Cash', paise: breakdown.cash_paise, color: TENDER_COLORS.cash },
  ].map((row) => ({ ...row, percent: Math.round((row.paise / total) * 100) }));

  return (
    <Card style={styles.block}>
      <SectionHeader title="Payment methods" />

      <View style={styles.stack}>
        {rows.map((row) =>
          row.percent > 0 ? (
            <View key={row.key} style={{ flex: row.percent, backgroundColor: row.color }} />
          ) : null,
        )}
      </View>

      <View style={styles.legend}>
        {rows.map((row) => (
          <View key={row.key} style={styles.legendItem}>
            <View style={styles.legendLabelRow}>
              <View style={[styles.legendDot, { backgroundColor: row.color }]} />
              <Text style={styles.legendLabel}>
                {row.label} · {row.percent}%
              </Text>
            </View>
            <Text style={styles.legendValue}>{formatPaiseToRupees(row.paise, { decimals: 0 })}</Text>
          </View>
        ))}
      </View>
    </Card>
  );
};

/**
 * Best sellers by revenue.
 */
export const TopSellingRankingChart: React.FC<{ items: TopItemData[]; title?: string }> = ({ items, title = 'Top items' }) => {
  const maxSales = Math.max(...items.map((i) => i.total_sales_paise), 1);

  return (
    <Card style={styles.block}>
      <SectionHeader title={title} subtitle="By revenue" />
      {items.map((item, index) => {
        const widthPercent = `${Math.max(6, Math.round((item.total_sales_paise / maxSales) * 100))}%` as const;
        return (
          <View key={item.name} style={[styles.rankRow, index > 0 ? styles.rankDivider : null]}>
            <Text style={styles.rankNumber}>{index + 1}</Text>
            <View style={{ flex: 1 }}>
              <View style={styles.rankMeta}>
                <Text style={styles.rankName} numberOfLines={1}>
                  {item.name}
                </Text>
                <Text style={styles.rankSales}>{formatPaiseToRupees(item.total_sales_paise, { decimals: 0 })}</Text>
              </View>
              <View style={styles.rankTrack}>
                <View
                  style={[
                    styles.rankFill,
                    { width: widthPercent, backgroundColor: index === 0 ? colors.accent : colors.accentLight },
                  ]}
                />
              </View>
              <Text style={styles.rankQty}>
                {item.quantity} sold · {item.category_name}
              </Text>
            </View>
          </View>
        );
      })}
    </Card>
  );
};

const styles = StyleSheet.create({
  block: {
    marginBottom: spacing.md,
  },
  bars: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    justifyContent: 'space-between',
    paddingTop: spacing.sm,
  },
  barColumn: {
    flex: 1,
    alignItems: 'center',
  },
  barPair: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    justifyContent: 'center',
    gap: 3,
    height: BAR_MAX_HEIGHT + 20,
  },
  barWithValue: {
    alignItems: 'center',
    justifyContent: 'flex-end',
  },
  legendRow: {
    flexDirection: 'row',
    gap: spacing.lg,
    marginTop: spacing.xs,
  },
  barValue: {
    fontFamily: fonts.regular,
    fontSize: 11,
    color: colors.textMuted,
    marginBottom: 4,
  },
  barValuePeak: {
    color: colors.primary,
    fontFamily: fonts.bold,
  },
  bar: {
    width: 12,
    borderRadius: radii.sm,
  },
  hourLabel: {
    fontFamily: fonts.regular,
    fontSize: 12,
    color: colors.textMuted,
    marginTop: 6,
  },
  hourLabelPeak: {
    color: colors.text,
    fontFamily: fonts.bold,
  },
  stack: {
    flexDirection: 'row',
    height: 12,
    borderRadius: 6,
    overflow: 'hidden',
    gap: 2,
    backgroundColor: colors.surfaceLight,
  },
  legend: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginTop: spacing.lg,
  },
  legendItem: {
    flex: 1,
  },
  legendLabelRow: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  legendDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    marginRight: 6,
  },
  legendLabel: {
    ...typography.caption,
    color: colors.textSecondary,
  },
  legendValue: {
    ...typography.bodyBold,
    color: colors.text,
    marginTop: 2,
  },
  rankRow: {
    flexDirection: 'row',
    gap: spacing.md,
    paddingVertical: spacing.md,
  },
  rankDivider: {
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.surfaceBorder,
  },
  rankNumber: {
    ...typography.h3,
    width: 18,
    color: colors.textMuted,
  },
  rankMeta: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginBottom: 6,
  },
  rankName: {
    ...typography.bodyBold,
    color: colors.text,
    flex: 1,
    marginRight: spacing.sm,
  },
  rankSales: {
    ...typography.bodyBold,
    color: colors.text,
  },
  rankTrack: {
    height: 6,
    borderRadius: 3,
    backgroundColor: colors.surfaceLight,
    overflow: 'hidden',
  },
  rankFill: {
    height: '100%',
    borderRadius: 3,
  },
  rankQty: {
    ...typography.caption,
    color: colors.textMuted,
    marginTop: 6,
  },
});
