import React, { useEffect, useMemo, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { Badge, Button, colors, fonts, Input, Modal, radii, spacing, Switch, typography } from '@repo/shared-ui';
import { formatPaiseToRupees } from '@repo/shared-utils';
import {
  ApiError,
  ApparelProduct,
  ApparelTaxMode,
  ApparelVariant,
  useAddApparelVariants,
  useApparelCategories,
  useApparelProducts,
  useCreateApparelCategory,
  useCreateApparelProduct,
  useUpdateApparelProduct,
  useUpdateApparelVariant,
} from '@repo/api-client';
import { QueryState } from '../../components/QueryState';
import { ImportSheet } from './ImportSheet';

const errorText = (e: unknown) => (e instanceof ApiError ? e.message : 'Something went wrong. Try again.');
const toPaise = (rupees: string) => Math.round((parseFloat(rupees) || 0) * 100);
const rupees = (paise?: number) => (paise === undefined ? '' : (paise / 100).toFixed(2));
const money = (paise: number) => formatPaiseToRupees(paise);
/** "S, M, L" or "S M L" -> ["S", "M", "L"], keeping the order typed and dropping repeats. */
const splitList = (text: string) => Array.from(new Set(text.split(/[,\n]+|\s{2,}/).map((s) => s.trim()).filter(Boolean)));

const Chip: React.FC<{ label: string; selected: boolean; onPress: () => void }> = ({ label, selected, onPress }) => (
  <TouchableOpacity onPress={onPress} style={[styles.chip, selected ? styles.chipActive : null]} accessibilityRole="radio"
    accessibilityState={{ selected }}>
    <Text style={[styles.chipText, selected ? styles.chipTextActive : null]}>{label}</Text>
  </TouchableOpacity>
);

/**
 * Dress shop products (styles). Each style has its sizes and colours (the variants); every variant has its own price, MRP and
 * barcode. Variants saved without a barcode get an in-store one.
 */
export const ProductsScreen: React.FC = () => {
  const [search, setSearch] = useState('');
  const [categoryId, setCategoryId] = useState<string | undefined>();
  const [showInactive, setShowInactive] = useState(false);
  const [editing, setEditing] = useState<ApparelProduct | 'new' | null>(null);
  const [importing, setImporting] = useState(false);
  const categories = useApparelCategories();
  const products = useApparelProducts({ search: search.trim() || undefined, category_id: categoryId, include_inactive: showInactive });
  const list = products.data?.items ?? [];

  return (
    <View style={styles.container}>
      <View style={styles.toolbar}>
        <View style={{ flex: 1 }}>
          <Input value={search} onChangeText={setSearch} placeholder="Search name, brand, style code or barcode" />
        </View>
        <Button title="Import" variant="secondary" onPress={() => setImporting(true)} />
        <Button title="New product" onPress={() => setEditing('new')} />
      </View>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.chipBar} contentContainerStyle={styles.chips}>
        <Chip label="All" selected={!categoryId} onPress={() => setCategoryId(undefined)} />
        {(categories.data ?? []).map((c) => (
          <Chip key={c.id} label={c.name} selected={categoryId === c.id} onPress={() => setCategoryId(c.id)} />
        ))}
        <View style={styles.inactiveToggle}>
          <Text style={styles.hint}>Show switched off</Text>
          <Switch value={showInactive} onValueChange={setShowInactive} />
        </View>
      </ScrollView>
      <QueryState query={products} isEmpty={list.length === 0} emptyText="No products yet. Add one or import a spreadsheet.">
        <ScrollView contentContainerStyle={styles.list}>
          <Text style={styles.hint}>{products.data?.total ?? 0} products</Text>
          {list.map((p) => (
            <Pressable key={p.id} onPress={() => setEditing(p)} style={styles.card} accessibilityRole="button">
              <View style={styles.cardHead}>
                <Text style={styles.cardTitle}>{p.name}</Text>
                {!p.is_active ? <Badge label="Off" variant="neutral" /> : null}
              </View>
              <Text style={styles.hint}>
                {[p.brand, p.style_code, p.category_name, p.tax_mode === 'fixed' ? `GST ${p.tax_rate_percent}%` : 'GST by price'].filter(Boolean).join(' · ')}
              </Text>
              <Text style={styles.variants}>
                {sizesOf(p.variants).join(' · ')} — {coloursOf(p.variants).join(', ')} — {priceRange(p.variants)}
              </Text>
            </Pressable>
          ))}
        </ScrollView>
      </QueryState>
      {editing ? <ProductEditor product={editing === 'new' ? null : editing} onClose={() => setEditing(null)} /> : null}
      <ImportSheet visible={importing} onClose={() => setImporting(false)} />
    </View>
  );
};

const sizesOf = (variants: ApparelVariant[]) => Array.from(new Set(variants.map((v) => v.size)));
const coloursOf = (variants: ApparelVariant[]) => Array.from(new Set(variants.map((v) => v.colour)));
function priceRange(variants: ApparelVariant[]) {
  const prices = variants.map((v) => v.price_paise);
  if (!prices.length) return '';
  const lo = Math.min(...prices);
  const hi = Math.max(...prices);
  return lo === hi ? money(lo) : `${money(lo)} – ${money(hi)}`;
}

const ProductEditor: React.FC<{ product: ApparelProduct | null; onClose: () => void }> = ({ product, onClose }) => {
  const categories = useApparelCategories();
  const createCategory = useCreateApparelCategory();
  const create = useCreateApparelProduct();
  const update = useUpdateApparelProduct();
  const addVariants = useAddApparelVariants();
  const [current, setCurrent] = useState<ApparelProduct | null>(product);
  const [form, setForm] = useState({
    name: product?.name ?? '', brand: product?.brand ?? '', categoryId: product?.category_id ?? '', styleCode: product?.style_code ?? '',
    hsn: product?.hsn_code ?? '', taxMode: (product?.tax_mode ?? 'slab') as ApparelTaxMode,
    taxRate: product?.tax_rate_percent !== undefined ? String(product.tax_rate_percent) : '',
    sizes: '', colours: '', price: '', mrp: '', newCategory: '',
  });
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!form.categoryId && categories.data?.length) setForm((f) => ({ ...f, categoryId: categories.data![0].id }));
  }, [categories.data, form.categoryId]);

  const sizes = useMemo(() => splitList(form.sizes), [form.sizes]);
  const colours = useMemo(() => splitList(form.colours), [form.colours]);
  const gridCount = sizes.length * Math.max(colours.length, 1);
  const busy = create.isPending || update.isPending || addVariants.isPending;

  const details = {
    category_id: form.categoryId, name: form.name.trim(), brand: form.brand, style_code: form.styleCode, hsn_code: form.hsn,
    tax_mode: form.taxMode, tax_rate_percent: form.taxMode === 'fixed' ? Number(form.taxRate) : undefined,
  };
  const grid = { sizes, colours, price_paise: toPaise(form.price), mrp_paise: form.mrp.trim() ? toPaise(form.mrp) : undefined };

  const save = async () => {
    setError(null);
    try {
      if (!current) {
        setCurrent(await create.mutateAsync({ ...details, ...grid }));
      } else {
        let saved = await update.mutateAsync({ id: current.id, changes: { ...details, tax_rate_percent: form.taxMode === 'fixed' ? Number(form.taxRate) : null } });
        if (sizes.length) saved = await addVariants.mutateAsync({ productId: current.id, grid });
        setCurrent(saved);
      }
      setForm((f) => ({ ...f, sizes: '', colours: '', price: '', mrp: '' }));
    } catch (e) {
      setError(errorText(e));
    }
  };

  const addCategory = async () => {
    try {
      const made = await createCategory.mutateAsync(form.newCategory.trim());
      setForm((f) => ({ ...f, categoryId: made.id, newCategory: '' }));
    } catch (e) {
      setError(errorText(e));
    }
  };

  const toggleActive = async () => {
    if (!current) return;
    try {
      setCurrent(await update.mutateAsync({ id: current.id, changes: { is_active: !current.is_active } }));
    } catch (e) {
      setError(errorText(e));
    }
  };

  const canSave = !!form.name.trim() && !!form.categoryId && (form.taxMode === 'slab' || form.taxRate.trim() !== '')
    && (current ? !sizes.length || toPaise(form.price) > 0 : sizes.length > 0 && toPaise(form.price) > 0);

  return (
    <Modal visible onClose={onClose} title={current ? current.name : 'New product'}
      subtitle={current ? `${current.variants.length} sizes and colours` : 'A style, with its sizes and colours'}
      footer={
        <>
          <Button title="Close" variant="secondary" onPress={onClose} />
          {current ? <Button title={current.is_active ? 'Switch off' : 'Switch on'} variant="ghost" onPress={toggleActive} /> : null}
          <View style={{ flex: 1 }} />
          <Button title={current ? (sizes.length ? `Save and add ${gridCount}` : 'Save') : `Create ${gridCount || ''} variants`}
            onPress={save} disabled={!canSave} loading={busy} />
        </>
      }
    >
      <ScrollView style={{ maxHeight: 560 }} contentContainerStyle={{ gap: spacing.md }}>
        <Input label="Name" value={form.name} onChangeText={(name) => setForm({ ...form, name })} placeholder="Men's Oxford Shirt" />
        <View style={styles.pair}>
          <View style={styles.field}><Input style={styles.fill} label="Brand" value={form.brand} onChangeText={(brand) => setForm({ ...form, brand })} /></View>
          <View style={styles.field}><Input style={styles.fill} label="Style code" value={form.styleCode} onChangeText={(styleCode) => setForm({ ...form, styleCode })} autoCapitalize="characters" /></View>
          <View style={styles.field}><Input style={styles.fill} label="HSN" value={form.hsn} onChangeText={(hsn) => setForm({ ...form, hsn: hsn.replace(/\D/g, '').slice(0, 8) })} keyboardType="number-pad" /></View>
        </View>
        <Text style={styles.label}>Category</Text>
        <View style={styles.wrap}>
          {(categories.data ?? []).map((c) => (
            <Chip key={c.id} label={c.name} selected={form.categoryId === c.id} onPress={() => setForm({ ...form, categoryId: c.id })} />
          ))}
        </View>
        <View style={styles.pair}>
          <View style={{ flex: 1 }}><Input style={styles.fill} value={form.newCategory} onChangeText={(newCategory) => setForm({ ...form, newCategory })} placeholder="New category" /></View>
          <Button title="Add" variant="secondary" size="sm" onPress={addCategory} disabled={!form.newCategory.trim()} />
        </View>
        <Text style={styles.label}>GST</Text>
        <View style={styles.wrap}>
          <Chip label="By price (slab)" selected={form.taxMode === 'slab'} onPress={() => setForm({ ...form, taxMode: 'slab' })} />
          <Chip label="Fixed rate" selected={form.taxMode === 'fixed'} onPress={() => setForm({ ...form, taxMode: 'fixed' })} />
          {form.taxMode === 'fixed' ? (
            <View style={{ width: 110 }}><Input style={styles.fill} value={form.taxRate} onChangeText={(taxRate) => setForm({ ...form, taxRate })} placeholder="Rate %" keyboardType="decimal-pad" /></View>
          ) : null}
        </View>

        <Text style={styles.label}>{current ? 'Add sizes or colours' : 'Sizes and colours'}</Text>
        <Input label="Sizes (in order, comma separated)" value={form.sizes} onChangeText={(v) => setForm({ ...form, sizes: v })} placeholder="S, M, L, XL" />
        <Input label="Colours (blank: one colour)" value={form.colours} onChangeText={(v) => setForm({ ...form, colours: v })} placeholder="Blue, White, Black" />
        <View style={styles.pair}>
          <View style={styles.field}><Input style={styles.fill} label="Selling price (₹)" value={form.price} onChangeText={(price) => setForm({ ...form, price })} keyboardType="decimal-pad" /></View>
          <View style={styles.field}><Input style={styles.fill} label="MRP (₹)" value={form.mrp} onChangeText={(mrp) => setForm({ ...form, mrp })} keyboardType="decimal-pad" /></View>
        </View>
        {sizes.length ? <Text style={styles.hint}>{gridCount} variants: {sizes.join(', ')} in {colours.length ? colours.join(', ') : 'one colour'}</Text> : null}
        {error ? <Text style={styles.error}>{error}</Text> : null}
        {current ? <VariantTable product={current} onChanged={setCurrent} onError={setError} /> : null}
      </ScrollView>
    </Modal>
  );
};

const VariantTable: React.FC<{ product: ApparelProduct; onChanged: (p: ApparelProduct) => void; onError: (m: string) => void }> = ({ product, onChanged, onError }) => {
  const update = useUpdateApparelVariant();
  const [edit, setEdit] = useState<{ id: string; price: string; mrp: string; barcode: string } | null>(null);
  const save = async () => {
    if (!edit) return;
    try {
      onChanged(await update.mutateAsync({ id: edit.id, changes: {
        price_paise: toPaise(edit.price), mrp_paise: edit.mrp.trim() ? toPaise(edit.mrp) : null, barcode: edit.barcode,
      } }));
      setEdit(null);
    } catch (e) {
      onError(errorText(e));
    }
  };
  const toggle = async (v: ApparelVariant) => {
    try {
      onChanged(await update.mutateAsync({ id: v.id, changes: { is_active: !v.is_active } }));
    } catch (e) {
      onError(errorText(e));
    }
  };
  return (
    <View style={styles.table}>
      <Text style={styles.label}>Variants</Text>
      {product.variants.map((v) => (
        <View key={v.id} style={styles.variantRow}>
          {edit?.id === v.id ? (
            <>
              <Text style={styles.variantName}>{v.size} · {v.colour}</Text>
              <View style={{ width: 90 }}><Input style={styles.fill} value={edit.price} onChangeText={(price) => setEdit({ ...edit, price })} keyboardType="decimal-pad" placeholder="Price" /></View>
              <View style={{ width: 90 }}><Input style={styles.fill} value={edit.mrp} onChangeText={(mrp) => setEdit({ ...edit, mrp })} keyboardType="decimal-pad" placeholder="MRP" /></View>
              <View style={{ flex: 1 }}><Input style={styles.fill} value={edit.barcode} onChangeText={(barcode) => setEdit({ ...edit, barcode })} placeholder="Barcode" /></View>
              <Button title="Save" size="sm" onPress={save} loading={update.isPending} />
            </>
          ) : (
            <>
              <Text style={[styles.variantName, !v.is_active ? styles.off : null]}>{v.size} · {v.colour}</Text>
              <Text style={styles.variantPrice}>
                {money(v.price_paise)}{v.mrp_paise !== undefined && v.mrp_paise > v.price_paise ? ` (MRP ${money(v.mrp_paise)})` : ''}
              </Text>
              <Text style={styles.code}>{v.barcode ?? '—'}</Text>
              <Button title="Edit" size="sm" variant="ghost" onPress={() => setEdit({ id: v.id, price: rupees(v.price_paise), mrp: rupees(v.mrp_paise), barcode: v.barcode ?? '' })} />
              <Button title={v.is_active ? 'Off' : 'On'} size="sm" variant="ghost" onPress={() => toggle(v)} />
            </>
          )}
        </View>
      ))}
    </View>
  );
};

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  toolbar: { flexDirection: 'row', gap: spacing.sm, alignItems: 'center', paddingHorizontal: spacing.xl, paddingTop: spacing.md },
  chipBar: { flexGrow: 0, flexShrink: 0 },
  chips: { gap: spacing.sm, paddingHorizontal: spacing.xl, paddingVertical: spacing.md, alignItems: 'center' },
  chip: { paddingHorizontal: spacing.md, paddingVertical: 6, borderRadius: radii.full, backgroundColor: colors.surfaceLight, borderWidth: 1, borderColor: colors.surfaceBorder },
  chipActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  chipText: { fontSize: 13, fontFamily: fonts.medium, color: colors.textSecondary },
  chipTextActive: { color: colors.textInverse },
  inactiveToggle: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, marginLeft: spacing.md },
  list: { paddingHorizontal: spacing.xl, paddingBottom: spacing.xxl, gap: spacing.sm },
  card: { backgroundColor: colors.surface, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.surfaceBorder, padding: spacing.lg, gap: 4 },
  cardHead: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  cardTitle: { ...typography.bodyBold, color: colors.text },
  variants: { ...typography.caption, color: colors.textSecondary },
  hint: { ...typography.caption, color: colors.textMuted },
  label: { ...typography.caption, color: colors.textSecondary, fontFamily: fonts.semibold },
  pair: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, alignItems: 'flex-end' },
  field: { flexGrow: 1, flexBasis: 140, minWidth: 0 },
  fill: { width: '100%' as const, minWidth: 0 },
  wrap: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, alignItems: 'center' },
  error: { ...typography.caption, color: colors.danger },
  table: { gap: spacing.xs, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.surfaceBorder, paddingTop: spacing.md },
  variantRow: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: spacing.sm, minHeight: 40 },
  variantName: { ...typography.body, color: colors.text, width: 120 },
  variantPrice: { ...typography.body, color: colors.text, flex: 1 },
  code: { ...typography.caption, color: colors.textMuted, fontFamily: fonts.medium },
  off: { color: colors.textMuted, textDecorationLine: 'line-through' },
});
