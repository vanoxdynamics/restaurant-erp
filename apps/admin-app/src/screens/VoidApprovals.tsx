import React, { useState } from 'react';
import { View, Text, ScrollView, StyleSheet, TouchableOpacity, RefreshControl } from 'react-native';
import { colors, radii, spacing, typography, shadows, Button, fonts } from '@repo/shared-ui';
import { formatPaiseToRupees, formatDateTime, VoidApprovalStatus, wastageReasonLabel } from '@repo/shared-utils';
import { ApiError, useVoidApprovals, useApproveVoidKOT, useRejectVoidKOT } from '@repo/api-client';
import { AuditLogFeed } from '../components/AuditLogFeed';
import { Icon } from '../components/Icon';
import { QueryState } from '../components/QueryState';
import { useAuthStore } from '../stores/authStore';

export const VoidApprovalsScreen: React.FC = () => {
  const { activeBranchId } = useAuthStore();
  const [activeTab, setActiveTab] = useState<'PENDING' | 'LOGS'>('PENDING');
  const pendingQuery = useVoidApprovals(activeBranchId, 'pending');
  const historyQuery = useVoidApprovals(activeBranchId);
  const approve = useApproveVoidKOT();
  const reject = useRejectVoidKOT();
  const [busyId, setBusyId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const pendingList = pendingQuery.data ?? [];
  const reviewedList = (historyQuery.data ?? []).filter((v) => v.status !== VoidApprovalStatus.PENDING);
  const activeQuery = activeTab === 'PENDING' ? pendingQuery : historyQuery;

  const review = async (id: string, action: 'approve' | 'reject') => {
    setBusyId(id);
    setActionError(null);
    try {
      await (action === 'approve' ? approve : reject).mutateAsync({ requestId: id });
    } catch (e) {
      setActionError(e instanceof ApiError ? e.message : 'Action failed. Try again.');
    } finally {
      setBusyId(null);
    }
  };

  return (
    <ScrollView
      style={styles.container}
      contentContainerStyle={styles.contentContainer}
      refreshControl={
        <RefreshControl refreshing={activeQuery.isRefetching} onRefresh={activeQuery.refetch} tintColor={colors.primary} />
      }
    >
      <View style={styles.topHeader}>
        <Text style={[typography.h1, { color: colors.text }]}>Approvals</Text>
        <Text style={[typography.caption, { color: colors.textSecondary }]}>
          {pendingList.length === 0 ? 'No pending requests' : `${pendingList.length} pending`}
        </Text>
      </View>

      <View style={styles.tabRow}>
        {([
          { key: 'PENDING', label: pendingList.length ? `Pending (${pendingList.length})` : 'Pending' },
          { key: 'LOGS', label: 'History' },
        ] as const).map((tab) => (
          <TouchableOpacity
            key={tab.key}
            style={[styles.tabButton, activeTab === tab.key ? styles.tabButtonActive : null]}
            onPress={() => setActiveTab(tab.key)}
            accessibilityRole="tab"
            accessibilityState={{ selected: activeTab === tab.key }}
          >
            <Text style={[styles.tabButtonText, activeTab === tab.key ? styles.tabButtonTextActive : null]}>
              {tab.label}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      {actionError ? <Text style={styles.actionError}>{actionError}</Text> : null}

      {activeTab === 'PENDING' ? (
        <QueryState query={pendingQuery}>
          <View style={styles.pendingContainer}>
            {pendingList.length === 0 ? (
              <View style={styles.emptyCard}>
                <View style={styles.emptyIcon}>
                  <Icon name="check" size={24} color={colors.primary} />
                </View>
                <Text style={styles.emptyTitle}>No pending requests</Text>
              </View>
            ) : (
              pendingList.map((item) => (
                <View key={item.id} style={styles.approvalCard}>
                  <View style={styles.approvalHeader}>
                    <Text style={styles.approvalTable}>
                      {item.table_number === 'Takeaway' ? 'Takeaway' : `Table ${item.table_number}`} · KOT {item.kot_number.split('-').pop()}
                    </Text>
                    <Text style={styles.requestedAt}>{formatDateTime(item.requested_at)}</Text>
                  </View>

                  <View style={styles.itemDetails}>
                    <Text style={styles.itemName}>
                      {item.quantity} × {item.item_name}
                    </Text>
                    <Text style={styles.itemAmount}>{formatPaiseToRupees(item.amount_paise, { decimals: 0 })}</Text>
                  </View>

                  <View style={styles.reasonBadge}>
                    <Icon name="message-square" size={14} color={colors.textSecondary} />
                    <Text style={styles.reasonText}>
                      {wastageReasonLabel(item.wastage_reason)} · {item.requested_by_user_name}
                    </Text>
                  </View>

                  <View style={styles.actionButtonsRow}>
                    <Button
                      title="Reject"
                      variant="outline"
                      style={{ flex: 1 }}
                      disabled={busyId !== null}
                      loading={busyId === item.id && reject.isPending}
                      onPress={() => review(item.id, 'reject')}
                      accessibilityLabel={`Reject void for ${item.item_name}`}
                    />
                    <Button
                      title="Approve"
                      variant="primary"
                      style={{ flex: 1.4 }}
                      disabled={busyId !== null}
                      loading={busyId === item.id && approve.isPending}
                      onPress={() => review(item.id, 'approve')}
                      accessibilityLabel={`Approve void for ${item.item_name}`}
                    />
                  </View>
                </View>
              ))
            )}
          </View>
        </QueryState>
      ) : (
        <QueryState query={historyQuery}>
          <AuditLogFeed logs={reviewedList} />
        </QueryState>
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
    paddingHorizontal: spacing.xl,
    paddingTop: spacing.md,
    paddingBottom: spacing.xxxl,
  },
  topHeader: {
    marginBottom: spacing.lg,
  },
  actionError: {
    ...typography.caption,
    color: colors.danger,
    marginBottom: spacing.md,
  },
  tabRow: {
    flexDirection: 'row',
    backgroundColor: colors.surfaceLight,
    borderRadius: radii.md,
    padding: 3,
    marginBottom: spacing.lg,
  },
  tabButton: {
    flex: 1,
    minHeight: 38,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: radii.sm + 2,
  },
  tabButtonActive: {
    backgroundColor: colors.surface,
    ...shadows.card,
  },
  tabButtonText: {
    ...typography.caption,
    fontFamily: fonts.semibold,
    color: colors.textSecondary,
  },
  tabButtonTextActive: {
    color: colors.text,
  },
  pendingContainer: {
    gap: spacing.md,
  },
  emptyCard: {
    alignItems: 'center',
    paddingVertical: spacing.xxxl,
    paddingHorizontal: spacing.xl,
  },
  emptyIcon: {
    width: 56,
    height: 56,
    borderRadius: 28,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.primaryGlow,
    marginBottom: spacing.md,
  },
  emptyTitle: {
    ...typography.h3,
    color: colors.text,
    marginBottom: spacing.xs,
  },
  emptySubtitle: {
    ...typography.body,
    color: colors.textSecondary,
    textAlign: 'center',
    maxWidth: 300,
  },
  approvalCard: {
    backgroundColor: colors.surface,
    borderRadius: radii.lg,
    padding: spacing.lg,
    borderWidth: 1,
    borderColor: colors.surfaceBorder,
    ...shadows.card,
  },
  approvalHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: spacing.xs,
  },
  approvalTable: {
    ...typography.caption,
    fontFamily: fonts.bold,
    color: colors.occupied,
  },
  requestedAt: {
    ...typography.caption,
    color: colors.textMuted,
  },
  itemDetails: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'baseline',
    gap: spacing.md,
  },
  itemName: {
    ...typography.h3,
    fontSize: 17,
    color: colors.text,
    flex: 1,
  },
  itemAmount: {
    ...typography.h3,
    fontSize: 17,
    color: colors.text,
  },
  reasonBadge: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: spacing.sm,
    backgroundColor: colors.surfaceLight,
    borderRadius: radii.md,
    paddingVertical: spacing.sm + 2,
    paddingHorizontal: spacing.md,
    marginTop: spacing.md,
  },
  reasonText: {
    ...typography.body,
    color: colors.text,
    flex: 1,
  },
  requestedByText: {
    ...typography.caption,
    color: colors.textMuted,
    marginTop: spacing.sm,
  },
  actionButtonsRow: {
    flexDirection: 'row',
    gap: spacing.md,
    marginTop: spacing.lg,
  },
});
