import React from 'react';
import {
  View,
  Text,
  ScrollView,
  StyleSheet,
  TouchableOpacity,
  RefreshControl,
  Share,
} from 'react-native';
import {
  colors,
  radii,
  spacing,
  typography,
  Button,
  StatCard,
  fonts,
} from '@repo/shared-ui';
import {
  formatPaiseToRupees,
  formatDateTime,
} from '@repo/shared-utils';
import { useZReport } from '@repo/api-client';
import { PaymentBreakdownChart, TopSellingRankingChart } from '../components/SkiaCharts';
import { useAuthStore } from '../stores/authStore';

export const ZReportsScreen: React.FC = () => {
  const { activeBranchId, activeBranchName } = useAuthStore();
  const reportQuery = useZReport(activeBranchId);
  const { data: zReport, refetch, isRefetching } = reportQuery;
  const selectedDate = zReport?.report_date ?? '';

  const handleShareReport = async () => {
    if (!zReport) return;
    try {
      await Share.share({
        title: `End-of-day report ${selectedDate} - ${activeBranchName}`,
        message: `End-of-day report (${selectedDate})\nBranch: ${activeBranchName}\nGross sales: ${formatPaiseToRupees(zReport.gross_sales_paise)}\nNet sales: ${formatPaiseToRupees(zReport.net_sales_paise)}\nGST (CGST + SGST): ${formatPaiseToRupees(zReport.total_tax_paise)}\nBills paid: ${zReport.total_bills_count}\nPayment methods:\n• UPI: ${formatPaiseToRupees(zReport.payment_breakdown.upi_paise)}\n• Card: ${formatPaiseToRupees(zReport.payment_breakdown.card_paise)}\n• Cash: ${formatPaiseToRupees(zReport.payment_breakdown.cash_paise)}`,
      });
    } catch (e) {
      console.warn(e);
    }
  };

  return (
    <ScrollView
      style={styles.container}
      contentContainerStyle={styles.contentContainer}
      refreshControl={
        <RefreshControl
          refreshing={isRefetching}
          onRefresh={() => void refetch()}
          tintColor={colors.cyan}
        />
      }
    >
      {/* Header */}
      <View style={styles.topHeader}>
        <View>
          <Text style={[typography.h1, { color: colors.text }]}>End-of-day report</Text>
        </View>

        <Button
          title="Share"
          size="sm"
          variant="outline"
          onPress={handleShareReport}
        />
      </View>

      {/* Date Switcher */}
      <View style={styles.dateSelectorRow}>
        <View style={styles.dateInfo}>
          <Text style={styles.dateLabel}>Business day</Text>
          <Text style={styles.dateValue}>{selectedDate}</Text>
        </View>
      </View>

      {/* Financial Executive Summary Cards */}
      <View style={styles.card}>
        <Text style={styles.cardTitle}>Sales</Text>

        <View style={styles.summaryRow}>
          <Text style={styles.summaryLabel}>Gross sales</Text>
          <Text style={styles.summaryValue}>
            {formatPaiseToRupees(zReport?.gross_sales_paise ?? 0)}
          </Text>
        </View>

        <View style={styles.summaryRow}>
          <Text style={styles.summaryLabel}>Discounts</Text>
          <Text style={[styles.summaryValue, { color: colors.danger }]}>
            -{formatPaiseToRupees(zReport?.total_discounts_paise ?? 0)}
          </Text>
        </View>

        <View style={[styles.summaryRow, styles.dividerRow]}>
          <Text style={[styles.summaryLabel, { fontFamily: fonts.bold, color: colors.text }]}>
            Net sales
          </Text>
          <Text style={[styles.summaryValue, { fontFamily: fonts.bold, color: colors.cyan }]}>
            {formatPaiseToRupees(zReport?.net_sales_paise ?? 0)}
          </Text>
        </View>

        {/* GST Tax Liability Split */}
        <View style={styles.taxSplitBox}>
          <Text style={styles.taxSplitTitle}>GST collected</Text>
          <View style={styles.taxSplitRow}>
            <View style={styles.taxSplitCol}>
              <Text style={styles.taxSplitLabel}>CGST (2.5%)</Text>
              <Text style={styles.taxSplitVal}>
                {formatPaiseToRupees(zReport?.total_tax_cgst_paise ?? 0)}
              </Text>
            </View>
            <View style={styles.taxSplitCol}>
              <Text style={styles.taxSplitLabel}>SGST (2.5%)</Text>
              <Text style={styles.taxSplitVal}>
                {formatPaiseToRupees(zReport?.total_tax_sgst_paise ?? 0)}
              </Text>
            </View>
            <View style={styles.taxSplitCol}>
              <Text style={styles.taxSplitLabel}>Total GST (5%)</Text>
              <Text style={[styles.taxSplitVal, { color: colors.available }]}>
                {formatPaiseToRupees(zReport?.total_tax_paise ?? 0)}
              </Text>
            </View>
          </View>
        </View>

        <View style={styles.summaryRow}>
          <Text style={styles.summaryLabel}>Bills paid</Text>
          <Text style={styles.summaryValue}>{zReport?.total_bills_count ?? 0} bills</Text>
        </View>

        <View style={styles.summaryRow}>
          <Text style={styles.summaryLabel}>Average bill</Text>
          <Text style={styles.summaryValue}>
            {formatPaiseToRupees(zReport?.average_bill_value_paise ?? 0)}
          </Text>
        </View>

        <View style={[styles.summaryRow, { borderBottomWidth: 0 }]}>
          <Text style={styles.summaryLabel}>Cancelled orders</Text>
          <Text style={[styles.summaryValue, { color: colors.danger }]}>
            {zReport?.voided_orders_count ?? 0}
          </Text>
        </View>
      </View>

      {/* Payment Settlement Mode Breakdown Chart */}
      {zReport && zReport.payment_breakdown.total_paise > 0 && (
        <PaymentBreakdownChart breakdown={zReport.payment_breakdown} />
      )}

      {/* Top Selling Items Table */}
      {zReport && zReport.top_selling_items.length > 0 && (
        <TopSellingRankingChart items={zReport.top_selling_items} />
      )}
    </ScrollView>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.background,
  },
  contentContainer: {
    padding: spacing.lg,
    paddingBottom: 100,
  },
  topHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    marginBottom: spacing.md,
  },
  dateSelectorRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    backgroundColor: colors.surface,
    borderRadius: radii.md,
    borderWidth: 1,
    borderColor: colors.surfaceBorder,
    padding: spacing.md,
    marginBottom: spacing.lg,
  },
  dateInfo: {},
  dateLabel: {
    fontSize: 12,
    color: colors.textMuted,
    fontFamily: fonts.bold,
    marginBottom: 2,
  },
  dateValue: {
    fontSize: 15,
    fontFamily: fonts.bold,
    color: colors.text,
  },
  card: {
    backgroundColor: colors.surface,
    borderRadius: radii.lg,
    borderWidth: 1,
    borderColor: colors.surfaceBorder,
    padding: spacing.lg,
    marginBottom: spacing.lg,
  },
  cardTitle: {
    fontSize: 12,
    fontFamily: fonts.bold,
    color: colors.textSecondary,
    marginBottom: spacing.md,
    letterSpacing: 0.5,
  },
  summaryRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: spacing.sm,
    borderBottomWidth: 1,
    borderBottomColor: colors.surfaceBorderLight,
  },
  dividerRow: {
    borderTopWidth: 1,
    borderTopColor: colors.surfaceBorder,
    borderBottomWidth: 1,
    borderBottomColor: colors.surfaceBorder,
    marginVertical: 4,
    paddingVertical: spacing.md,
  },
  summaryLabel: {
    fontFamily: fonts.regular,
    fontSize: 13,
    color: colors.textSecondary,
  },
  summaryValue: {
    fontSize: 14,
    fontFamily: fonts.bold,
    color: colors.text,
  },
  taxSplitBox: {
    backgroundColor: colors.surfaceLight,
    borderRadius: radii.md,
    padding: spacing.md,
    marginVertical: spacing.sm,
  },
  taxSplitTitle: {
    fontSize: 12,
    fontFamily: fonts.bold,
    color: colors.textMuted,
    marginBottom: spacing.sm,
  },
  taxSplitRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
  },
  taxSplitCol: {
    alignItems: 'center',
    flex: 1,
  },
  taxSplitLabel: {
    fontFamily: fonts.regular,
    fontSize: 12,
    color: colors.textSecondary,
    marginBottom: 2,
  },
  taxSplitVal: {
    fontSize: 14,
    fontFamily: fonts.bold,
    color: colors.text,
  },
});
