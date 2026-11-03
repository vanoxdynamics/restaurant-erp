import React from 'react';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { Button, colors, fonts, humanizeLabel, radii, spacing, typography } from '@repo/shared-ui';
import { ApiError, RosterEntry, useSignInRoster } from '@repo/api-client';
import { useDeviceStore } from '../stores/deviceStore';

const initials = (name: string) =>
  name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]!.toUpperCase())
    .join('');

export interface StaffPickerScreenProps {
  onSelect: (staff: RosterEntry) => void;
}

/** Shared-tablet sign-in, step one: tap your name. */
export const StaffPickerScreen: React.FC<StaffPickerScreenProps> = ({ onSelect }) => {
  const device = useDeviceStore((s) => s.device)!;
  const unregister = useDeviceStore((s) => s.unregister);
  const roster = useSignInRoster(device.deviceToken);

  // The token is rejected when it has expired or the link was revoked; linking again fixes it.
  const unlinked = roster.error instanceof ApiError && roster.error.status === 401;

  return (
    <View style={styles.container}>
      <Text style={styles.branch}>{device.branchName}</Text>
      <Text style={styles.title}>Who's working?</Text>

      {roster.isLoading ? <ActivityIndicator color={colors.primary} style={{ marginTop: spacing.xl }} /> : null}

      {roster.isError ? (
        <View style={styles.message}>
          <Text style={styles.error}>
            {unlinked ? 'This tablet needs to be linked again by a manager.' : 'Could not load the staff list.'}
          </Text>
          {unlinked ? (
            <Button title="Set up this device" variant="secondary" onPress={() => void unregister()} />
          ) : (
            <Button title="Retry" variant="secondary" onPress={() => void roster.refetch()} />
          )}
        </View>
      ) : null}

      {roster.data && roster.data.length === 0 ? (
        <Text style={styles.empty}>No staff have a PIN yet. Ask a manager to add you.</Text>
      ) : null}

      <ScrollView contentContainerStyle={styles.grid}>
        {(roster.data ?? []).map((person) => (
          <Pressable
            key={person.id}
            onPress={() => onSelect(person)}
            accessibilityRole="button"
            accessibilityLabel={`${person.full_name}, ${humanizeLabel(person.role)}`}
            style={({ pressed }) => [styles.tile, pressed ? styles.tilePressed : null]}
          >
            <View style={styles.avatar}>
              <Text style={styles.avatarText}>{initials(person.full_name)}</Text>
            </View>
            <Text style={styles.name} numberOfLines={1}>
              {person.full_name}
            </Text>
            <Text style={styles.role}>{humanizeLabel(person.role)}</Text>
          </Pressable>
        ))}
      </ScrollView>
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    alignItems: 'center',
    backgroundColor: colors.background,
    paddingTop: spacing.xxl,
    paddingHorizontal: spacing.xl,
  },
  branch: { ...typography.caption, color: colors.textMuted },
  title: { ...typography.h1, color: colors.text, marginTop: spacing.xs, marginBottom: spacing.xl },
  message: { alignItems: 'center', gap: spacing.md },
  error: { ...typography.body, color: colors.danger, textAlign: 'center' },
  empty: { ...typography.body, color: colors.textSecondary, textAlign: 'center' },
  grid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    justifyContent: 'center',
    gap: spacing.md,
    maxWidth: 760,
    paddingBottom: spacing.xl,
  },
  tile: {
    width: 150,
    alignItems: 'center',
    gap: spacing.xs,
    padding: spacing.lg,
    borderRadius: radii.lg,
    borderWidth: 1,
    borderColor: colors.surfaceBorder,
    backgroundColor: colors.surface,
  },
  tilePressed: { backgroundColor: colors.primaryGlow, borderColor: colors.primary },
  avatar: {
    width: 56,
    height: 56,
    borderRadius: 28,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.primaryGlow,
    marginBottom: spacing.xs,
  },
  avatarText: { ...typography.bodyBold, color: colors.primary, fontFamily: fonts.bold },
  name: { ...typography.bodyBold, color: colors.text, maxWidth: '100%' },
  role: { ...typography.caption, color: colors.textSecondary },
});
