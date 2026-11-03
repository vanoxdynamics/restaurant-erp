import React, { useState } from 'react';
import { ScrollView, StyleSheet, Text, View } from 'react-native';
import { Badge, Button, colors, humanizeLabel, Input, Modal, radii, spacing, typography } from '@repo/shared-ui';
import {
  ApiError,
  VoidApprovalItem,
  useApproveVoidKOT,
  useRejectVoidKOT,
  useVoidApprovals,
} from '@repo/api-client';
import { useDeviceStore } from '../stores/deviceStore';
import { QueryState } from './QueryState';

const errorText = (e: unknown) => (e instanceof ApiError ? e.message : 'Something went wrong. Try again.');
const kotLabel = (kotNumber: string) => kotNumber.split('-').pop();

/**
 * Cancellation requests from the floor, waiting for the kitchen to approve (stop making it) or
 * decline (already cooked or plated).
 */
export const CancellationRequests: React.FC = () => {
  const device = useDeviceStore((s) => s.device)!;
  const pending = useVoidApprovals(device.branchId, 'pending');
  const approve = useApproveVoidKOT();
  const reject = useRejectVoidKOT();

  const [declining, setDeclining] = useState<VoidApprovalItem | null>(null);
  const [note, setNote] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [sheetError, setSheetError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const requests = pending.data ?? [];

  const approveRequest = async (request: VoidApprovalItem) => {
    setError(null);
    setBusyId(request.id);
    try {
      await approve.mutateAsync({ requestId: request.id });
    } catch (e) {
      setError(errorText(e));
      void pending.refetch();
    } finally {
      setBusyId(null);
    }
  };

  const declineRequest = async () => {
    if (!declining) return;
    setSheetError(null);
    try {
      await reject.mutateAsync({ requestId: declining.id, note: note.trim() || undefined });
      setDeclining(null);
    } catch (e) {
      setSheetError(errorText(e));
    }
  };

  const openDecline = (request: VoidApprovalItem) => {
    setNote('');
    setSheetError(null);
    setDeclining(request);
  };

  return (
    <View style={styles.container}>
      <QueryState query={pending} isEmpty={requests.length === 0} emptyText="No cancellation requests.">
        <ScrollView contentContainerStyle={styles.scroll}>
          {error ? <Text style={styles.error}>{error}</Text> : null}
          {requests.map((request) => (
            <View key={request.id} style={styles.card}>
              <View style={styles.head}>
                <Text style={[styles.title, { flex: 1 }]}>
                  {request.table_number} · KOT {kotLabel(request.kot_number)} · {humanizeLabel(request.station)}
                </Text>
                <Badge
                  label={request.kot_status === 'ready' ? 'Ready, already cooked' : request.kot_status}
                  variant={request.kot_status === 'ready' ? 'warning' : 'neutral'}
                  size="sm"
                />
              </View>
              {request.items.map((item, index) => (
                <Text key={`${item.name}-${index}`} style={styles.item}>
                  {item.quantity} × {item.name}
                </Text>
              ))}
              <Text style={styles.meta}>
                {humanizeLabel(String(request.wastage_reason))} · asked by {request.requested_by_user_name}
              </Text>
              <View style={styles.actions}>
                <Button title="Decline" variant="secondary" onPress={() => openDecline(request)} disabled={busyId === request.id} />
                <Button
                  title="Approve cancel"
                  variant="danger"
                  onPress={() => approveRequest(request)}
                  loading={busyId === request.id}
                  style={{ flex: 1 }}
                />
              </View>
            </View>
          ))}
        </ScrollView>
      </QueryState>

      <Modal
        visible={declining !== null}
        onClose={() => setDeclining(null)}
        title="Decline cancellation"
        subtitle={declining ? `KOT ${kotLabel(declining.kot_number)} · ${declining.items.map((i) => `${i.quantity} × ${i.name}`).join(', ')}` : ''}
        footer={
          <>
            <Button title="Back" variant="secondary" onPress={() => setDeclining(null)} />
            <Button title="Decline" onPress={declineRequest} loading={reject.isPending} />
          </>
        }
      >
        <Input
          label="Reason for the waiter (optional)"
          placeholder="Already plated"
          value={note}
          onChangeText={setNote}
          maxLength={200}
        />
        {sheetError ? <Text style={styles.error}>{sheetError}</Text> : null}
      </Modal>
    </View>
  );
};

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  scroll: { padding: spacing.lg, gap: spacing.md, maxWidth: 640, width: '100%', alignSelf: 'center' },
  card: {
    backgroundColor: colors.surface,
    borderRadius: radii.lg,
    borderWidth: 1,
    borderColor: colors.surfaceBorder,
    padding: spacing.lg,
    gap: spacing.sm,
  },
  head: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  title: { ...typography.bodyBold, color: colors.text },
  item: { ...typography.body, color: colors.text },
  meta: { ...typography.caption, color: colors.textSecondary },
  actions: { flexDirection: 'row', gap: spacing.md, marginTop: spacing.sm },
  error: { ...typography.caption, color: colors.danger },
});
