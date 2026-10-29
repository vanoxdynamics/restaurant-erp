import React, { useMemo, useState } from 'react';
import { RefreshControl, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { colors, fonts, radii, spacing, typography } from '@repo/shared-ui';
import { TableStatus } from '@repo/shared-utils';
import { DiningTable, useFloorLayout } from '@repo/api-client';
import { TableCard } from '../components/TableCard';
import { QueryState } from '../components/QueryState';
import { useAuthStore } from '../stores/authStore';

const LEGEND: { status: TableStatus; label: string; color: string }[] = [
  { status: TableStatus.AVAILABLE, label: 'Available', color: colors.available },
  { status: TableStatus.OCCUPIED, label: 'Occupied', color: colors.occupied },
  { status: TableStatus.BILLED, label: 'Billed', color: colors.billed },
  { status: TableStatus.RESERVED, label: 'Reserved', color: colors.dirty },
];

/** Read-only floor view: occupancy and running bills. Seating and transfers happen on the POS. */
export const FloorObserverScreen: React.FC = () => {
  const { activeBranchId } = useAuthStore();
  const query = useFloorLayout(activeBranchId);
  const floors = query.data ?? [];
  const [area, setArea] = useState<string>('ALL');

  const allTables: DiningTable[] = useMemo(() => floors.flatMap((f) => f.tables), [floors]);
  const visibleTables = area === 'ALL' ? allTables : allTables.filter((t) => t.section === area);
  const inUse = allTables.filter((t) => t.status !== TableStatus.AVAILABLE).length;

  return (
    <ScrollView
      style={styles.container}
      contentContainerStyle={styles.content}
      refreshControl={
        <RefreshControl refreshing={query.isRefetching} onRefresh={query.refetch} tintColor={colors.primary} />
      }
    >
      <View style={styles.header}>
        <Text style={[typography.h1, { color: colors.text }]}>Floor</Text>
        {allTables.length > 0 ? (
          <Text style={[typography.caption, { color: colors.textSecondary }]}>
            {inUse} of {allTables.length} tables occupied
          </Text>
        ) : null}
      </View>

      <QueryState query={query} isEmpty={allTables.length === 0} emptyText="No tables set up for this branch.">
        {floors.length > 1 ? (
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.pills}>
            {['ALL', ...floors.map((f) => f.name)].map((name) => {
              const selected = area === name;
              return (
                <TouchableOpacity
                  key={name}
                  onPress={() => setArea(name)}
                  style={[styles.pill, selected ? styles.pillActive : null]}
                  accessibilityRole="tab"
                  accessibilityState={{ selected }}
                >
                  <Text style={[styles.pillText, selected ? styles.pillTextActive : null]}>
                    {name === 'ALL' ? 'All areas' : name}
                  </Text>
                </TouchableOpacity>
              );
            })}
          </ScrollView>
        ) : null}

        <View style={styles.legend}>
          {LEGEND.map((entry) => (
            <View key={entry.status} style={styles.legendItem}>
              <View style={[styles.legendDot, { backgroundColor: entry.color }]} />
              <Text style={styles.legendText}>
                {entry.label} ({allTables.filter((t) => t.status === entry.status).length})
              </Text>
            </View>
          ))}
        </View>

        <View style={styles.grid}>
          {visibleTables.map((table) => (
            <TableCard key={table.id} table={table} />
          ))}
        </View>
      </QueryState>
    </ScrollView>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.background,
  },
  content: {
    paddingHorizontal: spacing.xl,
    paddingTop: spacing.md,
    paddingBottom: spacing.xxxl,
  },
  header: {
    marginBottom: spacing.lg,
  },
  pills: {
    gap: spacing.sm,
    paddingBottom: spacing.md,
  },
  pill: {
    minHeight: 36,
    paddingHorizontal: spacing.md,
    borderRadius: radii.full,
    borderWidth: 1,
    borderColor: colors.surfaceBorder,
    backgroundColor: colors.surface,
    justifyContent: 'center',
  },
  pillActive: {
    backgroundColor: colors.primaryGlow,
    borderColor: colors.primary,
  },
  pillText: {
    ...typography.caption,
    color: colors.textSecondary,
  },
  pillTextActive: {
    color: colors.primary,
    fontFamily: fonts.semibold,
  },
  legend: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.md,
    marginBottom: spacing.lg,
  },
  legendItem: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  legendDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    marginRight: 6,
  },
  legendText: {
    ...typography.caption,
    color: colors.textSecondary,
  },
  grid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.md,
  },
});
