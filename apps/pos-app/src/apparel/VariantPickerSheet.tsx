import React, { useMemo } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { colors, fonts, Modal, radii, spacing, typography } from '@repo/shared-ui';
import { formatPaiseToRupees } from '@repo/shared-utils';
import type { ApparelProduct, ApparelVariant } from '@repo/api-client';

export interface VariantPickerSheetProps {
  product: ApparelProduct | null;
  /** Pieces of each variant in the cart, so a tap shows what is already picked. */
  inCart: Record<string, number>;
  onClose: () => void;
  onPick: (product: ApparelProduct, variant: ApparelVariant) => void;
}

/**
 * Sizes across, colours down. Each cell shows the pieces on the shelf and adds one when tapped. A cell with nothing left is
 * dimmed but still tappable: the count on record can be behind the shelf, and the customer is holding the garment.
 */
export const VariantPickerSheet: React.FC<VariantPickerSheetProps> = ({ product, inCart, onClose, onPick }) => {
  const { sizes, colours, cells } = useMemo(() => {
    const variants = (product?.variants ?? []).filter((v) => v.is_active).sort((a, b) => a.sort_order - b.sort_order);
    const sizes: string[] = [];
    const colours: string[] = [];
    const cells = new Map<string, ApparelVariant>();
    for (const v of variants) {
      if (!sizes.includes(v.size)) sizes.push(v.size);
      if (!colours.includes(v.colour)) colours.push(v.colour);
      cells.set(`${v.colour}|${v.size}`, v);
    }
    return { sizes, colours, cells };
  }, [product]);

  const price = (v: ApparelVariant) =>
    v.mrp_paise && v.mrp_paise > v.price_paise ? `${formatPaiseToRupees(v.price_paise)} (MRP ${formatPaiseToRupees(v.mrp_paise)})` : formatPaiseToRupees(v.price_paise);
  const first = product?.variants.find((v) => v.is_active);

  return (
    <Modal visible={!!product} onClose={onClose} title={product?.name ?? ''} subtitle={first ? price(first) : undefined}>
      <ScrollView horizontal showsHorizontalScrollIndicator={false}>
        <View style={{ gap: spacing.sm }}>
          <View style={styles.row}>
            <View style={styles.colourHead} />
            {sizes.map((s) => (
              <Text key={s} style={styles.sizeHead}>{s}</Text>
            ))}
          </View>
          {colours.map((colour) => (
            <View key={colour} style={styles.row}>
              <Text style={styles.colourHead} numberOfLines={2}>{colour}</Text>
              {sizes.map((size) => {
                const v = cells.get(`${colour}|${size}`);
                if (!v || !product) return <View key={size} style={styles.cellGap} />;
                const left = v.on_hand ?? 0;
                const picked = inCart[v.id] ?? 0;
                return (
                  <Pressable
                    key={size}
                    onPress={() => onPick(product, v)}
                    accessibilityRole="button"
                    accessibilityLabel={`${product.name}, ${size}, ${colour}, ${left} in stock${picked ? `, ${picked} in the sale` : ''}`}
                    style={({ pressed }) => [styles.cell, left <= 0 ? styles.cellEmpty : null, picked ? styles.cellPicked : null, pressed ? styles.cellPicked : null]}
                  >
                    <Text style={[styles.left, left <= 0 ? { color: colors.danger } : null]}>{left <= 0 ? '0' : left}</Text>
                    <Text style={styles.leftLabel}>{picked ? `${picked} in sale` : 'in stock'}</Text>
                  </Pressable>
                );
              })}
            </View>
          ))}
        </View>
      </ScrollView>
      <Text style={styles.hint}>Tap a cell to add one piece. Tap again for another.</Text>
    </Modal>
  );
};

const CELL = 76;

const styles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  colourHead: { width: 84, ...typography.caption, color: colors.textSecondary, fontFamily: fonts.semibold },
  sizeHead: { width: CELL, textAlign: 'center', ...typography.bodyBold, color: colors.text },
  cellGap: { width: CELL, height: CELL },
  cell: {
    width: CELL, height: CELL, borderRadius: radii.md, borderWidth: 1, borderColor: colors.surfaceBorder, backgroundColor: colors.surface,
    alignItems: 'center', justifyContent: 'center',
  },
  cellEmpty: { backgroundColor: colors.surfaceLight, opacity: 0.7 },
  cellPicked: { borderColor: colors.primary, backgroundColor: colors.primaryGlow },
  left: { ...typography.h3, color: colors.text, fontFamily: fonts.bold },
  leftLabel: { ...typography.label, color: colors.textMuted },
  hint: { ...typography.caption, color: colors.textMuted, marginTop: spacing.md },
});
