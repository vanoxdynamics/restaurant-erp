import React from 'react';
import { View, Text, Pressable, ViewStyle, StyleProp } from 'react-native';
import { colors, spacing, typography } from './theme';

export interface SectionHeaderProps {
  title: string;
  subtitle?: string;
  actionLabel?: string;
  onAction?: () => void;
  style?: StyleProp<ViewStyle>;
}

/** Title row above a group of content, with an optional text action on the right. */
export const SectionHeader: React.FC<SectionHeaderProps> = ({ title, subtitle, actionLabel, onAction, style }) => (
  <View
    style={[
      { flexDirection: 'row', alignItems: 'flex-end', justifyContent: 'space-between', marginBottom: spacing.md },
      style,
    ]}
  >
    <View style={{ flex: 1 }}>
      <Text style={[typography.h3, { color: colors.text }]} accessibilityRole="header">
        {title}
      </Text>
      {subtitle ? <Text style={[typography.caption, { color: colors.textMuted, marginTop: 2 }]}>{subtitle}</Text> : null}
    </View>
    {actionLabel && onAction ? (
      <Pressable onPress={onAction} hitSlop={10} accessibilityRole="button">
        {({ pressed }) => (
          <Text style={[typography.bodyBold, { color: colors.primary, opacity: pressed ? 0.6 : 1 }]}>{actionLabel}</Text>
        )}
      </Pressable>
    ) : null}
  </View>
);
