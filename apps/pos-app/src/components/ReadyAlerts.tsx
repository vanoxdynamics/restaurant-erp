import React, { useEffect, useRef, useState } from 'react';
import { Platform, Pressable, StyleSheet, Text, Vibration, View } from 'react-native';
import { colors, fonts, radii, shadows, spacing, typography } from '@repo/shared-ui';
import { StaffRole } from '@repo/shared-utils';
import { apiClient, Notice, NoticeKind } from '@repo/api-client';
import { IS_COUNTER } from '../device';
import { useSessionStore } from '../stores/sessionStore';
import { Icon, IconName } from './Icon';

/** How often the phone asks, and how long a notice stays up when nobody taps it. */
const POLL_MS = 5000;
const SHOW_MS = 15000;
/** Waiters and captains everywhere; managers and owners only on a phone (the counter is busy billing). */
const FLOOR_ROLES: StaffRole[] = [StaffRole.WAITER, StaffRole.CAPTAIN];
const PHONE_ROLES: StaffRole[] = [StaffRole.MANAGER, StaffRole.OWNER];

const LOOK: Record<NoticeKind, { icon: IconName; background: string; urgent: boolean }> = {
  food_ready: { icon: 'bell', background: colors.success, urgent: true },
  kot_printed: { icon: 'printer', background: colors.primary, urgent: true },
  bill_paid: { icon: 'check-circle', background: colors.primary, urgent: false },
  cancel_approved: { icon: 'check', background: colors.success, urgent: false },
  cancel_declined: { icon: 'x-circle', background: colors.danger, urgent: true },
  kot_cancelled: { icon: 'alert-triangle', background: colors.danger, urgent: true },
  sold_out: { icon: 'slash', background: colors.warning, urgent: false },
};

interface Shown {
  notice: Notice;
  shownAt: number;
}

/** A short beep in the browser preview (phones vibrate instead). */
function beep() {
  try {
    const Ctx = (globalThis as unknown as { AudioContext?: typeof AudioContext }).AudioContext;
    if (!Ctx) return;
    const ctx = new Ctx();
    const osc = ctx.createOscillator();
    osc.frequency.value = 880;
    osc.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.15);
  } catch {
    // no sound available: the banner is enough
  }
}

/**
 * Banners for floor staff while the app is open: food ready, a KOT printed at the counter to take to the kitchen, a cancel
 * request answered, the kitchen cancelling a ticket, a table's bill paid, an item sold out. Waiters hear about their own orders
 * (the server decides). Urgent ones vibrate. Tapping a banner opens its order.
 */
export const ReadyAlerts: React.FC<{ onOpenOrder: (orderId: string, title: string) => void }> = ({ onOpenOrder }) => {
  const user = useSessionStore((s) => s.user);
  const active = !!user && (FLOOR_ROLES.includes(user.role) || (!IS_COUNTER && PHONE_ROLES.includes(user.role)));
  const [shown, setShown] = useState<Shown[]>([]);
  const since = useRef<string | undefined>(undefined);
  const seen = useRef<Set<string>>(new Set());

  useEffect(() => {
    since.current = undefined;
    seen.current = new Set();
    setShown([]);
    if (!active) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const { now, notices } = await apiClient.getNotices(since.current);
        if (stopped) return;
        since.current = now;
        const fresh = notices.filter((n) => !seen.current.has(n.id));
        fresh.forEach((n) => seen.current.add(n.id));
        if (fresh.length) {
          setShown((current) => [...current, ...fresh.map((notice) => ({ notice, shownAt: Date.now() }))].slice(-3));
          if (fresh.some((n) => LOOK[n.kind]?.urgent)) {
            if (Platform.OS === 'web') beep();
            else Vibration.vibrate([0, 250, 120, 250]);
          }
        }
      } catch {
        // a missed poll is retried; the window overlaps, so nothing is lost
      } finally {
        if (!stopped) timer = setTimeout(poll, POLL_MS);
      }
    };
    void poll();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }, [active, user?.id]);

  useEffect(() => {
    if (!shown.length) return;
    const timer = setTimeout(() => setShown((current) => current.filter((s) => Date.now() - s.shownAt < SHOW_MS)), 1000);
    return () => clearTimeout(timer);
  }, [shown]);

  if (!active || !shown.length) return null;
  const dismiss = (id: string) => setShown((current) => current.filter((s) => s.notice.id !== id));

  return (
    <View style={styles.stack} pointerEvents="box-none">
      {shown.map(({ notice }) => {
        const look = LOOK[notice.kind] ?? LOOK.bill_paid;
        const where = notice.title.split(' · ')[1] ?? notice.title;
        return (
          <Pressable
            key={notice.id}
            onPress={() => {
              dismiss(notice.id);
              if (notice.order_id && notice.kind !== 'bill_paid') onOpenOrder(notice.order_id, where);
            }}
            accessibilityRole="alert"
            accessibilityLabel={`${notice.title}. ${notice.detail ?? ''}`}
            style={[styles.alert, { backgroundColor: look.background }]}
          >
            <Icon name={look.icon} size={18} color={colors.textInverse} />
            <View style={{ flex: 1 }}>
              <Text style={styles.title}>{notice.title}</Text>
              {notice.detail ? (
                <Text style={styles.detail} numberOfLines={2}>
                  {notice.detail}
                </Text>
              ) : null}
            </View>
            <Pressable onPress={() => dismiss(notice.id)} hitSlop={10} accessibilityRole="button" accessibilityLabel="Dismiss">
              <Icon name="x" size={18} color={colors.textInverse} />
            </Pressable>
          </Pressable>
        );
      })}
    </View>
  );
};

const styles = StyleSheet.create({
  stack: { position: 'absolute', top: spacing.md, left: spacing.md, right: spacing.md, gap: spacing.sm, zIndex: 50 },
  alert: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    padding: spacing.md,
    borderRadius: radii.lg,
    maxWidth: 560,
    width: '100%',
    alignSelf: 'center',
    ...shadows.raised,
  },
  title: { ...typography.bodyBold, color: colors.textInverse, fontFamily: fonts.bold },
  detail: { ...typography.caption, color: colors.textInverse },
});
