import React, { useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';
import { colors, fonts, radii, spacing, typography } from '@repo/shared-ui';
import { ApiError } from '@repo/api-client';
import { Icon } from '../components/Icon';
import { useDeviceStore } from '../stores/deviceStore';
import { useSessionStore } from '../stores/sessionStore';

const KEYS = ['1', '2', '3', '4', '5', '6', '7', '8', '9', 'clear', '0', 'back'] as const;
const MAX_PIN = 6;

/** Shared-tablet sign-in: each staff member enters their own 4–6 digit PIN. */
export const PinScreen: React.FC = () => {
  const device = useDeviceStore((s) => s.device)!;
  const signIn = useSessionStore((s) => s.signIn);
  const [pin, setPin] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (value = pin) => {
    if (value.length < 4 || busy) return;
    setBusy(true);
    setError(null);
    try {
      await signIn(device.branchId, value, device.deviceId);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Sign-in failed. Try again.');
      setPin('');
    } finally {
      setBusy(false);
    }
  };

  const press = (key: (typeof KEYS)[number]) => {
    setError(null);
    if (key === 'clear') return setPin('');
    if (key === 'back') return setPin((p) => p.slice(0, -1));
    if (pin.length >= MAX_PIN) return;
    setPin(pin + key);
  };

  return (
    <View style={styles.container}>
      <View style={styles.panel}>
        <Text style={styles.branch}>{device.branchName}</Text>
        <Text style={styles.title}>Enter your PIN</Text>

        <View style={styles.dots} accessibilityLabel={`${pin.length} digits entered`}>
          {Array.from({ length: Math.max(4, pin.length) }).map((_, i) => (
            <View key={i} style={[styles.dot, i < pin.length ? styles.dotFilled : null]} />
          ))}
        </View>

        <View style={styles.message}>
          {busy ? <ActivityIndicator color={colors.primary} /> : error ? <Text style={styles.error}>{error}</Text> : null}
        </View>

        <View style={styles.pad}>
          {KEYS.map((key) => (
            <Pressable
              key={key}
              onPress={() => press(key)}
              disabled={busy}
              accessibilityRole="button"
              accessibilityLabel={key === 'back' ? 'Delete' : key === 'clear' ? 'Clear' : key}
              style={({ pressed }) => [styles.key, key.length > 1 ? styles.keyFn : null, pressed ? styles.keyPressed : null]}
            >
              {key === 'back' ? (
                <Icon name="delete" size={22} color={colors.text} />
              ) : (
                <Text style={key === 'clear' ? styles.keyFnText : styles.keyText}>{key === 'clear' ? 'Clear' : key}</Text>
              )}
            </Pressable>
          ))}
        </View>

        <Pressable
          onPress={() => submit()}
          disabled={pin.length < 4 || busy}
          accessibilityRole="button"
          accessibilityLabel="Sign in"
          style={({ pressed }) => [
            styles.enter,
            pin.length < 4 || busy ? styles.enterDisabled : null,
            pressed ? { backgroundColor: colors.primaryDark } : null,
          ]}
        >
          <Text style={styles.enterText}>Sign in</Text>
        </Pressable>
      </View>
    </View>
  );
};

const KEY_SIZE = 76;

const styles = StyleSheet.create({
  container: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.background,
    padding: spacing.xl,
  },
  panel: {
    width: '100%',
    maxWidth: 360,
    alignItems: 'center',
  },
  branch: {
    ...typography.caption,
    color: colors.textMuted,
  },
  title: {
    ...typography.h1,
    color: colors.text,
    marginTop: spacing.xs,
  },
  dots: {
    flexDirection: 'row',
    gap: spacing.md,
    marginTop: spacing.xl,
  },
  dot: {
    width: 14,
    height: 14,
    borderRadius: 7,
    borderWidth: 2,
    borderColor: colors.textMuted,
  },
  dotFilled: {
    backgroundColor: colors.primary,
    borderColor: colors.primary,
  },
  message: {
    height: 40,
    justifyContent: 'center',
  },
  error: {
    ...typography.caption,
    color: colors.danger,
    textAlign: 'center',
  },
  pad: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    justifyContent: 'center',
    gap: spacing.md,
    width: KEY_SIZE * 3 + spacing.md * 2,
  },
  key: {
    width: KEY_SIZE,
    height: KEY_SIZE,
    borderRadius: KEY_SIZE / 2,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.surfaceBorder,
    alignItems: 'center',
    justifyContent: 'center',
  },
  keyFn: {
    backgroundColor: 'transparent',
    borderColor: 'transparent',
  },
  keyPressed: {
    backgroundColor: colors.surfaceLight,
  },
  keyText: {
    fontSize: 28,
    fontFamily: fonts.medium,
    color: colors.text,
  },
  keyFnText: {
    ...typography.bodyBold,
    color: colors.textSecondary,
  },
  enter: {
    marginTop: spacing.xl,
    width: KEY_SIZE * 3 + spacing.md * 2,
    minHeight: 52,
    borderRadius: radii.md,
    backgroundColor: colors.primary,
    alignItems: 'center',
    justifyContent: 'center',
  },
  enterDisabled: {
    opacity: 0.4,
  },
  enterText: {
    ...typography.bodyBold,
    color: colors.textInverse,
  },
});
