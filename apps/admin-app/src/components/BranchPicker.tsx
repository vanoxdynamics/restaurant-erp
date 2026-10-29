import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { colors, fonts, Modal, radii, spacing, typography } from '@repo/shared-ui';
import { useAuthStore } from '../stores/authStore';
import { Icon } from './Icon';

export interface BranchPickerProps {
  visible: boolean;
  onClose: () => void;
}

/** Switch the branch every screen reports on (owners with more than one branch). */
export const BranchPicker: React.FC<BranchPickerProps> = ({ visible, onClose }) => {
  const { branches, activeBranchId, switchBranch } = useAuthStore();

  return (
    <Modal visible={visible} onClose={onClose} title="Select branch">
      {branches.map((branch) => {
        const selected = branch.id === activeBranchId;
        return (
          <Pressable
            key={branch.id}
            onPress={async () => {
              await switchBranch(branch.id, branch.name);
              onClose();
            }}
            accessibilityRole="radio"
            accessibilityState={{ selected }}
            style={({ pressed }) => [styles.row, selected ? styles.rowSelected : null, pressed ? { opacity: 0.7 } : null]}
          >
            <View style={{ flex: 1 }}>
              <Text style={styles.name}>{branch.name}</Text>
              <Text style={styles.code}>{branch.code}</Text>
            </View>
            {selected ? <Icon name="check" size={20} color={colors.primary} /> : null}
          </Pressable>
        );
      })}
    </Modal>
  );
};

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    minHeight: 56,
    paddingHorizontal: spacing.md,
    borderRadius: radii.md,
    marginBottom: spacing.xs,
  },
  rowSelected: {
    backgroundColor: colors.primaryGlow,
  },
  name: {
    ...typography.bodyBold,
    color: colors.text,
  },
  code: {
    ...typography.caption,
    color: colors.textMuted,
    fontFamily: fonts.medium,
  },
});
