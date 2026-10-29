import React, { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { Button, Card, colors, fonts, RoleBadge, spacing, typography } from '@repo/shared-ui';
import { BranchPicker } from '../components/BranchPicker';
import { Icon } from '../components/Icon';
import { useAuthStore } from '../stores/authStore';

export const SettingsScreen: React.FC = () => {
  const { currentUser, activeBranchName, branches, logout } = useAuthStore();
  const [pickerOpen, setPickerOpen] = useState(false);
  const canSwitch = branches.length > 1;

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Text style={[typography.h1, { color: colors.text, marginBottom: spacing.lg }]}>Settings</Text>

      <Text style={styles.sectionTitle}>Account</Text>
      <Card style={styles.card}>
        <View style={styles.row}>
          <View style={{ flex: 1 }}>
            <Text style={styles.name}>{currentUser?.full_name}</Text>
            <Text style={styles.sub}>@{currentUser?.username}</Text>
          </View>
          {currentUser ? <RoleBadge role={currentUser.role} /> : null}
        </View>
      </Card>

      <Text style={styles.sectionTitle}>Branch</Text>
      <Card style={styles.card} padded={false}>
        <Pressable
          onPress={() => canSwitch && setPickerOpen(true)}
          disabled={!canSwitch}
          accessibilityRole="button"
          style={({ pressed }) => [styles.row, styles.rowPadded, pressed ? { opacity: 0.7 } : null]}
        >
          <View style={{ flex: 1 }}>
            <Text style={styles.label}>{activeBranchName}</Text>
            {canSwitch ? <Text style={styles.sub}>{branches.length} branches</Text> : null}
          </View>
          {canSwitch ? <Icon name="chevron-right" size={20} color={colors.textMuted} /> : null}
        </Pressable>
      </Card>

      <Button title="Sign out" variant="outline" onPress={() => void logout()} style={styles.signOut} />

      <BranchPicker visible={pickerOpen} onClose={() => setPickerOpen(false)} />
    </ScrollView>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.background,
  },
  content: {
    paddingHorizontal: spacing.xl,
    paddingTop: spacing.md,
    paddingBottom: spacing.xxxl,
  },
  sectionTitle: {
    ...typography.caption,
    color: colors.textMuted,
    fontFamily: fonts.semibold,
    marginBottom: spacing.sm,
  },
  card: {
    marginBottom: spacing.xl,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
  },
  rowPadded: {
    minHeight: 56,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
  },
  name: {
    ...typography.h3,
    color: colors.text,
  },
  label: {
    ...typography.bodyBold,
    color: colors.text,
  },
  sub: {
    ...typography.caption,
    color: colors.textMuted,
  },
  signOut: {
    marginTop: spacing.sm,
  },
});
