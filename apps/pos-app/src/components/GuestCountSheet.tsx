import React, { useEffect, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { Button, colors, fonts, Modal, radii, spacing, typography } from '@repo/shared-ui';
import { Icon } from './Icon';

export interface GuestCountSheetProps {
  visible: boolean;
  title: string;
  defaultGuests: number;
  loading?: boolean;
  error?: string | null;
  onClose: () => void;
  onConfirm: (guests: number) => void;
}

export const GuestCountSheet: React.FC<GuestCountSheetProps> = ({
  visible,
  title,
  defaultGuests,
  loading,
  error,
  onClose,
  onConfirm,
}) => {
  const [guests, setGuests] = useState(defaultGuests);
  useEffect(() => {
    if (visible) setGuests(defaultGuests);
  }, [visible, defaultGuests]);

  return (
    <Modal
      visible={visible}
      onClose={onClose}
      title={title}
      footer={
        <>
          <Button title="Cancel" variant="secondary" onPress={onClose} />
          <Button title="Open table" onPress={() => onConfirm(guests)} loading={loading} />
        </>
      }
    >
      <Text style={styles.label}>Guests</Text>
      <View style={styles.stepper}>
        <Pressable
          onPress={() => setGuests((g) => Math.max(1, g - 1))}
          accessibilityRole="button"
          accessibilityLabel="Fewer guests"
          style={styles.stepBtn}
        >
          <Icon name="minus" size={22} color={colors.text} />
        </Pressable>
        <Text style={styles.count} accessibilityLabel={`${guests} guests`}>
          {guests}
        </Text>
        <Pressable
          onPress={() => setGuests((g) => Math.min(50, g + 1))}
          accessibilityRole="button"
          accessibilityLabel="More guests"
          style={styles.stepBtn}
        >
          <Icon name="plus" size={22} color={colors.text} />
        </Pressable>
      </View>
      {error ? <Text style={styles.error}>{error}</Text> : null}
    </Modal>
  );
};

const styles = StyleSheet.create({
  label: {
    ...typography.caption,
    color: colors.text,
    fontFamily: fonts.semibold,
    marginBottom: spacing.sm,
  },
  stepper: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xl,
  },
  stepBtn: {
    width: 56,
    height: 56,
    borderRadius: radii.lg,
    borderWidth: 1,
    borderColor: colors.surfaceBorder,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.surface,
  },
  count: {
    ...typography.display,
    minWidth: 56,
    textAlign: 'center',
    color: colors.text,
  },
  error: {
    ...typography.caption,
    color: colors.danger,
    marginTop: spacing.md,
  },
});
