import React from 'react';
import {
  Pressable,
  Text,
  ActivityIndicator,
  ViewStyle,
  TextStyle,
  StyleProp,
} from 'react-native';
import { colors, radii, spacing, fonts } from './theme';

export type ButtonVariant = 'primary' | 'secondary' | 'danger' | 'outline' | 'ghost' | 'success';
export type ButtonSize = 'sm' | 'md' | 'lg';

export interface ButtonProps {
  title: string;
  onPress: () => void;
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  disabled?: boolean;
  icon?: React.ReactNode;
  style?: StyleProp<ViewStyle>;
  textStyle?: StyleProp<TextStyle>;
  accessibilityLabel?: string;
}

const VARIANTS: Record<ButtonVariant, { bg: string; pressed: string; text: string; border?: string }> = {
  primary: { bg: colors.primary, pressed: colors.primaryDark, text: colors.textInverse },
  success: { bg: colors.success, pressed: colors.primaryDark, text: colors.textInverse },
  danger: { bg: colors.danger, pressed: '#8f1e18', text: colors.textInverse },
  secondary: { bg: colors.surfaceLight, pressed: colors.surfaceBorder, text: colors.text },
  outline: { bg: 'transparent', pressed: colors.primaryGlow, text: colors.primary, border: colors.surfaceBorder },
  ghost: { bg: 'transparent', pressed: colors.primaryGlow, text: colors.primary },
};

const SIZES: Record<ButtonSize, { minHeight: number; paddingHorizontal: number; fontSize: number }> = {
  sm: { minHeight: 36, paddingHorizontal: spacing.md, fontSize: 13 },
  md: { minHeight: 44, paddingHorizontal: spacing.lg, fontSize: 15 },
  lg: { minHeight: 52, paddingHorizontal: spacing.xxl, fontSize: 16 },
};

export const Button: React.FC<ButtonProps> = ({
  title,
  onPress,
  variant = 'primary',
  size = 'md',
  loading = false,
  disabled = false,
  icon,
  style,
  textStyle,
  accessibilityLabel,
}) => {
  const v = VARIANTS[variant];
  const s = SIZES[size];
  const inactive = disabled || loading;

  return (
    <Pressable
      onPress={onPress}
      disabled={inactive}
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel ?? title}
      accessibilityState={{ disabled: inactive, busy: loading }}
      hitSlop={size === 'sm' ? 4 : 0}
      style={({ pressed }) => [
        {
          minHeight: s.minHeight,
          paddingHorizontal: s.paddingHorizontal,
          borderRadius: radii.md,
          backgroundColor: pressed && !inactive ? v.pressed : v.bg,
          borderWidth: v.border ? 1 : 0,
          borderColor: v.border,
          flexDirection: 'row',
          alignItems: 'center',
          justifyContent: 'center',
          opacity: inactive ? 0.5 : 1,
          transform: [{ scale: pressed && !inactive ? 0.98 : 1 }],
        },
        style,
      ]}
    >
      {loading ? (
        <ActivityIndicator size="small" color={v.text} />
      ) : (
        <>
          {icon ? <>{icon}</> : null}
          <Text
            numberOfLines={1}
            style={[
              { color: v.text, fontSize: s.fontSize, fontFamily: fonts.semibold },
              icon ? { marginLeft: spacing.sm } : undefined,
              textStyle,
            ]}
          >
            {title}
          </Text>
        </>
      )}
    </Pressable>
  );
};
