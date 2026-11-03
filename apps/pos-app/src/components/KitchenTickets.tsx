import React, { useEffect, useMemo, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { Badge, BadgeProps, Button, colors, fonts, humanizeLabel, radii, spacing, typography } from '@repo/shared-ui';
import { KitchenStation } from '@repo/shared-utils';
import {
  ApiError,
  BoardTicket,
  SUPPORTED_STATIONS,
  useAdvanceTicket,
  useKitchenBoard,
} from '@repo/api-client';
import { QueryState } from './QueryState';
import { useDeviceStore } from '../stores/deviceStore';

const errorText = (e: unknown) => (e instanceof ApiError ? e.message : 'Something went wrong. Try again.');
const kotLabel = (kotNumber: string) => kotNumber.split('-').pop();

const WARN_AFTER_MIN = 10;
const LATE_AFTER_MIN = 20;

const minutesSince = (iso: string, now: number) => Math.max(0, Math.floor((now - new Date(iso).getTime()) / 60000));

/** 'just now', '12 min', '2h 5m', '3d': a long-forgotten ticket should still read at a glance. */
const formatWait = (minutes: number) => {
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes} min`;
  if (minutes < 24 * 60) return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
  return `${Math.floor(minutes / (24 * 60))}d`;
};

const STATUS_BADGE: Record<BoardTicket['status'], BadgeProps['variant']> = {
  queued: 'neutral',
  preparing: 'warning',
  ready: 'available',
  served: 'primary',
  voided: 'danger',
};

/**
 * The kitchen's queue: open tickets oldest first, with one button per stage. The waiter marks a
 * ready ticket served from the floor, so a ready card only waits there.
 */
export const KitchenTickets: React.FC = () => {
  const device = useDeviceStore((s) => s.device)!;
  const board = useKitchenBoard(device.branchId);
  const advance = useAdvanceTicket();

  const [station, setStation] = useState<KitchenStation | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Re-render every 30s so the "waiting" minutes keep moving between refreshes.
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 30000);
    return () => clearInterval(timer);
  }, []);

  const all = board.data ?? [];
  const tickets = useMemo(() => (station ? all.filter((t) => t.station === station) : all), [all, station]);
  const countFor = (s: KitchenStation | null) => (s ? all.filter((t) => t.station === s).length : all.length);

  const move = async (ticket: BoardTicket, status: 'preparing' | 'ready') => {
    setError(null);
    setBusyId(ticket.id);
    try {
      await advance.mutateAsync({ kotId: ticket.id, status });
    } catch (e) {
      setError(errorText(e));
      void board.refetch();
    } finally {
      setBusyId(null);
    }
  };

  return (
    <View style={styles.container}>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.chipsRow} contentContainerStyle={styles.chips}>
        {([null, ...SUPPORTED_STATIONS] as (KitchenStation | null)[]).map((s) => (
          <Pressable
            key={s ?? 'all'}
            onPress={() => setStation(s)}
            accessibilityRole="tab"
            accessibilityState={{ selected: station === s }}
            style={[styles.chip, station === s ? styles.chipActive : null]}
          >
            <Text style={[styles.chipText, station === s ? styles.chipTextActive : null]}>
              {s ?? 'All'} ({countFor(s)})
            </Text>
          </Pressable>
        ))}
      </ScrollView>

      <QueryState query={board} isEmpty={tickets.length === 0} emptyText="No tickets waiting.">
        <ScrollView contentContainerStyle={styles.scroll}>
          {error ? <Text style={styles.error}>{error}</Text> : null}
          {tickets.map((ticket) => {
            const waited = minutesSince(ticket.created_at, now);
            const late = waited >= LATE_AFTER_MIN;
            const slow = waited >= WARN_AFTER_MIN;
            return (
              <View key={ticket.id} style={[styles.card, ticket.status === 'ready' ? styles.cardReady : null]}>
                <View style={styles.head}>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.title}>
                      {ticket.table_label} · KOT {kotLabel(ticket.kot_number)}
                    </Text>
                    <Text style={styles.meta}>{humanizeLabel(ticket.station)}</Text>
                  </View>
                  <View style={styles.badges}>
                    <Badge label={ticket.status === 'ready' ? 'Ready' : ticket.status} variant={STATUS_BADGE[ticket.status]} size="sm" />
                    <Text style={[styles.waited, slow ? styles.waitedSlow : null, late ? styles.waitedLate : null]}>
                      {formatWait(waited)}
                    </Text>
                  </View>
                </View>

                {ticket.items.map((item, index) => (
                  <View key={`${item.name}-${index}`}>
                    <Text style={styles.item}>
                      {item.quantity} × {item.name}
                    </Text>
                    {item.notes ? <Text style={styles.note}>{item.notes}</Text> : null}
                  </View>
                ))}

                {ticket.status === 'queued' ? (
                  <Button
                    title="Start preparing"
                    size="lg"
                    onPress={() => move(ticket, 'preparing')}
                    loading={busyId === ticket.id}
                  />
                ) : ticket.status === 'preparing' ? (
                  <Button
                    title="Mark ready"
                    size="lg"
                    variant="success"
                    onPress={() => move(ticket, 'ready')}
                    loading={busyId === ticket.id}
                  />
                ) : (
                  <Text style={styles.waiting}>Ready · waiting for the waiter to pick it up</Text>
                )}
              </View>
            );
          })}
        </ScrollView>
      </QueryState>
    </View>
  );
};

const styles = StyleSheet.create({
  container: { flex: 1 },
  chipsRow: { flexGrow: 0 },
  chips: { gap: spacing.sm, paddingHorizontal: spacing.lg, paddingVertical: spacing.md },
  chip: {
    minHeight: 40,
    paddingHorizontal: spacing.lg,
    borderRadius: radii.full,
    borderWidth: 1,
    borderColor: colors.surfaceBorder,
    backgroundColor: colors.surface,
    justifyContent: 'center',
  },
  chipActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  chipText: { ...typography.caption, color: colors.textSecondary, fontFamily: fonts.semibold },
  chipTextActive: { color: colors.textInverse },
  scroll: { padding: spacing.lg, paddingTop: 0, gap: spacing.md, maxWidth: 640, width: '100%', alignSelf: 'center' },
  card: {
    backgroundColor: colors.surface,
    borderRadius: radii.lg,
    borderWidth: 1,
    borderColor: colors.surfaceBorder,
    padding: spacing.lg,
    gap: spacing.sm,
  },
  cardReady: { opacity: 0.7 },
  head: { flexDirection: 'row', alignItems: 'flex-start', gap: spacing.sm },
  title: { ...typography.bodyBold, color: colors.text },
  meta: { ...typography.caption, color: colors.textSecondary },
  badges: { alignItems: 'flex-end', gap: spacing.xs },
  waited: { ...typography.caption, color: colors.textSecondary, fontFamily: fonts.semibold },
  waitedSlow: { color: colors.billed },
  waitedLate: { color: colors.danger },
  item: { ...typography.body, color: colors.text },
  note: { ...typography.caption, color: colors.textSecondary },
  waiting: { ...typography.caption, color: colors.textSecondary, marginTop: spacing.xs },
  error: { ...typography.caption, color: colors.danger },
});
