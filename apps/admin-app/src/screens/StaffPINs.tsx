import React, { useState } from 'react';
import { View, Text, ScrollView, StyleSheet, TouchableOpacity, RefreshControl } from 'react-native';
import { colors, radii, spacing, typography, Button, Badge, RoleBadge, Input, Modal, fonts } from '@repo/shared-ui';
import { formatPaiseToRupees, formatVariancePaise, formatDateTime, StaffRole } from '@repo/shared-utils';
import {
  ApiError,
  StaffUser,
  useCreateStaffUser,
  useSetStaffActive,
  useShiftAudits,
  useStaffUsers,
  useUpdateStaffPIN,
} from '@repo/api-client';
import { QueryState } from '../components/QueryState';
import { useAuthStore } from '../stores/authStore';

const RANK: Record<string, number> = {
  [StaffRole.OWNER]: 50,
  [StaffRole.MANAGER]: 40,
  [StaffRole.CASHIER]: 30,
  [StaffRole.CAPTAIN]: 20,
  [StaffRole.WAITER]: 10,
  [StaffRole.KITCHEN]: 10,
};
const FLOOR_ROLES = [StaffRole.WAITER, StaffRole.CAPTAIN, StaffRole.CASHIER, StaffRole.KITCHEN];
const isPin = (value: string) => /^\d{4,6}$/.test(value);
const errorText = (e: unknown) => (e instanceof ApiError ? e.message : 'Something went wrong. Try again.');

export const StaffPINsScreen: React.FC = () => {
  const { currentUser, activeBranchId } = useAuthStore();
  const staffQuery = useStaffUsers(activeBranchId);
  const shiftQuery = useShiftAudits(activeBranchId);
  const resetPin = useUpdateStaffPIN();
  const createStaff = useCreateStaffUser();
  const setActive = useSetStaffActive();

  const staffList = staffQuery.data ?? [];
  const shiftAudits = shiftQuery.data ?? [];
  const [activeTab, setActiveTab] = useState<'STAFF' | 'SHIFTS'>('STAFF');

  const isOwner = currentUser?.role === StaffRole.OWNER;
  const canManage = (staff: StaffUser) =>
    staff.id !== currentUser?.id && (isOwner || RANK[staff.role] < RANK[currentUser?.role ?? StaffRole.WAITER]);
  const assignableRoles = isOwner ? [...FLOOR_ROLES, StaffRole.MANAGER] : FLOOR_ROLES;

  // Reset PIN
  const [pinTarget, setPinTarget] = useState<StaffUser | null>(null);
  const [newPin, setNewPin] = useState('');
  const [pinError, setPinError] = useState<string | null>(null);

  // Activate / deactivate
  const [activeTarget, setActiveTarget] = useState<StaffUser | null>(null);
  const [activeError, setActiveError] = useState<string | null>(null);

  // Add staff
  const [addOpen, setAddOpen] = useState(false);
  const [fullName, setFullName] = useState('');
  const [username, setUsername] = useState('');
  const [role, setRole] = useState<StaffRole>(StaffRole.WAITER);
  const [pin, setPin] = useState('');
  const [password, setPassword] = useState('');
  const [addError, setAddError] = useState<string | null>(null);
  const needsPassword = role === StaffRole.MANAGER;

  const openPin = (staff: StaffUser) => {
    setPinTarget(staff);
    setNewPin('');
    setPinError(null);
  };

  const savePin = async () => {
    if (!pinTarget || !isPin(newPin)) {
      setPinError('PIN must be 4–6 digits.');
      return;
    }
    try {
      await resetPin.mutateAsync({ userId: pinTarget.id, newPin });
      setPinTarget(null);
    } catch (e) {
      setPinError(errorText(e));
    }
  };

  const confirmActive = async () => {
    if (!activeTarget) return;
    try {
      await setActive.mutateAsync({ userId: activeTarget.id, isActive: !activeTarget.is_active });
      setActiveTarget(null);
    } catch (e) {
      setActiveError(errorText(e));
    }
  };

  const resetAddForm = () => {
    setFullName('');
    setUsername('');
    setRole(StaffRole.WAITER);
    setPin('');
    setPassword('');
    setAddError(null);
  };

  const saveStaff = async () => {
    if (!fullName.trim() || !username.trim()) return setAddError('Name and username are required.');
    if (!isPin(pin)) return setAddError('PIN must be 4–6 digits.');
    if (needsPassword && password.length < 10) return setAddError('Password must be at least 10 characters.');
    try {
      await createStaff.mutateAsync({
        full_name: fullName.trim(),
        username: username.trim(),
        role,
        pin,
        password: needsPassword ? password : undefined,
        branch_id: activeBranchId,
      });
      resetAddForm();
      setAddOpen(false);
    } catch (e) {
      setAddError(errorText(e));
    }
  };

  const activeQuery = activeTab === 'STAFF' ? staffQuery : shiftQuery;

  return (
    <ScrollView
      style={styles.container}
      contentContainerStyle={styles.contentContainer}
      refreshControl={
        <RefreshControl refreshing={activeQuery.isRefetching} onRefresh={activeQuery.refetch} tintColor={colors.primary} />
      }
    >
      <View style={styles.topHeader}>
        <Text style={[typography.h1, { color: colors.text }]}>Staff</Text>
        <Button title="Add staff" size="sm" variant="primary" onPress={() => setAddOpen(true)} />
      </View>

      <View style={styles.tabRow}>
        {([
          { key: 'STAFF', label: `Staff (${staffList.length})` },
          { key: 'SHIFTS', label: 'Shifts' },
        ] as const).map((tab) => (
          <TouchableOpacity
            key={tab.key}
            style={[styles.tabBtn, activeTab === tab.key ? styles.tabBtnActive : null]}
            onPress={() => setActiveTab(tab.key)}
            accessibilityRole="tab"
            accessibilityState={{ selected: activeTab === tab.key }}
          >
            <Text style={[styles.tabBtnText, activeTab === tab.key ? { color: colors.primary } : null]}>
              {tab.label}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      {activeTab === 'STAFF' && (
        <QueryState query={staffQuery} isEmpty={staffList.length === 0} emptyText="No staff in this branch.">
          <View style={styles.staffGrid}>
            {staffList.map((staff) => (
              <View key={staff.id} style={[styles.staffCard, !staff.is_active ? styles.staffInactive : null]}>
                <View style={styles.staffHeader}>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.staffName}>{staff.full_name}</Text>
                    <Text style={styles.staffUsername}>@{staff.username}</Text>
                  </View>
                  {staff.is_active ? <RoleBadge role={staff.role} /> : <Badge label="Inactive" variant="neutral" size="sm" />}
                </View>

                {canManage(staff) ? (
                  <View style={styles.staffActions}>
                    {staff.is_active ? (
                      <Button title="Reset PIN" size="sm" variant="outline" onPress={() => openPin(staff)} />
                    ) : null}
                    <Button
                      title={staff.is_active ? 'Deactivate' : 'Reactivate'}
                      size="sm"
                      variant="ghost"
                      onPress={() => {
                        setActiveError(null);
                        setActiveTarget(staff);
                      }}
                      textStyle={staff.is_active ? { color: colors.danger } : undefined}
                    />
                  </View>
                ) : null}
              </View>
            ))}
          </View>
        </QueryState>
      )}

      {activeTab === 'SHIFTS' && (
        <QueryState query={shiftQuery} isEmpty={shiftAudits.length === 0} emptyText="No closed shifts yet.">
          <View style={styles.shiftsGrid}>
            {shiftAudits.map((shift) => {
              const varianceInfo = formatVariancePaise(shift.variance_paise);
              return (
                <View key={shift.id} style={styles.shiftCard}>
                  <View style={styles.shiftHeader}>
                    <View>
                      <Text style={styles.cashierTitle}>{shift.cashier_name}</Text>
                      <Text style={styles.shiftDate}>
                        {formatDateTime(shift.start_time)}
                        {shift.end_time ? ` – ${formatDateTime(shift.end_time)}` : ''}
                      </Text>
                    </View>
                    <Badge label={shift.status} variant={shift.status === 'CLOSED' ? 'neutral' : 'available'} size="sm" />
                  </View>

                  <View style={styles.mathGrid}>
                    {[
                      ['Opening cash', shift.opening_float_paise],
                      ['Cash sales', shift.cash_sales_paise],
                      ['Expected cash', shift.expected_cash_paise],
                      ['Counted cash', shift.blind_drop_cash_paise],
                    ].map(([label, value]) => (
                      <View key={label as string} style={styles.mathItem}>
                        <Text style={styles.mathLabel}>{label}</Text>
                        <Text style={styles.mathVal}>{formatPaiseToRupees(value as number)}</Text>
                      </View>
                    ))}
                  </View>

                  <View
                    style={[
                      styles.varianceAlertBox,
                      varianceInfo.isShortage
                        ? styles.varianceShortage
                        : varianceInfo.isOverage
                        ? styles.varianceOverage
                        : styles.varianceBalanced,
                    ]}
                  >
                    <Text style={styles.varianceTitle}>
                      {varianceInfo.isShortage ? 'Cash short' : varianceInfo.isOverage ? 'Cash over' : 'Balanced'}
                    </Text>
                    <Text
                      style={[
                        styles.varianceAmount,
                        { color: varianceInfo.isShortage ? colors.danger : varianceInfo.isOverage ? colors.billed : colors.available },
                      ]}
                    >
                      {varianceInfo.formatted}
                    </Text>
                  </View>
                </View>
              );
            })}
          </View>
        </QueryState>
      )}

      {/* Reset PIN */}
      <Modal
        visible={pinTarget !== null}
        onClose={() => setPinTarget(null)}
        title="Reset PIN"
        subtitle={pinTarget?.full_name}
        footer={
          <>
            <Button title="Cancel" variant="secondary" onPress={() => setPinTarget(null)} />
            <Button title="Save" variant="primary" onPress={savePin} loading={resetPin.isPending} />
          </>
        }
      >
        <Input
          label="New PIN"
          placeholder="4–6 digits"
          maxLength={6}
          secureTextEntry
          keyboardType="number-pad"
          value={newPin}
          onChangeText={setNewPin}
          error={pinError ?? undefined}
        />
      </Modal>

      {/* Deactivate / reactivate */}
      <Modal
        visible={activeTarget !== null}
        onClose={() => setActiveTarget(null)}
        title={activeTarget?.is_active ? 'Deactivate staff member' : 'Reactivate staff member'}
        subtitle={activeTarget?.full_name}
        footer={
          <>
            <Button title="Cancel" variant="secondary" onPress={() => setActiveTarget(null)} />
            <Button
              title={activeTarget?.is_active ? 'Deactivate' : 'Reactivate'}
              variant={activeTarget?.is_active ? 'danger' : 'primary'}
              onPress={confirmActive}
              loading={setActive.isPending}
            />
          </>
        }
      >
        <Text style={styles.modalBody}>
          {activeTarget?.is_active
            ? 'They will be signed out on all devices and can no longer sign in.'
            : 'They will be able to sign in again. Set a new PIN after reactivating.'}
        </Text>
        {activeError ? <Text style={styles.modalError}>{activeError}</Text> : null}
      </Modal>

      {/* Add staff */}
      <Modal
        visible={addOpen}
        onClose={() => setAddOpen(false)}
        title="Add staff member"
        footer={
          <>
            <Button title="Cancel" variant="secondary" onPress={() => setAddOpen(false)} />
            <Button title="Save" variant="primary" onPress={saveStaff} loading={createStaff.isPending} />
          </>
        }
      >
        <Input label="Full name" value={fullName} onChangeText={setFullName} />
        <Input label="Username" value={username} onChangeText={setUsername} autoCapitalize="none" autoCorrect={false} />

        <Text style={styles.fieldLabel}>Role</Text>
        <View style={styles.rolePickerRow}>
          {assignableRoles.map((r) => (
            <TouchableOpacity
              key={r}
              style={[styles.roleChip, role === r ? styles.roleChipActive : null]}
              onPress={() => setRole(r)}
              accessibilityRole="radio"
              accessibilityState={{ selected: role === r }}
            >
              <Text style={[styles.roleChipText, role === r ? { color: colors.primary } : null]}>
                {r.charAt(0) + r.slice(1).toLowerCase()}
              </Text>
            </TouchableOpacity>
          ))}
        </View>

        <Input
          label="POS PIN"
          placeholder="4–6 digits"
          maxLength={6}
          keyboardType="number-pad"
          secureTextEntry
          value={pin}
          onChangeText={setPin}
        />
        {needsPassword ? (
          <Input
            label="Password"
            helper="At least 10 characters"
            secureTextEntry
            value={password}
            onChangeText={setPassword}
          />
        ) : null}
        {addError ? <Text style={styles.modalError}>{addError}</Text> : null}
      </Modal>
    </ScrollView>
  );
};

const styles = StyleSheet.create({
  staffInactive: {
    opacity: 0.6,
  },
  staffActions: {
    flexDirection: 'row',
    gap: spacing.sm,
    marginTop: spacing.md,
  },
  fieldLabel: {
    ...typography.caption,
    color: colors.text,
    fontFamily: fonts.semibold,
    marginBottom: 6,
  },
  modalBody: {
    ...typography.body,
    color: colors.textSecondary,
  },
  modalError: {
    ...typography.caption,
    color: colors.danger,
    marginTop: spacing.sm,
  },
  container: {
    flex: 1,
    backgroundColor: colors.background,
  },
  contentContainer: {
    padding: spacing.lg,
    paddingBottom: 100,
  },
  topHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: spacing.md,
  },
  tabRow: {
    flexDirection: 'row',
    borderBottomWidth: 1,
    borderBottomColor: colors.surfaceBorder,
    marginBottom: spacing.lg,
  },
  tabBtn: {
    paddingVertical: spacing.sm,
    paddingHorizontal: spacing.md,
    borderBottomWidth: 2,
    borderBottomColor: 'transparent',
  },
  tabBtnActive: {
    borderBottomColor: colors.cyan,
  },
  tabBtnText: {
    fontSize: 13,
    fontFamily: fonts.bold,
    color: colors.textSecondary,
  },
  staffGrid: {
    gap: spacing.md,
  },
  staffCard: {
    backgroundColor: colors.surface,
    borderRadius: radii.lg,
    borderWidth: 1,
    borderColor: colors.surfaceBorder,
    padding: spacing.lg,
  },
  staffHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: spacing.md,
  },
  staffName: {
    fontSize: 16,
    fontFamily: fonts.bold,
    color: colors.text,
  },
  staffUsername: {
    fontFamily: fonts.regular,
    fontSize: 12,
    color: colors.textMuted,
  },
  pinSection: {
    backgroundColor: colors.surfaceLight,
    borderRadius: radii.md,
    padding: spacing.md,
    marginBottom: spacing.sm,
  },
  pinRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  pinLabel: {
    fontFamily: fonts.regular,
    fontSize: 12,
    color: colors.textSecondary,
    marginBottom: 2,
  },
  pinValue: {
    fontSize: 16,
    fontFamily: fonts.bold,
    color: colors.text,
    letterSpacing: 2,
  },
  phoneText: {
    fontFamily: fonts.regular,
    fontSize: 12,
    color: colors.textMuted,
    marginTop: 2,
  },
  shiftsGrid: {
    gap: spacing.md,
  },
  shiftCard: {
    backgroundColor: colors.surface,
    borderRadius: radii.lg,
    borderWidth: 1,
    borderColor: colors.surfaceBorder,
    padding: spacing.lg,
  },
  shiftHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    marginBottom: spacing.md,
  },
  cashierTitle: {
    fontSize: 15,
    fontFamily: fonts.bold,
    color: colors.text,
  },
  shiftDate: {
    fontFamily: fonts.regular,
    fontSize: 12,
    color: colors.textMuted,
    marginTop: 2,
  },
  mathGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.sm,
    backgroundColor: colors.surfaceLight,
    padding: spacing.md,
    borderRadius: radii.md,
    marginBottom: spacing.md,
  },
  mathItem: {
    flex: 1,
    minWidth: 120,
  },
  mathLabel: {
    fontFamily: fonts.regular,
    fontSize: 12,
    color: colors.textMuted,
    marginBottom: 2,
  },
  mathVal: {
    fontSize: 14,
    fontFamily: fonts.bold,
    color: colors.text,
  },
  varianceAlertBox: {
    padding: spacing.md,
    borderRadius: radii.md,
    borderWidth: 1,
    marginBottom: spacing.sm,
  },
  varianceShortage: {
    backgroundColor: 'rgba(239, 68, 68, 0.1)',
    borderColor: colors.danger,
  },
  varianceOverage: {
    backgroundColor: 'rgba(6, 182, 212, 0.1)',
    borderColor: colors.cyan,
  },
  varianceBalanced: {
    backgroundColor: 'rgba(16, 185, 129, 0.1)',
    borderColor: colors.available,
  },
  varianceTitle: {
    fontSize: 12,
    fontFamily: fonts.bold,
    color: colors.text,
    marginBottom: 2,
  },
  varianceAmount: {
    fontSize: 14,
    fontFamily: fonts.bold,
  },
  shiftNotes: {
    fontFamily: fonts.regular,
    fontSize: 12,
    color: colors.textSecondary,
    fontStyle: 'italic',
  },
  rolePickerRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.xs,
    marginBottom: spacing.md,
  },
  roleChip: {
    paddingVertical: 6,
    paddingHorizontal: spacing.sm + 2,
    borderRadius: radii.xs,
    borderWidth: 1,
    borderColor: colors.surfaceBorder,
    backgroundColor: colors.surfaceLight,
  },
  roleChipActive: {
    borderColor: colors.cyan,
    backgroundColor: 'rgba(6, 182, 212, 0.15)',
  },
  roleChipText: {
    fontSize: 12,
    color: colors.textSecondary,
    fontFamily: fonts.bold,
  },
});
