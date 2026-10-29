import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { colors, radii, shadows, spacing, TableStatusBadge, typography } from '@repo/shared-ui';
import { formatPaiseToRupees, getOccupancyTime, TableStatus } from '@repo/shared-utils';
import { DiningTable } from '@repo/api-client';
import { Icon } from './Icon';

export interface TableCardProps {
  table: DiningTable;
}

const STATUS_TINT: Record<string, string> = {
  [TableStatus.AVAILABLE]: colors.available,
  [TableStatus.OCCUPIED]: colors.occupied,
  [TableStatus.BILLED]: colors.billed,
  [TableStatus.RESERVED]: colors.dirty,
};

/** Read-only table tile for the admin floor view. */
export const TableCard: React.FC<TableCardProps> = ({ table }) => {
  const busy = table.running_bill_paise !== undefined;
  const tint = STATUS_TINT[table.status] ?? colors.surfaceBorder;
  const meta = [
    table.occupied_at ? getOccupancyTime(table.occupied_at) : null,
    table.item_count ? `${table.item_count} items` : null,
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <View
      style={[styles.card, { borderTopColor: tint }]}
      accessible
      accessibilityLabel={`Table ${table.table_number}, ${table.status.toLowerCase()}${
        busy ? `, bill ${formatPaiseToRupees(table.running_bill_paise ?? 0, { decimals: 0 })}` : ''
      }`}
    >
      <View style={styles.topRow}>
        <Text style={styles.tableNumber}>{table.table_number}</Text>
        <TableStatusBadge status={table.status} size="sm" />
      </View>
      <View style={styles.seatsRow}>
        <Icon name="users" size={13} color={colors.textMuted} />
        <Text style={styles.seatsText}>{table.capacity} seats</Text>
      </View>

      {busy ? (
        <View style={styles.body}>
          <Text style={styles.amount}>{formatPaiseToRupees(table.running_bill_paise ?? 0, { decimals: 0 })}</Text>
          {meta ? <Text style={styles.meta}>{meta}</Text> : null}
        </View>
      ) : null}
    </View>
  );
};

const styles = StyleSheet.create({
  card: {
    flex: 1,
    minWidth: 150,
    backgroundColor: colors.surface,
    borderRadius: radii.lg,
    borderWidth: 1,
    borderColor: colors.surfaceBorder,
    borderTopWidth: 3,
    padding: spacing.md,
    ...shadows.card,
  },
  topRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  tableNumber: {
    ...typography.h2,
    color: colors.text,
  },
  seatsRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    marginTop: 2,
  },
  seatsText: {
    ...typography.caption,
    color: colors.textMuted,
  },
  body: {
    marginTop: spacing.md,
  },
  amount: {
    ...typography.h3,
    color: colors.text,
  },
  meta: {
    ...typography.caption,
    color: colors.textSecondary,
    marginTop: 2,
  },
});
