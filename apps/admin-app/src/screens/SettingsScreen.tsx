import React from 'react';
import { ScrollView, StyleSheet, Text } from 'react-native';
import { Card, colors, fonts, spacing, typography } from '@repo/shared-ui';
import { useEntitlements } from '@repo/api-client';
import { StaffRole } from '@repo/shared-utils';
import { BusinessProfileCard } from '../components/BusinessProfileCard';
import { useAuthStore } from '../stores/authStore';

const CATEGORY_NAMES: Record<string, string> = {
  restaurant: 'Restaurant',
  cafe: 'Café / QSR / Bakery',
  grocery: 'Grocery / Kirana',
  retail: 'General retail',
};

/** The business's settings. Your own account (name, branch, signing out) is in Profile, behind the avatar. */
export const SettingsScreen: React.FC = () => {
  const { currentUser } = useAuthStore();
  const business = useEntitlements().data;

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Text style={[typography.h1, { color: colors.text, marginBottom: spacing.lg }]}>Settings</Text>

      <Text style={styles.sectionTitle}>Name and logo on bills</Text>
      <BusinessProfileCard canEdit={currentUser?.role === StaffRole.OWNER || currentUser?.role === StaffRole.MANAGER} />

      {business ? (
        <>
          <Text style={styles.sectionTitle}>Business</Text>
          <Card style={styles.card}>
            <Text style={styles.label}>{CATEGORY_NAMES[business.category] ?? business.category}</Text>
            <Text style={styles.sub}>
              {[
                business.settings.table_service ? 'Tables' : null,
                business.settings.kitchen_tickets ? 'Kitchen tickets' : null,
                business.settings.quick_sale ? 'Quick sale' : null,
                business.settings.barcode ? 'Barcodes' : null,
                business.settings.weighed_items ? 'Loose items' : null,
                business.settings.tokens ? 'Tokens' : null,
              ].filter(Boolean).join(' · ')}
            </Text>
            <Text style={[styles.sub, { marginTop: spacing.xs }]}>Set by Vanox. Contact support to change it.</Text>
          </Card>
        </>
      ) : null}
    </ScrollView>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.background,
  },
  content: {
    paddingHorizontal: spacing.xl,
    paddingTop: spacing.md,
    paddingBottom: spacing.xxxl,
  },
  sectionTitle: {
    ...typography.caption,
    color: colors.textMuted,
    fontFamily: fonts.semibold,
    marginBottom: spacing.sm,
  },
  card: {
    marginBottom: spacing.xl,
  },
  label: {
    ...typography.bodyBold,
    color: colors.text,
  },
  sub: {
    ...typography.caption,
    color: colors.textMuted,
  },
});
