import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { colors, spacing, typography } from '@repo/shared-ui';
import { TopBar } from './TopBar';

/**
 * Restaurants and shops use different apps. Signed in to the wrong one, staff see which app their business uses instead of
 * screens that do not fit it.
 */
export const WrongApp: React.FC<{ expected: 'restaurant' | 'retail' }> = ({ expected }) => (
  <View style={styles.container}>
    <TopBar title={expected === 'retail' ? 'Restaurant app' : 'Retail app'} />
    <View style={styles.body}>
      <Text style={styles.title}>This app is not for your business</Text>
      <Text style={styles.text}>
        {expected === 'retail'
          ? 'Your business is set up as a café or shop. Install the Vanox Retail app on this device to make sales.'
          : 'Your business is set up as a restaurant. Install the Restaurant Counter app (or the waiter app on phones).'}
      </Text>
    </View>
  </View>
);

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  body: { padding: spacing.xl, gap: spacing.md, alignItems: 'center' },
  title: { ...typography.h3, color: colors.text, textAlign: 'center' },
  text: { ...typography.body, color: colors.textSecondary, textAlign: 'center', maxWidth: 480 },
});
