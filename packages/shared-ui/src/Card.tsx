import React from 'react';
import { View, ViewStyle, Pressable, StyleProp } from 'react-native';
import { colors, radii, shadows, spacing } from './theme';

export interface CardProps {
  children: React.ReactNode;
  style?: StyleProp<ViewStyle>;
  variant?: 'surface' | 'glass' | 'highlight' | 'outlined';
  onPress?: () => void;
  padded?: boolean;
  accessibilityLabel?: string;
}

const VARIANT_STYLES: Record<NonNullable<CardProps['variant']>, ViewStyle> = {
  surface: { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.surfaceBorder, ...shadows.card },
  glass: { backgroundColor: colors.backgroundSecondary, borderWidth: 1, borderColor: colors.surfaceBorder },
  highlight: { backgroundColor: colors.primaryGlow, borderWidth: 1, borderColor: '#cfe5dc' },
  outlined: { backgroundColor: 'transparent', borderWidth: 1, borderColor: colors.surfaceBorder },
};

export const Card: React.FC<CardProps> = ({
  children,
  style,
  variant = 'surface',
  onPress,
  padded = true,
  accessibilityLabel,
}) => {
  const base: ViewStyle = {
    borderRadius: radii.lg,
    padding: padded ? spacing.lg : 0,
    ...VARIANT_STYLES[variant],
  };

  if (onPress) {
    return (
      <Pressable
        onPress={onPress}
        accessibilityRole="button"
        accessibilityLabel={accessibilityLabel}
        style={({ pressed }) => [base, pressed ? { opacity: 0.85, transform: [{ scale: 0.99 }] } : null, style]}
      >
        {children}
      </Pressable>
    );
  }

  return <View style={[base, style]}>{children}</View>;
};
