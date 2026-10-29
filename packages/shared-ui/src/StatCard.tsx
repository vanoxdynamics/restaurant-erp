import React from 'react';
import { View, Text, ViewStyle, StyleProp } from 'react-native';
import { Card } from './Card';
import { colors, humanizeLabel, spacing, typography } from './theme';

export interface StatCardProps {
  title: string;
  value: string;
  subtitle?: string;
  trend?: string;
  trendPositive?: boolean;
  icon?: React.ReactNode;
  accentColor?: string;
  style?: StyleProp<ViewStyle>;
  onPress?: () => void;
}

export const StatCard: React.FC<StatCardProps> = ({
  title,
  value,
  subtitle,
  trend,
  trendPositive,
  icon,
  style,
  onPress,
}) => {
  return (
    <Card style={[{ flex: 1, minWidth: 140 }, style]} onPress={onPress} accessibilityLabel={`${title}: ${value}`}>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: spacing.sm }}>
        <Text style={[typography.caption, { color: colors.textSecondary }]}>{humanizeLabel(title)}</Text>
        {icon ? <View>{icon}</View> : null}
      </View>
      <Text style={[typography.h2, { color: colors.text, fontSize: 22, marginBottom: spacing.xs }]}>{value}</Text>
      <View style={{ flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap', gap: 6 }}>
        {trend ? (
          <Text
            style={[
              typography.label,
              { color: trendPositive ? colors.success : colors.danger },
            ]}
          >
            {trendPositive ? '↑ ' : '↓ '}
            {trend}
          </Text>
        ) : null}
        {subtitle ? (
          <Text style={[typography.caption, { color: colors.textMuted }]}>{subtitle}</Text>
        ) : null}
      </View>
    </Card>
  );
};
