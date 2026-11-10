import React from 'react';
import { Pressable, StyleSheet, Text } from 'react-native';
import { colors, fonts, radii, spacing, typography } from '@repo/shared-ui';
import { useCashDay } from '@repo/api-client';
import { IS_COUNTER } from '../device';
import { useKitchenPlan } from '../plan';
import { useSessionStore } from '../stores/sessionStore';
import { Icon } from './Icon';

/** Whether the signed-in person handles the counter's cash (the same people who take payment). */
function useHandlesCash(): boolean {
  const role = useSessionStore((s) => s.user?.role);
  const plan = useKitchenPlan();
  return IS_COUNTER && plan.canBill(role);
}

/** The cash day for the counter's buttons, or undefined for people who do not handle cash. */
export function useCounterCashDay() {
  const handles = useHandlesCash();
  const query = useCashDay(handles);
  return handles ? query.data : undefined;
}

/** "Open day" or "Close day", for the counter's top-bar button. */
export function cashButtonLabel(status?: string): string {
  return status === 'open' ? 'Close day' : 'Open day';
}

/**
 * The reminder on the counter's home screen: the day is not open yet, or a day was left open from an earlier date (the app
 * was closed without closing it). Tapping it goes to the cash screen.
 */
export const CashDayBanner: React.FC<{ onPress: () => void }> = ({ onPress }) => {
  const cashDay = useCounterCashDay();
  if (!cashDay || (cashDay.status === 'open' && !cashDay.stale)) return null;
  const stale = cashDay.status === 'open' && cashDay.stale;
  return (
    <Pressable onPress={onPress} accessibilityRole="button" style={[styles.banner, stale ? styles.stale : null]}>
      <Icon name={stale ? 'alert-triangle' : 'sun'} size={16} color={stale ? colors.danger : colors.warning} />
      <Text style={[styles.text, stale ? { color: colors.danger } : null]}>
        {stale
          ? 'An earlier day was never closed. Tap to count the cash and close it.'
          : 'The day is not open yet. Tap to count the drawer and open it.'}
      </Text>
    </Pressable>
  );
};

const styles = StyleSheet.create({
  banner: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    marginHorizontal: spacing.lg,
    marginTop: spacing.md,
    padding: spacing.md,
    borderRadius: radii.md,
    backgroundColor: colors.warningBg,
  },
  stale: { backgroundColor: colors.dangerBg },
  text: { ...typography.caption, color: colors.warning, fontFamily: fonts.semibold, flex: 1 },
});
