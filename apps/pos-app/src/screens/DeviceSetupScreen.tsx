import React, { useState } from 'react';
import { KeyboardAvoidingView, Platform, StyleSheet, Text, View } from 'react-native';
import { Button, colors, fonts, Input, radii, spacing, typography } from '@repo/shared-ui';
import { ApiError } from '@repo/api-client';
import { useDeviceStore } from '../stores/deviceStore';

/** One-time setup: link this tablet to the branch with a code from a manager. */
export const DeviceSetupScreen: React.FC = () => {
  const register = useDeviceStore((s) => s.register);
  const [code, setCode] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const ready = code.replace('-', '').length === 8;

  const submit = async () => {
    if (!ready || busy) return;
    setBusy(true);
    setError(null);
    try {
      await register(code);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Setup failed. Try again.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <KeyboardAvoidingView style={styles.container} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <View style={styles.card}>
        <View style={styles.logo}>
          <Text style={styles.logoText}>R</Text>
        </View>
        <Text style={styles.title}>Set up this device</Text>
        <Text style={styles.subtitle}>
          Ask a manager for a setup code. They can make one in the admin app under Staff, then Link a device.
        </Text>

        <Input
          label="Setup code"
          value={code}
          onChangeText={(text) => setCode(text.toUpperCase())}
          placeholder="K7M2-9QXP"
          autoCapitalize="characters"
          autoCorrect={false}
          maxLength={9}
          onSubmitEditing={submit}
        />

        {error ? <Text style={styles.error}>{error}</Text> : null}
        <Button title="Link device" size="lg" onPress={submit} loading={busy} disabled={!ready} />
      </View>
    </KeyboardAvoidingView>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    justifyContent: 'center',
    padding: spacing.xl,
    backgroundColor: colors.background,
  },
  card: {
    width: '100%',
    maxWidth: 440,
    alignSelf: 'center',
    backgroundColor: colors.surface,
    borderRadius: radii.xl,
    borderWidth: 1,
    borderColor: colors.surfaceBorder,
    padding: spacing.xxl,
  },
  logo: {
    width: 48,
    height: 48,
    borderRadius: radii.lg,
    backgroundColor: colors.primary,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: spacing.lg,
  },
  logoText: {
    color: colors.textInverse,
    fontSize: 22,
    fontFamily: fonts.bold,
  },
  title: {
    ...typography.h1,
    color: colors.text,
  },
  subtitle: {
    ...typography.body,
    color: colors.textSecondary,
    marginTop: spacing.xs,
    marginBottom: spacing.xxl,
  },
  error: {
    ...typography.caption,
    color: colors.danger,
    marginBottom: spacing.md,
  },
});
