import React, { useEffect, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { Button, colors, Input, Modal, radii, spacing, typography } from '@repo/shared-ui';
import { TableStatus, WastageReason, wastageReasonLabel } from '@repo/shared-utils';
import { DiningTable, KitchenTicket } from '@repo/api-client';

const REASONS = Object.values(WastageReason);

const Choice: React.FC<{ label: string; selected: boolean; onPress: () => void }> = ({ label, selected, onPress }) => (
  <Pressable
    onPress={onPress}
    accessibilityRole="radio"
    accessibilityLabel={label}
    accessibilityState={{ selected }}
    style={[styles.choice, selected ? styles.choiceSelected : null]}
  >
    <Text style={[styles.choiceText, selected ? { color: colors.primary } : null]}>{label}</Text>
  </Pressable>
);

// ---- Void request ------------------------------------------------------------------

export interface VoidRequestSheetProps {
  ticket: KitchenTicket | null;
  loading?: boolean;
  error?: string | null;
  onClose: () => void;
  onSubmit: (reason: string) => void;
}

export const VoidRequestSheet: React.FC<VoidRequestSheetProps> = ({ ticket, loading, error, onClose, onSubmit }) => {
  const [reason, setReason] = useState<string | null>(null);
  useEffect(() => setReason(null), [ticket]);

  return (
    <Modal
      visible={ticket !== null}
      onClose={onClose}
      title="Request void"
      subtitle={ticket ? `KOT ${ticket.kot_number.split('-').pop()} · ${ticket.items.map((i) => `${i.quantity} × ${i.name}`).join(', ')}` : ''}
      footer={
        <>
          <Button title="Cancel" variant="secondary" onPress={onClose} />
          <Button title="Send to manager" onPress={() => reason && onSubmit(reason)} disabled={!reason} loading={loading} />
        </>
      }
    >
      <Text style={styles.label}>Reason</Text>
      <View style={styles.choices}>
        {REASONS.map((r) => (
          <Choice key={r} label={wastageReasonLabel(r)} selected={reason === r} onPress={() => setReason(r)} />
        ))}
      </View>
      {error ? <Text style={styles.error}>{error}</Text> : null}
    </Modal>
  );
};

// ---- Transfer table ---------------------------------------------------------------

export interface TransferSheetProps {
  visible: boolean;
  fromLabel: string;
  tables: DiningTable[];
  loading?: boolean;
  error?: string | null;
  onClose: () => void;
  onSubmit: (targetTableId: string) => void;
}

export const TransferSheet: React.FC<TransferSheetProps> = ({ visible, fromLabel, tables, loading, error, onClose, onSubmit }) => {
  const [target, setTarget] = useState<string | null>(null);
  useEffect(() => {
    if (visible) setTarget(null);
  }, [visible]);
  const free = tables.filter((t) => t.status === TableStatus.AVAILABLE && !t.merged_into_table_id);

  return (
    <Modal
      visible={visible}
      onClose={onClose}
      title="Transfer table"
      subtitle={`From ${fromLabel}`}
      footer={
        <>
          <Button title="Cancel" variant="secondary" onPress={onClose} />
          <Button title="Transfer" onPress={() => target && onSubmit(target)} disabled={!target} loading={loading} />
        </>
      }
    >
      <Text style={styles.label}>Free tables</Text>
      {free.length === 0 ? (
        <Text style={styles.empty}>No free tables.</Text>
      ) : (
        <View style={styles.choices}>
          {free.map((t) => (
            <Choice key={t.id} label={`${t.table_number} · ${t.capacity} seats`} selected={target === t.id} onPress={() => setTarget(t.id)} />
          ))}
        </View>
      )}
      {error ? <Text style={styles.error}>{error}</Text> : null}
    </Modal>
  );
};

// ---- Item note ------------------------------------------------------------------------

export interface NoteSheetProps {
  visible: boolean;
  itemName: string;
  initial?: string;
  onClose: () => void;
  onSave: (notes: string) => void;
}

export const NoteSheet: React.FC<NoteSheetProps> = ({ visible, itemName, initial, onClose, onSave }) => {
  const [notes, setNotes] = useState(initial ?? '');
  useEffect(() => {
    if (visible) setNotes(initial ?? '');
  }, [visible, initial]);

  return (
    <Modal
      visible={visible}
      onClose={onClose}
      title="Note for kitchen"
      subtitle={itemName}
      footer={
        <>
          <Button title="Cancel" variant="secondary" onPress={onClose} />
          <Button title="Save" onPress={() => onSave(notes.trim())} />
        </>
      }
    >
      <Input placeholder="e.g. less spicy, no onion" value={notes} onChangeText={setNotes} maxLength={200} autoFocus />
    </Modal>
  );
};

const styles = StyleSheet.create({
  label: {
    ...typography.bodyBold,
    color: colors.text,
    marginBottom: spacing.sm,
  },
  choices: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.sm,
  },
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
  choiceText: {
    ...typography.bodyBold,
    color: colors.text,
  },
  empty: {
    ...typography.body,
    color: colors.textSecondary,
  },
  error: {
    ...typography.caption,
    color: colors.danger,
    marginTop: spacing.md,
  },
});
