import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { colors, fonts, radii, spacing, typography } from '@repo/shared-ui';
import { formatPaiseToRupees } from '@repo/shared-utils';
import { Icon } from './Icon';

export interface CartBarProps {
  count: number;
  totalPaise: number;
  onPress: () => void;
}

/**
 * Sticky summary under the menu on phones, where the order itself is on another tab: it shows what
 * has been picked so far and takes the waiter to it in one tap.
 */
export const CartBar: React.FC<CartBarProps> = ({ count, totalPaise, onPress }) => (
  <View style={styles.wrap}>
    <Pressable
      onPress={onPress}
      accessibilityRole="button"
      accessibilityLabel={`View order, ${count} new ${count === 1 ? 'item' : 'items'}, ${formatPaiseToRupees(totalPaise, { decimals: 0 })}`}
      style={({ pressed }) => [styles.bar, pressed ? { backgroundColor: colors.primaryDark } : null]}
    >
      <View style={styles.count}>
        <Text style={styles.countText}>{count}</Text>
      </View>
      <Text style={styles.label}>{count === 1 ? 'item added' : 'items added'}</Text>
      <Text style={styles.total}>{formatPaiseToRupees(totalPaise, { decimals: 0 })}</Text>
      <Text style={styles.view}>View order</Text>
      <Icon name="chevron-right" size={18} color={colors.textInverse} />
    </Pressable>
  </View>
);

const styles = StyleSheet.create({
  wrap: {
    padding: spacing.md,
    paddingTop: spacing.sm,
    backgroundColor: colors.background,
  },
  bar: {
    minHeight: 56,
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    paddingHorizontal: spacing.lg,
    borderRadius: radii.lg,
    backgroundColor: colors.primary,
  },
  count: {
    minWidth: 28,
    height: 28,
    borderRadius: 14,
    paddingHorizontal: spacing.xs,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.textInverse,
  },
  countText: { ...typography.bodyBold, color: colors.primary, fontFamily: fonts.bold },
  label: { ...typography.body, color: colors.textInverse, flex: 1 },
  total: { ...typography.bodyBold, color: colors.textInverse },
  view: { ...typography.bodyBold, color: colors.textInverse },
});
