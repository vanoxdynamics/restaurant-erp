import React, { useMemo, useState } from 'react';
import { Pressable, RefreshControl, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import {
  Badge,
  Button,
  colors,
  fonts,
  humanizeLabel,
  Input,
  Modal,
  radii,
  shadows,
  spacing,
  Switch,
  typography,
} from '@repo/shared-ui';
import { formatPaiseToRupees, KitchenStation } from '@repo/shared-utils';
import {
  ApiError,
  Category,
  ItemUnit,
  MenuItem,
  SUPPORTED_STATIONS,
  useCatalog,
  useCreateCategory,
  useCreateMenuItem,
  useEntitlements,
  useToggle86,
  useUpdateMenuItem,
} from '@repo/api-client';
import { QueryState } from '../components/QueryState';
import { ImportItemsSheet } from '../components/ImportItemsSheet';

const errorText = (e: unknown) => (e instanceof ApiError ? e.message : 'Something went wrong. Try again.');
const toPaise = (rupees: string) => Math.round((parseFloat(rupees) || 0) * 100);

interface ItemForm {
  categoryId: string;
  name: string;
  description: string;
  price: string;
  station: KitchenStation;
  gst: string;
  hsn: string;
  unit: ItemUnit;
  soldByWeight: boolean;
  barcode: string;
  sku: string;
}

const emptyForm = (categoryId = ''): ItemForm => ({
  categoryId,
  name: '',
  description: '',
  price: '',
  station: KitchenStation.MAIN_KITCHEN,
  gst: '5',
  hsn: '',
  unit: 'pcs',
  soldByWeight: false,
  barcode: '',
  sku: '',
});

const GST_RATES = ['0', '5', '12', '18', '28'];
const UNITS: { unit: ItemUnit; label: string }[] = [
  { unit: 'pcs', label: 'Piece' },
  { unit: 'kg', label: 'kg' },
  { unit: 'g', label: 'g' },
  { unit: 'l', label: 'Litre' },
  { unit: 'ml', label: 'ml' },
];

export const MenuBuilderScreen: React.FC = () => {
  const catalogQuery = useCatalog();
  const categories = catalogQuery.data ?? [];
  const toggle86 = useToggle86();
  const createItem = useCreateMenuItem();
  const updateItem = useUpdateMenuItem();
  const createCategory = useCreateCategory();
  const settings = useEntitlements().data;
  // Restaurants keep their menu editor; shops and cafés (the retail app) also get units, barcodes, HSN codes and CSV import.
  const retail = !!settings && settings.category !== 'restaurant';
  const shop = retail && !settings.settings.kitchen_tickets;
  const kitchen = settings?.settings.kitchen_tickets ?? true;
  const noun = shop ? 'Items' : 'Menu';
  const [importOpen, setImportOpen] = useState(false);

  const [search, setSearch] = useState('');
  const [station, setStation] = useState<KitchenStation | 'ALL'>('ALL');
  const [categoryId, setCategoryId] = useState<string | null>(null);

  // Item sheet: editing an existing item, or adding a new one (editing === null).
  const [itemSheetOpen, setItemSheetOpen] = useState(false);
  const [editing, setEditing] = useState<MenuItem | null>(null);
  const [form, setForm] = useState<ItemForm>(emptyForm());
  const [formError, setFormError] = useState<string | null>(null);

  const [categorySheetOpen, setCategorySheetOpen] = useState(false);
  const [categoryName, setCategoryName] = useState('');
  const [categoryError, setCategoryError] = useState<string | null>(null);

  const visible = useMemo(() => {
    const term = search.trim().toLowerCase();
    return categories
      .filter((c) => !categoryId || c.id === categoryId)
      .map((c) => ({
        ...c,
        items: c.items.filter(
          (item) =>
            (station === 'ALL' || item.station === station) &&
            (!term ||
              item.name.toLowerCase().includes(term) ||
              item.description?.toLowerCase().includes(term) ||
              item.barcode === search.trim() ||
              item.sku?.toLowerCase() === term),
        ),
      }))
      .filter((c) => c.items.length > 0);
  }, [categories, categoryId, search, station]);

  const openAdd = () => {
    setEditing(null);
    setForm(emptyForm(categoryId ?? categories[0]?.id ?? ''));
    setFormError(null);
    setItemSheetOpen(true);
  };

  const openEdit = (item: MenuItem) => {
    setEditing(item);
    setForm({
      categoryId: item.category_id,
      name: item.name,
      description: item.description ?? '',
      price: (item.base_price_paise / 100).toFixed(2),
      station: item.station,
      gst: String(item.tax_rate_percent),
      hsn: item.hsn_code ?? '',
      unit: item.unit,
      soldByWeight: item.sold_by_weight,
      barcode: item.barcode ?? '',
      sku: item.sku ?? '',
    });
    setFormError(null);
    setItemSheetOpen(true);
  };

  const saveItem = async () => {
    if (!form.name.trim()) return setFormError('Item name is required.');
    if (!form.categoryId) return setFormError('Select a category.');
    const pricePaise = toPaise(form.price);
    if (pricePaise <= 0) return setFormError('Enter a price above ₹0.');
    const gst = Number(form.gst);
    if (!GST_RATES.includes(form.gst) && !(gst >= 0 && gst <= 28)) return setFormError('GST rate must be between 0 and 28.');
    if (form.hsn && !/^\d{4,8}$/.test(form.hsn)) return setFormError('HSN code is 4 to 8 digits.');
    const shopFields = {
      tax_rate_percent: gst,
      hsn_code: form.hsn.trim(),
      unit: form.unit,
      sold_by_weight: form.unit !== 'pcs' && form.soldByWeight,
      barcode: form.barcode.trim(),
      sku: form.sku.trim(),
    };
    try {
      if (editing) {
        await updateItem.mutateAsync({
          itemId: editing.id,
          changes: {
            name: form.name.trim(),
            description: form.description.trim(),
            base_price_paise: pricePaise,
            station: form.station,
            category_id: form.categoryId,
            ...shopFields,
            hsn_code: shopFields.hsn_code || null,
            barcode: shopFields.barcode || null,
            sku: shopFields.sku || null,
          },
        });
      } else {
        await createItem.mutateAsync({
          category_id: form.categoryId,
          name: form.name.trim(),
          description: form.description.trim() || undefined,
          base_price_paise: pricePaise,
          station: form.station,
          ...shopFields,
        });
      }
      setItemSheetOpen(false);
    } catch (e) {
      setFormError(errorText(e));
    }
  };

  const removeItem = async () => {
    if (!editing) return;
    try {
      await updateItem.mutateAsync({ itemId: editing.id, changes: { is_active: false } });
      setItemSheetOpen(false);
    } catch (e) {
      setFormError(errorText(e));
    }
  };

  const saveCategory = async () => {
    if (!categoryName.trim()) return setCategoryError('Category name is required.');
    try {
      await createCategory.mutateAsync({ name: categoryName.trim(), sort_order: categories.length });
      setCategoryName('');
      setCategorySheetOpen(false);
    } catch (e) {
      setCategoryError(errorText(e));
    }
  };

  const saving = createItem.isPending || updateItem.isPending;

  return (
    <ScrollView
      style={styles.container}
      contentContainerStyle={styles.content}
      keyboardShouldPersistTaps="handled"
      refreshControl={
        <RefreshControl refreshing={catalogQuery.isRefetching} onRefresh={() => void catalogQuery.refetch()} tintColor={colors.primary} />
      }
    >
      <View style={styles.header}>
        <Text style={[typography.h1, { color: colors.text }]}>{noun}</Text>
        <View style={styles.headerActions}>
          {retail ? <Button title="Import CSV" size="sm" variant="secondary" onPress={() => setImportOpen(true)} /> : null}
          <Button
            title="Category"
            size="sm"
            variant="secondary"
            onPress={() => {
              setCategoryError(null);
              setCategorySheetOpen(true);
            }}
          />
          <Button title="Add item" size="sm" onPress={openAdd} disabled={categories.length === 0} />
        </View>
      </View>

      <Input placeholder={shop ? 'Search by name or barcode' : 'Search the menu'} value={search} onChangeText={setSearch} />

      {kitchen ? (
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chips}>
          {(['ALL', ...SUPPORTED_STATIONS] as const).map((s) => (
            <Chip key={s} label={s === 'ALL' ? 'All stations' : humanizeLabel(s)} selected={station === s} onPress={() => setStation(s)} />
          ))}
        </ScrollView>
      ) : null}

      {categories.length > 1 ? (
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chips}>
          <Chip label="All" selected={categoryId === null} onPress={() => setCategoryId(null)} />
          {categories.map((c) => (
            <Chip key={c.id} label={c.name} selected={categoryId === c.id} onPress={() => setCategoryId(c.id)} />
          ))}
        </ScrollView>
      ) : null}

      <QueryState
        query={catalogQuery}
        isEmpty={visible.length === 0}
        emptyText={categories.length === 0 ? `No ${noun.toLowerCase()} yet. Add a category or import a CSV to start.` : 'No items match.'}
      >
        {visible.map((category: Category) => (
          <View key={category.id} style={styles.section}>
            <View style={styles.sectionHeader}>
              <Text style={[typography.h3, { color: colors.text }]}>{category.name}</Text>
              <Text style={styles.count}>
                {category.items.length} {category.items.length === 1 ? 'item' : 'items'}
              </Text>
            </View>

            {category.items.map((item) => {
              const soldOut = !item.is_available_86;
              return (
                <Pressable
                  key={item.id}
                  onPress={() => openEdit(item)}
                  accessibilityRole="button"
                  accessibilityLabel={`Edit ${item.name}`}
                  style={({ pressed }) => [styles.itemCard, soldOut ? styles.itemSoldOut : null, pressed ? { opacity: 0.85 } : null]}
                >
                  <View style={{ flex: 1 }}>
                    <View style={styles.itemTitleRow}>
                      <Text style={[styles.itemName, soldOut ? styles.itemNameSoldOut : null]} numberOfLines={1}>
                        {item.name}
                      </Text>
                      {soldOut ? <Badge label="Sold out" variant="danger" size="sm" /> : null}
                    </View>
                    {item.description ? (
                      <Text style={styles.itemDescription} numberOfLines={2}>
                        {item.description}
                      </Text>
                    ) : null}
                    <Text style={styles.itemMeta}>
                      {formatPaiseToRupees(item.base_price_paise)}
                      {item.unit !== 'pcs' ? `/${item.unit}` : ''} · {kitchen ? humanizeLabel(item.station) : `GST ${item.tax_rate_percent}%`}
                      {item.barcode ? ` · ${item.barcode}` : ''}
                      {item.variants.length > 0
                        ? ` · ${item.variants.map((v) => `${v.name} ${formatPaiseToRupees(v.price_paise, { decimals: 0 })}`).join(', ')}`
                        : ''}
                    </Text>
                  </View>
                  <Switch
                    value={!soldOut}
                    onValueChange={(available) => toggle86.mutate({ itemId: item.id, isAvailable: available })}
                    accessibilityLabel={`${item.name} available`}
                  />
                </Pressable>
              );
            })}
          </View>
        ))}
      </QueryState>

      {/* Add / edit item */}
      <Modal
        visible={itemSheetOpen}
        onClose={() => setItemSheetOpen(false)}
        title={editing ? 'Edit item' : 'Add item'}
        footer={
          <>
            {editing ? (
              <Button title="Remove" variant="ghost" onPress={removeItem} textStyle={{ color: colors.danger }} />
            ) : null}
            <View style={{ flex: 1 }} />
            <Button title="Cancel" variant="secondary" onPress={() => setItemSheetOpen(false)} />
            <Button title="Save" onPress={saveItem} loading={saving} />
          </>
        }
      >
        <Text style={styles.fieldLabel}>Category</Text>
        <View style={styles.chipWrap}>
          {categories.map((c) => (
            <Chip key={c.id} label={c.name} selected={form.categoryId === c.id} onPress={() => setForm({ ...form, categoryId: c.id })} />
          ))}
        </View>

        <Input label="Item name" value={form.name} onChangeText={(name) => setForm({ ...form, name })} />
        <Input
          label="Description"
          value={form.description}
          onChangeText={(description) => setForm({ ...form, description })}
          multiline
        />
        <Input
          label="Price"
          prefix="₹"
          keyboardType="decimal-pad"
          value={form.price}
          onChangeText={(price) => setForm({ ...form, price: price.replace(/[^0-9.]/g, '') })}
        />

        <Text style={styles.fieldLabel}>GST rate</Text>
        <View style={styles.chipWrap}>
          {GST_RATES.map((r) => (
            <Chip key={r} label={`${r}%`} selected={Number(form.gst) === Number(r)} onPress={() => setForm({ ...form, gst: r })} />
          ))}
        </View>

        {retail ? (
          <>
            <Text style={styles.fieldLabel}>Sold per</Text>
            <View style={styles.chipWrap}>
              {UNITS.map((u) => (
                <Chip
                  key={u.unit}
                  label={u.label}
                  selected={form.unit === u.unit}
                  onPress={() => setForm({ ...form, unit: u.unit, soldByWeight: u.unit === 'pcs' ? false : form.unit === 'pcs' ? true : form.soldByWeight })}
                />
              ))}
            </View>
            {form.unit !== 'pcs' ? (
              <View style={styles.switchRow}>
                <Text style={[styles.fieldLabel, { flex: 1, marginBottom: 0 }]}>Loose: weighed or measured at the counter</Text>
                <Switch value={form.soldByWeight} onValueChange={(soldByWeight) => setForm({ ...form, soldByWeight })} accessibilityLabel="Sold by weight" />
              </View>
            ) : null}

            <View style={styles.fieldRow}>
              <Input label="Barcode" value={form.barcode} onChangeText={(barcode) => setForm({ ...form, barcode: barcode.replace(/\s/g, '') })} autoCapitalize="none" containerStyle={{ flex: 1 }} />
              <Input label="SKU" value={form.sku} onChangeText={(sku) => setForm({ ...form, sku: sku.replace(/\s/g, '') })} autoCapitalize="characters" containerStyle={{ flex: 1 }} />
            </View>
            <Input label="HSN code" value={form.hsn} onChangeText={(hsn) => setForm({ ...form, hsn: hsn.replace(/\D/g, '').slice(0, 8) })} keyboardType="number-pad" helper="Printed on the bill. 4 to 8 digits." />
          </>
        ) : null}

        {kitchen ? (
          <>
            <Text style={styles.fieldLabel}>Kitchen station</Text>
            <View style={styles.chipWrap}>
              {SUPPORTED_STATIONS.map((s) => (
                <Chip key={s} label={humanizeLabel(s)} selected={form.station === s} onPress={() => setForm({ ...form, station: s })} />
              ))}
            </View>
          </>
        ) : null}

        {formError ? <Text style={styles.error}>{formError}</Text> : null}
      </Modal>

      {retail ? <ImportItemsSheet visible={importOpen} onClose={() => setImportOpen(false)} /> : null}

      {/* Add category */}
      <Modal
        visible={categorySheetOpen}
        onClose={() => setCategorySheetOpen(false)}
        title="Add category"
        footer={
          <>
            <Button title="Cancel" variant="secondary" onPress={() => setCategorySheetOpen(false)} />
            <Button title="Save" onPress={saveCategory} loading={createCategory.isPending} />
          </>
        }
      >
        <Input label="Category name" value={categoryName} onChangeText={setCategoryName} error={categoryError ?? undefined} />
      </Modal>
    </ScrollView>
  );
};

const Chip: React.FC<{ label: string; selected: boolean; onPress: () => void }> = ({ label, selected, onPress }) => (
  <TouchableOpacity
    onPress={onPress}
    style={[styles.chip, selected ? styles.chipActive : null]}
    accessibilityRole="radio"
    accessibilityState={{ selected }}
  >
    <Text style={[styles.chipText, selected ? styles.chipTextActive : null]}>{label}</Text>
  </TouchableOpacity>
);

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
  header: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    justifyContent: 'space-between',
    alignItems: 'center',
    gap: spacing.md,
    marginBottom: spacing.lg,
  },
  headerActions: {
    flexDirection: 'row',
    gap: spacing.sm,
  },
  chips: {
    gap: spacing.sm,
    paddingBottom: spacing.md,
  },
  chipWrap: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.sm,
    marginBottom: spacing.lg,
  },
  chip: {
    minHeight: 36,
    paddingHorizontal: spacing.md,
    borderRadius: radii.full,
    borderWidth: 1,
    borderColor: colors.surfaceBorder,
    backgroundColor: colors.surface,
    justifyContent: 'center',
  },
  chipActive: {
    backgroundColor: colors.primaryGlow,
    borderColor: colors.primary,
  },
  chipText: {
    ...typography.caption,
    color: colors.textSecondary,
  },
  chipTextActive: {
    color: colors.primary,
    fontFamily: fonts.semibold,
  },
  section: {
    marginTop: spacing.lg,
    gap: spacing.sm,
  },
  sectionHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'baseline',
    marginBottom: spacing.xs,
  },
  count: {
    ...typography.caption,
    color: colors.textMuted,
  },
  itemCard: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    backgroundColor: colors.surface,
    borderRadius: radii.lg,
    borderWidth: 1,
    borderColor: colors.surfaceBorder,
    padding: spacing.lg,
    ...shadows.card,
  },
  itemSoldOut: {
    backgroundColor: colors.surfaceLight,
  },
  itemTitleRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
  },
  itemName: {
    ...typography.bodyBold,
    color: colors.text,
    flexShrink: 1,
  },
  itemNameSoldOut: {
    color: colors.textMuted,
  },
  itemDescription: {
    ...typography.caption,
    color: colors.textSecondary,
    marginTop: 2,
  },
  itemMeta: {
    ...typography.caption,
    color: colors.text,
    fontFamily: fonts.semibold,
    marginTop: spacing.xs,
  },
  fieldLabel: {
    ...typography.caption,
    color: colors.text,
    fontFamily: fonts.semibold,
    marginBottom: 6,
  },
  error: {
    ...typography.caption,
    color: colors.danger,
  },
  switchRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    marginBottom: spacing.lg,
  },
  fieldRow: {
    flexDirection: 'row',
    gap: spacing.md,
  },
});
