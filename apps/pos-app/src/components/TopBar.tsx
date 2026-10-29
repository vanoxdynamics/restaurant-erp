import React from 'react';
import { Pressable, StyleSheet, Text, useWindowDimensions, View } from 'react-native';
import { colors, fonts, radii, spacing, typography } from '@repo/shared-ui';
import { Icon } from './Icon';
import { useSessionStore } from '../stores/sessionStore';

export interface TopBarProps {
  title: string;
  subtitle?: string;
  onBack?: () => void;
  right?: React.ReactNode;
}

/** Screen header with the signed-in staff member and a Lock button on every screen. */
export const TopBar: React.FC<TopBarProps> = ({ title, subtitle, onBack, right }) => {
  const user = useSessionStore((s) => s.user);
  const lock = useSessionStore((s) => s.lock);
  // On handhelds the title needs the room; the Lock button still names the user for screen readers.
  const compact = useWindowDimensions().width < 600;

  return (
    <View style={styles.bar}>
      {onBack ? (
        <Pressable onPress={onBack} hitSlop={8} accessibilityRole="button" accessibilityLabel="Back" style={styles.iconBtn}>
          <Icon name="arrow-left" size={22} color={colors.text} />
        </Pressable>
      ) : null}
      <View style={{ flex: 1 }}>
        <Text style={styles.title} numberOfLines={1}>
          {title}
        </Text>
        {subtitle ? (
          <Text style={styles.subtitle} numberOfLines={1}>
            {subtitle}
          </Text>
        ) : null}
      </View>
      {right}
      <View style={styles.user}>
        {compact ? null : (
          <Text style={styles.userName} numberOfLines={1}>
            {user?.full_name}
          </Text>
        )}
        <Pressable
          onPress={lock}
          accessibilityRole="button"
          accessibilityLabel={`Lock (signed in as ${user?.full_name ?? 'staff'})`}
          style={({ pressed }) => [styles.lock, pressed ? { backgroundColor: colors.surfaceBorder } : null]}
        >
          <Icon name="lock" size={16} color={colors.textSecondary} />
          <Text style={styles.lockText}>Lock</Text>
        </Pressable>
      </View>
    </View>
  );
};

const styles = StyleSheet.create({
  bar: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    backgroundColor: colors.surface,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.surfaceBorder,
  },
  iconBtn: {
    width: 40,
    height: 40,
    borderRadius: 20,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.surfaceLight,
  },
  title: {
    ...typography.h2,
    color: colors.text,
  },
  subtitle: {
    ...typography.caption,
    color: colors.textSecondary,
  },
  user: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
  },
  userName: {
    ...typography.caption,
    color: colors.textSecondary,
    fontFamily: fonts.semibold,
    maxWidth: 140,
  },
  lock: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    minHeight: 40,
    paddingHorizontal: spacing.md,
    borderRadius: radii.md,
    backgroundColor: colors.surfaceLight,
  },
  lockText: {
    ...typography.caption,
    color: colors.textSecondary,
    fontFamily: fonts.semibold,
  },
});
