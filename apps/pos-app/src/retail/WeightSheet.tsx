import React, { useEffect, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { Button, colors, fonts, Modal, radii, spacing, typography } from '@repo/shared-ui';
import { formatPaiseToRupees } from '@repo/shared-utils';
import { ItemUnit, MenuItem } from '@repo/api-client';
import { formatQuantity, roundQuantity } from '../business';

export interface WeightSheetProps {
  item: MenuItem | null;
  onClose: () => void;
  /** The quantity in the item's own unit (kg for an item priced per kg), 3 decimals at most. */
  onAdd: (quantity: number) => void;
}

/** Smaller units a loose item can be weighed in: priced per kg, weighed in g; priced per litre, measured in ml. */
const ENTRY_UNITS: Record<ItemUnit, { unit: ItemUnit; factor: number }[]> = {
  kg: [{ unit: 'kg', factor: 1 }, { unit: 'g', factor: 0.001 }],
  l: [{ unit: 'l', factor: 1 }, { unit: 'ml', factor: 0.001 }],
  g: [{ unit: 'g', factor: 1 }],
  ml: [{ unit: 'ml', factor: 1 }],
  pcs: [{ unit: 'pcs', factor: 1 }],
};

const KEYS = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '.', '0', '⌫'];

/** Number pad for a loose item: weight (or volume) in the item's unit or the smaller one, with the price as you type. */
export const WeightSheet: React.FC<WeightSheetProps> = ({ item, onClose, onAdd }) => {
  const units = ENTRY_UNITS[item?.unit ?? 'kg'];
  const [entry, setEntry] = useState('');
  const [entryUnit, setEntryUnit] = useState(units[0]);

  useEffect(() => {
    setEntry('');
    setEntryUnit(ENTRY_UNITS[item?.unit ?? 'kg'][0]);
  }, [item]);

  const quantity = roundQuantity((Number(entry) || 0) * entryUnit.factor);
  const price = Math.round((item?.base_price_paise ?? 0) * quantity);
  const valid = quantity > 0 && quantity <= 9999;

  const press = (key: string) => {
    if (key === '⌫') return setEntry((e) => e.slice(0, -1));
    setEntry((e) => {
      if (key === '.' && (e.includes('.') || entryUnit.factor !== 1)) return e; // grams and ml are whole numbers
      const next = e === '0' && key !== '.' ? key : e + key;
      const [, decimals = ''] = next.split('.');
      return decimals.length > 3 || next.length > 8 ? e : next;
    });
  };

  return (
    <Modal
      visible={!!item}
      onClose={onClose}
      title={item?.name ?? ''}
      subtitle={item ? `${formatPaiseToRupees(item.base_price_paise)} per ${item.unit}` : undefined}
      footer={
        <Button
          title={valid ? `Add ${formatQuantity(quantity, item?.unit)} · ${formatPaiseToRupees(price)}` : 'Enter the weight'}
          onPress={() => onAdd(quantity)}
          disabled={!valid}
        />
      }
    >
      <View style={styles.display} accessibilityLiveRegion="polite">
        <Text style={styles.value}>{entry || '0'}</Text>
        <Text style={styles.unit}>{entryUnit.unit}</Text>
      </View>
      {units.length > 1 ? (
        <View style={styles.units}>
          {units.map((u) => (
            <Pressable
              key={u.unit}
              onPress={() => {
                setEntryUnit(u);
                setEntry('');
              }}
              accessibilityRole="button"
              accessibilityState={{ selected: u.unit === entryUnit.unit }}
              style={[styles.unitBtn, u.unit === entryUnit.unit ? styles.unitBtnActive : null]}
            >
              <Text style={[styles.unitText, u.unit === entryUnit.unit ? { color: colors.textInverse } : null]}>{u.unit}</Text>
            </Pressable>
          ))}
        </View>
      ) : null}
      <View style={styles.pad}>
        {KEYS.map((key) => (
          <Pressable
            key={key}
            onPress={() => press(key)}
            accessibilityRole="button"
            accessibilityLabel={key === '⌫' ? 'Delete' : key === '.' ? 'Decimal point' : key}
            style={({ pressed }) => [styles.key, pressed ? { backgroundColor: colors.surfaceBorder } : null]}
          >
            <Text style={styles.keyText}>{key}</Text>
          </Pressable>
        ))}
      </View>
    </Modal>
  );
};

const styles = StyleSheet.create({
  display: { flexDirection: 'row', alignItems: 'baseline', justifyContent: 'center', gap: spacing.sm, paddingVertical: spacing.md },
  value: { ...typography.h1, fontFamily: fonts.bold, color: colors.text },
  unit: { ...typography.h3, color: colors.textSecondary },
  units: { flexDirection: 'row', justifyContent: 'center', gap: spacing.sm, marginBottom: spacing.md },
  unitBtn: {
    minWidth: 64,
    minHeight: 40,
    borderRadius: radii.full,
    borderWidth: 1,
    borderColor: colors.surfaceBorder,
    alignItems: 'center',
    justifyContent: 'center',
  },
  unitBtnActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  unitText: { ...typography.bodyBold, color: colors.text },
  pad: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, justifyContent: 'center' },
  key: {
    width: '30%',
    minHeight: 56,
    borderRadius: radii.md,
    backgroundColor: colors.surfaceLight,
    alignItems: 'center',
    justifyContent: 'center',
  },
  keyText: { ...typography.h3, fontFamily: fonts.semibold, color: colors.text },
});
