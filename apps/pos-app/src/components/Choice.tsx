import React from 'react';
import { Pressable, StyleSheet, Text } from 'react-native';
import { colors, radii, spacing, typography } from '@repo/shared-ui';

export interface ChoiceProps {
  label: string;
  selected: boolean;
  onPress: () => void;
}

/** A single-select option chip. */
export const Choice: React.FC<ChoiceProps> = ({ label, selected, onPress }) => (
  <Pressable
    onPress={onPress}
    accessibilityRole="radio"
    accessibilityLabel={label}
    accessibilityState={{ selected }}
    style={[styles.choice, selected ? styles.choiceSelected : null]}
  >
    <Text style={[styles.text, selected ? { color: colors.primary } : null]}>{label}</Text>
  </Pressable>
);

const styles = StyleSheet.create({
  choice: {
    minHeight: 48,
    paddingHorizontal: spacing.lg,
    borderRadius: radii.md,
    borderWidth: 1.5,
    borderColor: colors.surfaceBorder,
    backgroundColor: colors.surface,
    justifyContent: 'center',
  },
  choiceSelected: {
    borderColor: colors.primary,
    backgroundColor: colors.primaryGlow,
  },
  text: {
    ...typography.bodyBold,
    color: colors.text,
  },
});
