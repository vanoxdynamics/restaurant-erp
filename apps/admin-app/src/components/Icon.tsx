import React from 'react';
import Feather from '@expo/vector-icons/Feather';
import { colors } from '@repo/shared-ui';

export type IconName = React.ComponentProps<typeof Feather>['name'];

export interface IconProps {
  name: IconName;
  size?: number;
  color?: string;
}

/** Thin line icons used across the admin app in place of emoji. */
export const Icon: React.FC<IconProps> = ({ name, size = 20, color = colors.textSecondary }) => (
  <Feather name={name} size={size} color={color} />
);
