import React, { useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { colors, fonts, radii, spacing, typography } from '@repo/shared-ui';
import { useVoidApprovals } from '@repo/api-client';
import { CancellationRequests } from '../components/CancellationRequests';
import { KitchenTickets } from '../components/KitchenTickets';
import { TopBar } from '../components/TopBar';
import { useDeviceStore } from '../stores/deviceStore';

/**
 * Kitchen home. Tickets are the day-to-day queue; cancellation requests from the floor are a second
 * tab whose count stays visible so a request is never missed while working the queue.
 */
export const KitchenScreen: React.FC = () => {
  const device = useDeviceStore((s) => s.device)!;
  const pending = useVoidApprovals(device.branchId, 'pending');
  const [tab, setTab] = useState<'tickets' | 'cancellations'>('tickets');
  const pendingCount = pending.data?.length ?? 0;

  return (
    <View style={styles.container}>
      <TopBar title="Kitchen" />
      <View style={styles.segment}>
        {(['tickets', 'cancellations'] as const).map((key) => (
          <Pressable
            key={key}
            onPress={() => setTab(key)}
            accessibilityRole="tab"
            accessibilityState={{ selected: tab === key }}
            style={[styles.segmentBtn, tab === key ? styles.segmentActive : null]}
          >
            <Text style={[styles.segmentText, tab === key ? styles.segmentTextActive : null]}>
              {key === 'tickets' ? 'Tickets' : pendingCount ? `Cancellations (${pendingCount})` : 'Cancellations'}
            </Text>
          </Pressable>
        ))}
      </View>
      {tab === 'tickets' ? <KitchenTickets /> : <CancellationRequests />}
    </View>
  );
};

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  segment: {
    flexDirection: 'row',
    margin: spacing.lg,
    marginBottom: 0,
    padding: 3,
    borderRadius: radii.md,
    backgroundColor: colors.surfaceLight,
  },
  segmentBtn: { flex: 1, minHeight: 40, borderRadius: radii.sm, alignItems: 'center', justifyContent: 'center' },
  segmentActive: { backgroundColor: colors.surface },
  segmentText: { ...typography.caption, color: colors.textSecondary, fontFamily: fonts.semibold },
  segmentTextActive: { color: colors.text },
});
