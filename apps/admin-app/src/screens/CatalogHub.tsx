import React, { useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { colors, fonts, radii, spacing } from '@repo/shared-ui';
import { useEntitlements } from '@repo/api-client';
import { MenuBuilderScreen } from './MenuBuilder';
import { StockScreen } from './StockScreen';

type CatalogView = 'items' | 'stock';

/**
 * The Menu tab. A business with the `inventory` module gets a switch between its items and their stock; everyone else sees the
 * item editor alone, as before.
 */
export const CatalogHub: React.FC = () => {
  const entitlements = useEntitlements().data;
  const [view, setView] = useState<CatalogView>('items');
  if (!entitlements?.modules.includes('inventory')) return <MenuBuilderScreen />;
  const shop = entitlements.category !== 'restaurant' && !entitlements.settings.kitchen_tickets;

  return (
    <View style={styles.container}>
      <View style={styles.segmentWrapper}>
        <View style={styles.segmented} accessibilityRole="tablist">
          {([
            ['items', shop ? 'Items' : 'Menu'],
            ['stock', 'Stock'],
          ] as const).map(([key, label]) => (
            <Pressable
              key={key}
              onPress={() => setView(key)}
              accessibilityRole="tab"
              accessibilityState={{ selected: view === key }}
              style={[styles.segment, view === key ? styles.segmentActive : null]}
            >
              <Text style={[styles.segmentText, view === key ? styles.segmentTextActive : null]}>{label}</Text>
            </Pressable>
          ))}
        </View>
      </View>
      <View style={{ flex: 1 }}>{view === 'items' ? <MenuBuilderScreen /> : <StockScreen />}</View>
    </View>
  );
};

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  segmentWrapper: { paddingHorizontal: spacing.xl, paddingTop: spacing.md },
  segmented: { flexDirection: 'row', backgroundColor: colors.surfaceLight, borderRadius: radii.md, padding: 3 },
  segment: { flex: 1, minHeight: 36, alignItems: 'center', justifyContent: 'center', borderRadius: radii.sm },
  segmentActive: { backgroundColor: colors.surface },
  segmentText: { fontSize: 13, fontFamily: fonts.medium, color: colors.textSecondary },
  segmentTextActive: { color: colors.text, fontFamily: fonts.semibold },
});
