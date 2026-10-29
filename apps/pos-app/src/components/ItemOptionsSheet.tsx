import React, { useEffect, useMemo, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { Button, colors, fonts, Input, Modal, radii, spacing, typography } from '@repo/shared-ui';
import { formatPaiseToRupees } from '@repo/shared-utils';
import { CartLine, MenuItem } from '@repo/api-client';
import { Icon } from './Icon';

export interface ItemOptionsSheetProps {
  item: MenuItem | null;
  onClose: () => void;
  onAdd: (line: Omit<CartLine, 'key'>) => void;
}

/** Portion, modifiers, quantity and a note for one menu item before it goes in the cart. */
export const ItemOptionsSheet: React.FC<ItemOptionsSheetProps> = ({ item, onClose, onAdd }) => {
  const variants = (item?.variants ?? []).filter((v) => v.is_available_86);
  const groups = item?.modifier_groups ?? [];
  const [variantId, setVariantId] = useState<string | undefined>();
  const [selected, setSelected] = useState<Record<string, string[]>>({});
  const [quantity, setQuantity] = useState(1);
  const [notes, setNotes] = useState('');

  useEffect(() => {
    if (!item) return;
    setVariantId(item.variants.length === 1 ? item.variants[0].id : undefined);
    setSelected({});
    setQuantity(1);
    setNotes('');
  }, [item]);

  const variant = variants.find((v) => v.id === variantId);
  const chosenModifiers = groups.flatMap((g) => g.modifiers.filter((m) => (selected[g.id] ?? []).includes(m.id)));
  const unitPrice = (variant?.price_paise ?? item?.base_price_paise ?? 0) + chosenModifiers.reduce((n, m) => n + m.price_paise, 0);

  const missing = useMemo(() => {
    if (item && item.variants.length > 0 && !variant) return 'Choose a portion.';
    for (const g of groups) {
      const count = (selected[g.id] ?? []).length;
      if (count < g.min_selection) return `Choose ${g.min_selection === 1 ? 'an option' : `${g.min_selection} options`} for ${g.name}.`;
    }
    return null;
  }, [item, variant, groups, selected]);

  const toggleModifier = (groupId: string, modifierId: string, max: number) => {
    setSelected((prev) => {
      const current = prev[groupId] ?? [];
      if (current.includes(modifierId)) return { ...prev, [groupId]: current.filter((id) => id !== modifierId) };
      if (max === 1) return { ...prev, [groupId]: [modifierId] };
      if (current.length >= max) return prev;
      return { ...prev, [groupId]: [...current, modifierId] };
    });
  };

  const add = () => {
    if (!item || missing) return;
    onAdd({
      item_id: item.id,
      name: item.name,
      variant_id: variant?.id,
      variant_name: variant?.name,
      modifier_ids: chosenModifiers.map((m) => m.id),
      modifier_names: chosenModifiers.map((m) => m.name),
      unit_price_paise: unitPrice,
      quantity,
      notes: notes.trim() || undefined,
    });
    onClose();
  };

  return (
    <Modal
      visible={item !== null}
      onClose={onClose}
      title={item?.name ?? ''}
      footer={
        <Button
          title={`Add · ${formatPaiseToRupees(unitPrice * quantity, { decimals: 0 })}`}
          onPress={add}
          disabled={!!missing}
          style={{ flex: 1 }}
        />
      }
    >
      {item && item.variants.length > 0 ? (
        <View style={styles.group}>
          <Text style={styles.groupTitle}>Portion</Text>
          <View style={styles.options}>
            {item.variants.map((v) => (
              <Option
                key={v.id}
                label={v.name}
                price={formatPaiseToRupees(v.price_paise, { decimals: 0 })}
                selected={variantId === v.id}
                disabled={!v.is_available_86}
                onPress={() => setVariantId(v.id)}
              />
            ))}
          </View>
        </View>
      ) : null}

      {groups.map((g) => (
        <View key={g.id} style={styles.group}>
          <Text style={styles.groupTitle}>
            {g.name}
            <Text style={styles.groupRule}>
              {g.min_selection > 0 ? '  Required' : '  Optional'}
              {g.max_selection > 1 ? ` · up to ${g.max_selection}` : ''}
            </Text>
          </Text>
          <View style={styles.options}>
            {g.modifiers.map((m) => (
              <Option
                key={m.id}
                label={m.name}
                price={m.price_paise ? `+${formatPaiseToRupees(m.price_paise, { decimals: 0 })}` : undefined}
                selected={(selected[g.id] ?? []).includes(m.id)}
                disabled={!m.is_available}
                onPress={() => toggleModifier(g.id, m.id, g.max_selection)}
              />
            ))}
          </View>
        </View>
      ))}

      <View style={styles.group}>
        <Text style={styles.groupTitle}>Quantity</Text>
        <View style={styles.stepper}>
          <StepButton icon="minus" label="Decrease quantity" onPress={() => setQuantity((q) => Math.max(1, q - 1))} />
          <Text style={styles.qty}>{quantity}</Text>
          <StepButton icon="plus" label="Increase quantity" onPress={() => setQuantity((q) => Math.min(99, q + 1))} />
        </View>
      </View>

      <Input label="Note for kitchen" placeholder="e.g. less spicy" value={notes} onChangeText={setNotes} maxLength={200} />
      {missing ? <Text style={styles.hint}>{missing}</Text> : null}
    </Modal>
  );
};

const Option: React.FC<{ label: string; price?: string; selected: boolean; disabled?: boolean; onPress: () => void }> = ({
  label,
  price,
  selected,
  disabled,
  onPress,
}) => (
  <Pressable
    onPress={onPress}
    disabled={disabled}
    accessibilityRole="radio"
    accessibilityLabel={label}
    accessibilityState={{ selected, disabled }}
    style={[styles.option, selected ? styles.optionSelected : null, disabled ? { opacity: 0.4 } : null]}
  >
    <Text style={[styles.optionLabel, selected ? { color: colors.primary } : null]}>{label}</Text>
    {price ? <Text style={styles.optionPrice}>{disabled ? 'Sold out' : price}</Text> : null}
  </Pressable>
);

export const StepButton: React.FC<{ icon: 'plus' | 'minus'; label: string; onPress: () => void; size?: number }> = ({
  icon,
  label,
  onPress,
  size = 44,
}) => (
  <Pressable
    onPress={onPress}
    accessibilityRole="button"
    accessibilityLabel={label}
    hitSlop={4}
    style={({ pressed }) => [styles.step, { width: size, height: size }, pressed ? { backgroundColor: colors.surfaceLight } : null]}
  >
    <Icon name={icon} size={18} color={colors.text} />
  </Pressable>
);

const styles = StyleSheet.create({
  group: {
    marginBottom: spacing.lg,
  },
  groupTitle: {
    ...typography.bodyBold,
    color: colors.text,
    marginBottom: spacing.sm,
  },
  groupRule: {
    ...typography.caption,
    color: colors.textMuted,
  },
  options: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.sm,
  },
  option: {
    minHeight: 48,
    minWidth: 96,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderRadius: radii.md,
    borderWidth: 1.5,
    borderColor: colors.surfaceBorder,
    backgroundColor: colors.surface,
    justifyContent: 'center',
  },
  optionSelected: {
    borderColor: colors.primary,
    backgroundColor: colors.primaryGlow,
  },
  optionLabel: {
    ...typography.bodyBold,
    color: colors.text,
  },
  optionPrice: {
    ...typography.caption,
    color: colors.textSecondary,
  },
  stepper: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.lg,
  },
  step: {
    borderRadius: radii.md,
    borderWidth: 1,
    borderColor: colors.surfaceBorder,
    backgroundColor: colors.surface,
    alignItems: 'center',
    justifyContent: 'center',
  },
  qty: {
    ...typography.h2,
    minWidth: 32,
    textAlign: 'center',
    color: colors.text,
    fontFamily: fonts.bold,
  },
  hint: {
    ...typography.caption,
    color: colors.textSecondary,
  },
});
