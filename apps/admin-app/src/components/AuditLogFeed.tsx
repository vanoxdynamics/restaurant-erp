import React from 'react';
import { View, Text, StyleSheet, FlatList } from 'react-native';
import { colors, radii, spacing, typography, Badge, CodeText, fonts } from '@repo/shared-ui';
import { formatPaiseToRupees, formatDateTime, wastageReasonLabel } from '@repo/shared-utils';
import { VoidApprovalItem } from '@repo/api-client';

export interface AuditLogFeedProps {
  logs: VoidApprovalItem[];
  maxItems?: number;
}

export const AuditLogFeed: React.FC<AuditLogFeedProps> = ({ logs, maxItems = 10 }) => {
  const displayLogs = logs.slice(0, maxItems);

  return (
    <View style={styles.container}>
      {displayLogs.length === 0 ? (
        <View style={styles.emptyContainer}>
          <Text style={{ color: colors.textMuted }}>No voids recorded.</Text>
        </View>
      ) : (
        <View style={styles.listContainer}>
          {displayLogs.map((log) => {
            const isApproved = log.status === 'APPROVED';
            const isRejected = log.status === 'REJECTED';
            const isPending = log.status === 'PENDING';

            return (
              <View key={log.id} style={styles.logCard}>
                <View style={styles.logTopRow}>
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: spacing.xs }}>
                    <CodeText style={styles.kotNumber}>KOT {log.kot_number.split('-').pop()}</CodeText>
                    <Badge label={`Table ${log.table_number}`} variant="neutral" size="sm" />
                  </View>
                  <Badge
                    label={log.status}
                    variant={isApproved ? 'available' : isRejected ? 'danger' : 'warning'}
                    size="sm"
                    pulse={isPending}
                  />
                </View>

                <View style={styles.itemRow}>
                  <Text style={styles.itemName}>
                    {log.quantity} × {log.item_name}
                  </Text>
                  <Text style={styles.itemAmount}>{formatPaiseToRupees(log.amount_paise, { decimals: 0 })}</Text>
                </View>

                {/* Mandatory Wastage Reason Badge */}
                <View style={styles.reasonBox}>
                  <Text style={styles.reasonLabel}>Reason</Text>
                  <Text style={styles.reasonText}>"{wastageReasonLabel(log.wastage_reason)}"</Text>
                </View>

                <View style={styles.logFooter}>
                  <Text style={styles.metaText}>
                    By: <Text style={{ color: colors.textSecondary }}>{log.requested_by_user_name}</Text>
                  </Text>
                  <Text style={styles.metaText}>{formatDateTime(log.requested_at)}</Text>
                </View>

                {log.reviewed_by_user_id && (
                  <View style={styles.authRow}>
                    <Text style={styles.authText}>
                      Approved by <Text style={{ color: colors.primaryLight }}>{log.reviewed_by_user_id}</Text>
                    </Text>
                  </View>
                )}
              </View>
            );
          })}
        </View>
      )}
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    backgroundColor: colors.surface,
    borderRadius: radii.lg,
    borderWidth: 1,
    borderColor: colors.surfaceBorder,
    padding: spacing.lg,
    marginBottom: spacing.lg,
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: spacing.md,
  },
  listContainer: {
    gap: spacing.md,
  },
  emptyContainer: {
    padding: spacing.xl,
    alignItems: 'center',
  },
  logCard: {
    backgroundColor: colors.surfaceLight,
    borderRadius: radii.md,
    borderWidth: 1,
    borderColor: colors.surfaceBorder,
    padding: spacing.md,
  },
  logTopRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: spacing.xs,
  },
  kotNumber: {
    fontSize: 12,
    fontFamily: fonts.bold,
  },
  itemRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginVertical: 4,
  },
  itemName: {
    fontSize: 14,
    fontFamily: fonts.bold,
    color: colors.text,
  },
  itemAmount: {
    fontSize: 14,
    fontFamily: fonts.bold,
    color: colors.text,
  },
  reasonBox: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(245, 158, 11, 0.08)',
    borderLeftWidth: 3,
    borderLeftColor: colors.warning,
    paddingVertical: 4,
    paddingHorizontal: spacing.sm,
    borderRadius: radii.xs,
    marginVertical: 6,
  },
  reasonLabel: {
    fontSize: 12,
    fontFamily: fonts.bold,
    color: colors.warning,
    marginRight: 6,
  },
  reasonText: {
    fontSize: 12,
    fontFamily: fonts.medium,
    color: colors.text,
    fontStyle: 'italic',
  },
  logFooter: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginTop: 4,
  },
  metaText: {
    fontFamily: fonts.regular,
    fontSize: 12,
    color: colors.textMuted,
  },
  authRow: {
    marginTop: 6,
    paddingTop: 6,
    borderTopWidth: 1,
    borderTopColor: colors.surfaceBorderLight,
  },
  authText: {
    fontSize: 12,
    color: colors.available,
    fontFamily: fonts.semibold,
  },
});
