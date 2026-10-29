import React, { useState } from 'react';
import { KeyboardAvoidingView, Platform, StyleSheet, Text, View } from 'react-native';
import { Button, colors, fonts, Input, radii, spacing, typography } from '@repo/shared-ui';
import { ApiError } from '@repo/api-client';
import { useDeviceStore } from '../stores/deviceStore';

/** One-time setup: a manager links this tablet to their branch. */
export const DeviceSetupScreen: React.FC = () => {
  const register = useDeviceStore((s) => s.register);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (!username.trim() || !password || busy) return;
    setBusy(true);
    setError(null);
    try {
      await register(username, password);
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
        <Text style={styles.subtitle}>A manager signs in once to link this tablet to the branch.</Text>

        <Input label="Manager username" value={username} onChangeText={setUsername} autoCapitalize="none" autoCorrect={false} />
        <Input label="Password" value={password} onChangeText={setPassword} secureTextEntry onSubmitEditing={submit} />

        {error ? <Text style={styles.error}>{error}</Text> : null}
        <Button title="Link device" size="lg" onPress={submit} loading={busy} disabled={!username.trim() || !password} />
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
