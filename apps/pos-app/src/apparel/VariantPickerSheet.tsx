import React, { useEffect, useMemo, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { colors, fonts, Modal, radii, spacing, typography } from '@repo/shared-ui';
import { canBill, formatPaiseToRupees, NEGATIVE_STOCK_LIMIT } from '@repo/shared-utils';
import type { ApparelProduct, ApparelVariant } from '@repo/api-client';

export interface VariantPickerSheetProps {
  product: ApparelProduct | null;
  /** Pieces of each variant in the cart, so a tap shows what is already picked. */
  inCart: Record<string, number>;
  onClose: () => void;
  onPick: (product: ApparelProduct, variant: ApparelVariant) => void;
}

/**
 * The warning shown when a piece is added beyond the recorded stock. The sale still goes through (the customer is holding the
 * garment); the count goes below zero, which tells the manager a delivery or count needs fixing.
 */
export function stockWarning(name: string, variant: Pick<ApparelVariant, 'size' | 'colour' | 'on_hand'>, alreadyInCart: number): string | null {
  if (variant.on_hand == null) return null; // count not shown to this user
  const after = variant.on_hand - (alreadyInCart + 1);
  if (after >= 0) return null;
  const onRecord = variant.on_hand <= 0 ? 'none on record' : `only ${variant.on_hand} on record`;
  return `${name}, ${variant.size} / ${variant.colour}: ${onRecord}. Stock will show ${after} after this sale. Check the delivery or count it.`;
}

/**
 * The message when one more piece would take the recorded stock past the limit (-5). The piece is not added: the stock has to
 * be received or counted in the admin app first. Null when it can be billed.
 */
export function stockLimitMessage(name: string, variant: Pick<ApparelVariant, 'size' | 'colour' | 'on_hand'>, alreadyInCart: number): string | null {
  if (canBill(variant.on_hand, alreadyInCart + 1)) return null;
  return `${name}, ${variant.size} / ${variant.colour} is locked: stock is at ${variant.on_hand} and may not go below ${NEGATIVE_STOCK_LIMIT}. `
    + 'Ask a manager to receive the delivery or count the stock in the admin app.';
}

/**
 * Sizes across, colours down. Each cell shows the pieces on the shelf and adds one when tapped. A cell with nothing left is
 * dimmed but still tappable (the count on record can be behind the shelf) until the stock reaches the limit: then it is locked.
 */
export const VariantPickerSheet: React.FC<VariantPickerSheetProps> = ({ product, inCart, onClose, onPick }) => {
  const [warning, setWarning] = useState<string | null>(null);
  const [gridWidth, setGridWidth] = useState(0);
  useEffect(() => setWarning(null), [product]);
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
  // Cells shrink to fit the sheet; only below MIN_CELL does the grid scroll sideways.
  const cell = gridWidth > 0 && sizes.length > 0
    ? Math.max(MIN_CELL, Math.min(MAX_CELL, Math.floor((gridWidth - COLOUR_HEAD - spacing.sm * sizes.length) / sizes.length)))
    : MAX_CELL;
  const cellSize = { width: cell, height: cell };

  return (
    <Modal visible={!!product} onClose={onClose} title={product?.name ?? ''} subtitle={first ? price(first) : undefined}>
      {/* Measured on a plain View: a horizontal ScrollView's onLayout does not fire on web. */}
      <View onLayout={(e) => setGridWidth(e.nativeEvent.layout.width)}>
      <ScrollView horizontal showsHorizontalScrollIndicator={false}>
        <View style={{ gap: spacing.sm }}>
          <View style={styles.row}>
            <View style={styles.colourHead} />
            {sizes.map((s) => (
              <Text key={s} style={[styles.sizeHead, { width: cell }]}>{s}</Text>
            ))}
          </View>
          {colours.map((colour) => (
            <View key={colour} style={styles.row}>
              <Text style={styles.colourHead} numberOfLines={2}>{colour}</Text>
              {sizes.map((size) => {
                const v = cells.get(`${colour}|${size}`);
                if (!v || !product) return <View key={size} style={cellSize} />;
                const left = v.on_hand ?? 0;
                const picked = inCart[v.id] ?? 0;
                const locked = !canBill(v.on_hand, picked + 1);
                return (
                  <Pressable
                    key={size}
                    onPress={() => {
                      const limit = stockLimitMessage(product.name, v, picked);
                      if (limit) {
                        setWarning(limit);
                        return;
                      }
                      setWarning(stockWarning(product.name, v, picked));
                      onPick(product, v);
                    }}
                    accessibilityRole="button"
                    accessibilityLabel={`${product.name}, ${size}, ${colour}, ${left} in stock${picked ? `, ${picked} in the sale` : ''}${locked ? ', locked' : ''}`}
                    style={({ pressed }) => [styles.cell, cellSize, left <= 0 ? styles.cellEmpty : null, picked ? styles.cellPicked : null,
                      locked ? styles.cellLocked : null, pressed && !locked ? styles.cellPicked : null]}
                  >
                    <Text style={[styles.left, left <= 0 ? { color: colors.danger } : null]}>{left}</Text>
                    <Text style={[styles.leftLabel, picked > left ? { color: colors.warning } : null, locked ? { color: colors.danger } : null]}>
                      {locked ? 'Locked' : picked ? `${picked} in sale` : left > 0 ? 'in stock' : 'on record'}
                    </Text>
                  </Pressable>
                );
              })}
            </View>
          ))}
        </View>
      </ScrollView>
      </View>
      {warning ? (
        <Text style={styles.warning} accessibilityRole="alert">{warning}</Text>
      ) : null}
      <Text style={styles.hint}>Tap a cell to add one piece. Tap again for another. Pieces not on record can still be sold, down to {NEGATIVE_STOCK_LIMIT}.</Text>
    </Modal>
  );
};

const MAX_CELL = 76;
const MIN_CELL = 56;
const COLOUR_HEAD = 84;

const styles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  colourHead: { width: COLOUR_HEAD, ...typography.caption, color: colors.textSecondary, fontFamily: fonts.semibold },
  sizeHead: { textAlign: 'center', ...typography.bodyBold, color: colors.text },
  cell: {
    borderRadius: radii.md, borderWidth: 1, borderColor: colors.surfaceBorder, backgroundColor: colors.surface,
    alignItems: 'center', justifyContent: 'center',
  },
  cellEmpty: { backgroundColor: colors.surfaceLight, opacity: 0.7 },
  cellPicked: { borderColor: colors.primary, backgroundColor: colors.primaryGlow },
  cellLocked: { borderColor: colors.danger, backgroundColor: colors.dangerBg, opacity: 0.6 },
  left: { ...typography.h3, color: colors.text, fontFamily: fonts.bold },
  leftLabel: { ...typography.label, color: colors.textMuted },
  hint: { ...typography.caption, color: colors.textMuted, marginTop: spacing.md },
  warning: {
    ...typography.caption, color: colors.warning, backgroundColor: colors.warningBg, padding: spacing.md, borderRadius: radii.md,
    marginTop: spacing.md,
  },
});
