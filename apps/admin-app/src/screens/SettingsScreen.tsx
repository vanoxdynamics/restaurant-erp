import React from 'react';
import { ScrollView, StyleSheet, Text, View } from 'react-native';
import { Card, colors, fonts, spacing, typography } from '@repo/shared-ui';
import { useEntitlements, type BusinessSettings } from '@repo/api-client';
import { StaffRole } from '@repo/shared-utils';
import { BusinessProfileCard } from '../components/BusinessProfileCard';
import { SettingsCard as ApparelSettingsCard } from './apparel/SettingsCard';
import { Icon } from '../components/Icon';
import { useAuthStore } from '../stores/authStore';

const CATEGORY_NAMES: Record<string, string> = {
  restaurant: 'Restaurant',
  cafe: 'Café / QSR / Bakery',
  grocery: 'Grocery / Kirana',
  retail: 'General retail',
  apparel: 'Dress shop / Clothing',
  hardware: 'Hardware store',
};

/** What each switched-on feature means, in plain words. */
const FEATURES: { key: keyof BusinessSettings; title: string; text: string }[] = [
  { key: 'table_service', title: 'Tables', text: 'Take orders for customers seated at tables' },
  { key: 'kitchen_tickets', title: 'Kitchen tickets', text: 'Send orders to the kitchen to prepare' },
  { key: 'quick_sale', title: 'Quick sale', text: 'Bill a customer at the counter in a few taps' },
  { key: 'barcode', title: 'Barcodes', text: 'Scan item barcodes to add them to a bill' },
  { key: 'weighed_items', title: 'Loose items', text: 'Sell items by weight or quantity' },
  { key: 'tokens', title: 'Tokens', text: 'Give customers a token number for pickup' },
];

/** The business's settings. Your own account (name, branch, signing out) is in Profile, behind the avatar. */
export const SettingsScreen: React.FC = () => {
  const { currentUser } = useAuthStore();
  const business = useEntitlements().data;
  const canEdit = currentUser?.role === StaffRole.OWNER || currentUser?.role === StaffRole.MANAGER;
  const enabled = business ? FEATURES.filter((f) => business.settings[f.key]) : [];

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Text style={[typography.h1, { color: colors.text, marginBottom: spacing.xl }]}>Settings</Text>

      <Text style={styles.sectionTitle}>Bill header</Text>
      <Text style={styles.sectionHelp}>The logo and name customers see at the top of every printed bill.</Text>
      <View style={styles.block}>
        <BusinessProfileCard canEdit={canEdit} />
      </View>

      {business?.category === 'apparel' ? (
        <>
          <Text style={styles.sectionTitle}>Dress shop</Text>
          <ApparelSettingsCard canEdit={currentUser?.role === StaffRole.OWNER || currentUser?.role === StaffRole.MANAGER} />
        </>
      ) : null}

      {business ? (
        <>
          <Text style={styles.sectionTitle}>Your business</Text>
          <Text style={styles.sectionHelp}>{CATEGORY_NAMES[business.category] ?? business.category}</Text>
          <Card style={styles.card}>
            {enabled.length > 0 ? (
              enabled.map((f) => (
                <View key={f.key} style={styles.feature}>
                  <Icon name="check" size={16} color={colors.primary} />
                  <View style={{ flex: 1 }}>
                    <Text style={styles.label}>{f.title}</Text>
                    <Text style={styles.sub}>{f.text}</Text>
                  </View>
                </View>
              ))
            ) : (
              <Text style={styles.sub}>No extra features are switched on.</Text>
            )}
            <Text style={[styles.sub, styles.note]}>These are set for your plan. Contact Vanox support to change them.</Text>
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
    ...typography.bodyBold,
    color: colors.text,
    fontFamily: fonts.semibold,
  },
  sectionHelp: {
    ...typography.caption,
    color: colors.textSecondary,
    marginTop: 2,
    marginBottom: spacing.sm,
  },
  block: {
    marginBottom: spacing.xl,
  },
  card: {
    marginBottom: spacing.xl,
    gap: spacing.md,
  },
  feature: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: spacing.sm,
  },
  label: {
    ...typography.bodyBold,
    color: colors.text,
  },
  sub: {
    ...typography.caption,
    color: colors.textSecondary,
  },
  note: {
    marginTop: spacing.xs,
    color: colors.textMuted,
  },
});
