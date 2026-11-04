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
  /** No kitchen screen to ask (slip plan): a manager's PIN cancels the ticket on the spot. */
  withManagerPin?: boolean;
  onClose: () => void;
  onSubmit: (reason: string, approverPin?: string) => void;
}

export const VoidRequestSheet: React.FC<VoidRequestSheetProps> = ({ ticket, loading, error, withManagerPin, onClose, onSubmit }) => {
  const [reason, setReason] = useState<string | null>(null);
  const [pin, setPin] = useState('');
  useEffect(() => {
    setReason(null);
    setPin('');
  }, [ticket]);
  const ready = !!reason && (!withManagerPin || pin.length >= 4);

  return (
    <Modal
      visible={ticket !== null}
      onClose={onClose}
      title={withManagerPin ? 'Cancel ticket' : 'Request cancel'}
      subtitle={ticket ? `KOT ${ticket.kot_number.split('-').pop()} · ${ticket.items.map((i) => `${i.quantity} × ${i.name}`).join(', ')}` : ''}
      footer={
        <>
          <Button title="Back" variant="secondary" onPress={onClose} />
          <Button
            title={withManagerPin ? 'Cancel ticket' : 'Send to kitchen'}
            variant={withManagerPin ? 'danger' : undefined}
            onPress={() => reason && onSubmit(reason, withManagerPin ? pin : undefined)}
            disabled={!ready}
            loading={loading}
          />
        </>
      }
    >
      <Text style={styles.label}>Reason</Text>
      <View style={styles.choices}>
        {REASONS.map((r) => (
          <Choice key={r} label={wastageReasonLabel(r)} selected={reason === r} onPress={() => setReason(r)} />
        ))}
      </View>
      {withManagerPin ? (
        <View style={{ marginTop: spacing.lg }}>
          <Input
            label="Manager PIN"
            value={pin}
            onChangeText={(v) => setPin(v.replace(/\D/g, '').slice(0, 6))}
            keyboardType="number-pad"
            secureTextEntry
            helper="A VOID KOT prints for the kitchen. Take the original back."
          />
        </View>
      ) : null}
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
