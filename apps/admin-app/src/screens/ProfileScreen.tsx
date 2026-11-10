import React, { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { Button, Card, colors, fonts, RoleBadge, spacing, typography } from '@repo/shared-ui';
import { StaffRole } from '@repo/shared-utils';
import { BranchPicker } from '../components/BranchPicker';
import { Icon } from '../components/Icon';
import { useAuthStore } from '../stores/authStore';

/** What each role can do in this app, in plain words (the server enforces it). */
const CAN_DO: Partial<Record<StaffRole, string[]>> = {
  [StaffRole.OWNER]: [
    'See every branch, and all branches together in Insights',
    'Add branches',
    'Add and manage all staff, including managers',
    'Menu, insights, daily reports and business settings',
  ],
  [StaffRole.MANAGER]: [
    'Work in your own branch',
    'Add and manage cashiers, captains, waiters and kitchen staff',
    'Menu, insights, daily reports and business settings',
  ],
};

/** You: who is signed in, what you can do, which branch you are looking at, and signing out. */
export const ProfileScreen: React.FC = () => {
  const { currentUser, activeBranchName, branches, logout } = useAuthStore();
  const [pickerOpen, setPickerOpen] = useState(false);
  const canSwitch = branches.length > 1;
  const initials = (currentUser?.full_name ?? '?').split(' ').map((p) => p[0]).slice(0, 2).join('').toUpperCase();
  const abilities = currentUser ? CAN_DO[currentUser.role] : undefined;

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <View style={styles.header}>
        <View style={styles.avatar}>
          <Text style={styles.avatarText}>{initials}</Text>
        </View>
        <Text style={styles.name}>{currentUser?.full_name}</Text>
        <Text style={styles.sub}>@{currentUser?.username}</Text>
        {currentUser ? <RoleBadge role={currentUser.role} /> : null}
      </View>

      {abilities ? (
        <>
          <Text style={styles.sectionTitle}>What you can do</Text>
          <Card style={styles.card}>
            {abilities.map((line) => (
              <View key={line} style={styles.ability}>
                <Icon name="check" size={16} color={colors.primary} />
                <Text style={styles.abilityText}>{line}</Text>
              </View>
            ))}
          </Card>
        </>
      ) : null}

      <Text style={styles.sectionTitle}>Branch</Text>
      <Card style={styles.card} padded={false}>
        <Pressable
          onPress={() => canSwitch && setPickerOpen(true)}
          disabled={!canSwitch}
          accessibilityRole="button"
          accessibilityLabel={canSwitch ? `Branch ${activeBranchName}. Change branch` : `Branch ${activeBranchName}`}
          style={({ pressed }) => [styles.row, pressed ? { opacity: 0.7 } : null]}
        >
          <View style={{ flex: 1 }}>
            <Text style={styles.label}>{activeBranchName}</Text>
            {canSwitch ? <Text style={styles.sub}>{branches.length} branches · tap to switch</Text> : null}
          </View>
          {canSwitch ? <Icon name="chevron-right" size={20} color={colors.textMuted} /> : null}
        </Pressable>
      </Card>

      <Button title="Sign out" variant="outline" onPress={() => void logout()} />
      <BranchPicker visible={pickerOpen} onClose={() => setPickerOpen(false)} />
    </ScrollView>
  );
};

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  content: { paddingHorizontal: spacing.xl, paddingTop: spacing.lg, paddingBottom: spacing.xxxl },
  header: { alignItems: 'center', gap: spacing.xs, marginBottom: spacing.xl },
  avatar: {
    width: 72, height: 72, borderRadius: 36, backgroundColor: colors.primary, alignItems: 'center', justifyContent: 'center',
    marginBottom: spacing.sm,
  },
  avatarText: { ...typography.h2, color: colors.textInverse, fontFamily: fonts.bold },
  name: { ...typography.h2, color: colors.text },
  sectionTitle: { ...typography.caption, color: colors.textMuted, fontFamily: fonts.semibold, marginBottom: spacing.sm },
  card: { marginBottom: spacing.xl, gap: spacing.sm },
  ability: { flexDirection: 'row', alignItems: 'flex-start', gap: spacing.sm },
  abilityText: { ...typography.body, color: colors.text, flex: 1 },
  row: { flexDirection: 'row', alignItems: 'center', gap: spacing.md, minHeight: 56, paddingHorizontal: spacing.lg, paddingVertical: spacing.md },
  label: { ...typography.bodyBold, color: colors.text },
  sub: { ...typography.caption, color: colors.textMuted },
});
