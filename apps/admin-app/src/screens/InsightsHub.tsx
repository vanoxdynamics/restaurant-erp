import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { colors, fonts, radii, spacing } from '@repo/shared-ui';
import { useEntitlements } from '@repo/api-client';
import { DayReport as ApparelDayReport, ProductSales as ApparelProductSales } from './apparel/ReportsScreen';
import { InsightsScreen } from './InsightsScreen';
import { ZReportsScreen } from './ZReports';
import { CustomersScreen } from './CustomersScreen';

export type InsightsView = 'overview' | 'reports' | 'customers';

/** Insights: the end-of-day reports (Daily reports), the business over time (Trends) and who buys (Customers), one switch apart. */
export const InsightsHub: React.FC<{ view: InsightsView; onChangeView: (view: InsightsView) => void }> = ({ view, onChangeView }) => {
  // A dress shop's reports come from its own module: the day report replaces the Z-report, and sales by product and size over a
  // date range replace the trends, which read restaurant orders and bills.
  const apparel = useEntitlements().data?.category === 'apparel';
  return (
  <View style={styles.container}>
    <View style={styles.segmentWrapper}>
      <View style={styles.segmented} accessibilityRole="tablist">
        {([
          ['reports', 'Daily reports'],
          ['overview', apparel ? 'Sales by product' : 'Trends'],
          ['customers', 'Customers'],
        ] as const).map(([key, label]) => (
          <Pressable
            key={key}
            onPress={() => onChangeView(key)}
            accessibilityRole="tab"
            accessibilityState={{ selected: view === key }}
            style={[styles.segment, view === key ? styles.segmentActive : null]}
          >
            <Text style={[styles.segmentText, view === key ? styles.segmentTextActive : null]}>{label}</Text>
          </Pressable>
        ))}
      </View>
    </View>
    <View style={{ flex: 1 }}>{view === 'overview'
      ? (apparel ? <ApparelProductSales /> : <InsightsScreen />)
      : view === 'reports' ? (apparel ? <ApparelDayReport /> : <ZReportsScreen />) : <CustomersScreen />}</View>
  </View>
  );
};

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  segmentWrapper: { paddingHorizontal: spacing.xl, paddingTop: spacing.md },
  segmented: { flexDirection: 'row', backgroundColor: colors.surfaceLight, borderRadius: radii.md, padding: 3 },
  segment: { flex: 1, minHeight: 36, alignItems: 'center', justifyContent: 'center', borderRadius: radii.sm },
  segmentActive: { backgroundColor: colors.surface },
  segmentText: { fontSize: 13, fontFamily: fonts.medium, color: colors.textSecondary },
  segmentTextActive: { color: colors.text, fontFamily: fonts.semibold },
});
