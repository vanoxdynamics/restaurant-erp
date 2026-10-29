import React, { useMemo, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { colors, fonts, Input, radii, spacing, typography } from '@repo/shared-ui';
import { formatPaiseToRupees } from '@repo/shared-utils';
import { MenuItem, useCatalog } from '@repo/api-client';
import { QueryState } from './QueryState';

export interface MenuPanelProps {
  onPick: (item: MenuItem) => void;
}

export const MenuPanel: React.FC<MenuPanelProps> = ({ onPick }) => {
  const catalogQuery = useCatalog();
  const categories = catalogQuery.data ?? [];
  const [categoryId, setCategoryId] = useState<string | null>(null);
  const [search, setSearch] = useState('');

  const items = useMemo(() => {
    const term = search.trim().toLowerCase();
    const pool = term
      ? categories.flatMap((c) => c.items)
      : (categories.find((c) => c.id === categoryId) ?? categories[0])?.items ?? [];
    return term ? pool.filter((i) => i.name.toLowerCase().includes(term)) : pool;
  }, [categories, categoryId, search]);

  const activeCategoryId = categoryId ?? categories[0]?.id;

  return (
    <View style={styles.panel}>
      <Input placeholder="Search the menu" value={search} onChangeText={setSearch} containerStyle={{ marginBottom: spacing.md }} />

      {!search ? (
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.tabs} style={styles.tabsRow}>
          {categories.map((c) => (
            <TouchableOpacity
              key={c.id}
              onPress={() => setCategoryId(c.id)}
              style={[styles.tab, activeCategoryId === c.id ? styles.tabActive : null]}
              accessibilityRole="tab"
              accessibilityState={{ selected: activeCategoryId === c.id }}
            >
              <Text style={[styles.tabText, activeCategoryId === c.id ? styles.tabTextActive : null]}>{c.name}</Text>
            </TouchableOpacity>
          ))}
        </ScrollView>
      ) : null}

      <QueryState query={catalogQuery} isEmpty={items.length === 0} emptyText={search ? 'No items match.' : 'No items in this category.'}>
        <ScrollView contentContainerStyle={styles.grid} keyboardShouldPersistTaps="handled">
          {items.map((item) => {
            const soldOut = !item.is_available_86;
            const fromPrice = item.variants.length
              ? Math.min(...item.variants.map((v) => v.price_paise))
              : item.base_price_paise;
            return (
              <Pressable
                key={item.id}
                onPress={() => onPick(item)}
                disabled={soldOut}
                accessibilityRole="button"
                accessibilityLabel={soldOut ? `${item.name}, sold out` : `Add ${item.name}`}
                style={({ pressed }) => [styles.item, soldOut ? styles.itemSoldOut : null, pressed ? styles.itemPressed : null]}
              >
                <Text style={[styles.itemName, soldOut ? { color: colors.textMuted } : null]} numberOfLines={2}>
                  {item.name}
                </Text>
                <Text style={styles.itemPrice}>
                  {soldOut ? 'Sold out' : `${item.variants.length ? 'from ' : ''}${formatPaiseToRupees(fromPrice, { decimals: 0 })}`}
                </Text>
              </Pressable>
            );
          })}
        </ScrollView>
      </QueryState>
    </View>
  );
};

const styles = StyleSheet.create({
  panel: {
    flex: 1,
    padding: spacing.lg,
  },
  tabsRow: {
    flexGrow: 0,
    marginBottom: spacing.md,
  },
  tabs: {
    gap: spacing.sm,
  },
  tab: {
    minHeight: 40,
    paddingHorizontal: spacing.lg,
    borderRadius: radii.full,
    borderWidth: 1,
    borderColor: colors.surfaceBorder,
    backgroundColor: colors.surface,
    justifyContent: 'center',
  },
  tabActive: {
    backgroundColor: colors.primary,
    borderColor: colors.primary,
  },
  tabText: {
    ...typography.caption,
    color: colors.textSecondary,
    fontFamily: fonts.semibold,
  },
  tabTextActive: {
    color: colors.textInverse,
  },
  grid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.md,
    paddingBottom: spacing.xl,
  },
  item: {
    width: 150,
    minHeight: 92,
    padding: spacing.md,
    borderRadius: radii.lg,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.surfaceBorder,
    justifyContent: 'space-between',
  },
  itemSoldOut: {
    backgroundColor: colors.surfaceLight,
  },
  itemPressed: {
    borderColor: colors.primary,
    backgroundColor: colors.primaryGlow,
  },
  itemName: {
    ...typography.bodyBold,
    color: colors.text,
  },
  itemPrice: {
    ...typography.caption,
    color: colors.textSecondary,
    marginTop: spacing.xs,
  },
});
