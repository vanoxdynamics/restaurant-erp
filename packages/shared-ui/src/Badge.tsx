import React from 'react';
import { View, Text, ViewStyle, TextStyle, StyleProp } from 'react-native';
import { colors, humanizeLabel, radii, spacing, fonts } from './theme';
import { TableStatus, StaffRole } from '@repo/shared-utils';

export interface BadgeProps {
  label: string;
  variant?: 'available' | 'occupied' | 'billed' | 'dirty' | 'primary' | 'danger' | 'warning' | 'neutral' | 'accent' | 'cyan';
  size?: 'sm' | 'md';
  pulse?: boolean;
  style?: StyleProp<ViewStyle>;
  textStyle?: StyleProp<TextStyle>;
}

const PALETTE: Record<NonNullable<BadgeProps['variant']>, { bg: string; text: string }> = {
  available: { bg: colors.availableBg, text: colors.available },
  occupied: { bg: colors.occupiedBg, text: colors.occupied },
  billed: { bg: colors.billedBg, text: colors.billed },
  dirty: { bg: colors.dirtyBg, text: colors.dirty },
  primary: { bg: colors.primaryGlow, text: colors.primary },
  cyan: { bg: colors.primaryGlow, text: colors.primary },
  danger: { bg: colors.dangerBg, text: colors.danger },
  warning: { bg: colors.warningBg, text: colors.warning },
  accent: { bg: colors.accentBg, text: colors.accentDark },
  neutral: { bg: colors.surfaceLight, text: colors.textSecondary },
};

/** Small soft-filled pill. All-caps labels are shown in sentence case. */
export const Badge: React.FC<BadgeProps> = ({
  label,
  variant = 'neutral',
  size = 'md',
  pulse = false,
  style,
  textStyle,
}) => {
  const palette = PALETTE[variant];
  const isSm = size === 'sm';

  return (
    <View
      style={[
        {
          flexDirection: 'row',
          alignItems: 'center',
          backgroundColor: palette.bg,
          paddingVertical: isSm ? 2 : 4,
          paddingHorizontal: isSm ? spacing.sm : 10,
          borderRadius: radii.full,
          alignSelf: 'flex-start',
        },
        style,
      ]}
    >
      {pulse && (
        <View
          style={{
            width: 6,
            height: 6,
            borderRadius: 3,
            backgroundColor: palette.text,
            marginRight: 6,
          }}
        />
      )}
      <Text
        style={[
          {
            color: palette.text,
            fontSize: isSm ? 11 : 12,
            fontFamily: fonts.semibold,
            lineHeight: isSm ? 15 : 16,
          },
          textStyle,
        ]}
      >
        {humanizeLabel(label)}
      </Text>
    </View>
  );
};

export const TableStatusBadge: React.FC<{ status: TableStatus | string; size?: 'sm' | 'md' }> = ({
  status,
  size = 'md',
}) => {
  let variant: BadgeProps['variant'] = 'neutral';
  switch (status) {
    case TableStatus.AVAILABLE:
      variant = 'available';
      break;
    case TableStatus.OCCUPIED:
      variant = 'occupied';
      break;
    case TableStatus.BILLED:
      variant = 'billed';
      break;
    case TableStatus.DIRTY:
      variant = 'dirty';
      break;
  }
  return <Badge label={status} variant={variant} size={size} pulse={status === TableStatus.OCCUPIED} />;
};

export const RoleBadge: React.FC<{ role: StaffRole | string; size?: 'sm' | 'md' }> = ({
  role,
  size = 'sm',
}) => {
  let variant: BadgeProps['variant'] = 'neutral';
  if (role === StaffRole.OWNER) variant = 'accent';
  else if (role === StaffRole.MANAGER) variant = 'primary';
  else if (role === StaffRole.CASHIER) variant = 'billed';
  else if (role === StaffRole.CAPTAIN) variant = 'available';
  else if (role === StaffRole.KITCHEN) variant = 'warning';

  return <Badge label={role} variant={variant} size={size} />;
};
