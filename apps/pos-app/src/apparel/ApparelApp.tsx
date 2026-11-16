import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { colors, spacing, typography } from '@repo/shared-ui';
import { useBusinessProfile } from '@repo/api-client';
import { TopBar } from '../components/TopBar';

/** The dress shop counter. Its screens arrive in later stages; until then it says so. */
export function ApparelApp(): React.JSX.Element {
  const profile = useBusinessProfile().data;
  return (
    <View style={styles.container}>
      <TopBar title={profile?.name ?? 'Dress shop'} subtitle="Dress shop" />
      <View style={styles.body}>
        <Text style={styles.title}>{profile?.name ?? ' '}</Text>
        <Text style={styles.text}>Coming soon</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  body: { padding: spacing.xl, gap: spacing.md, alignItems: 'center' },
  title: { ...typography.h3, color: colors.text, textAlign: 'center' },
  text: { ...typography.body, color: colors.textSecondary, textAlign: 'center' },
});
