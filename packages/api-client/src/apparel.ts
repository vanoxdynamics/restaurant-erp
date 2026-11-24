/**
 * The dress shop module's client (`/apparel`): types, adapters, client functions on the shared `apiClient`, and TanStack Query
 * hooks. The API speaks rupee strings; the apps use integer paise, converted here.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient, toPaise, toRupees } from './client';
import type { PrintablePage, PrintRow } from './types';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Raw = Record<string, any>;

export const APPAREL_API_PREFIX = '/apparel';
const P = APPAREL_API_PREFIX;

// ---- Types ------------------------------------------------------------------------------------------------------------------

export interface ApparelSettings {
  prices_include_tax: boolean;
  round_to_rupee: boolean;
  low_rate_percent: number;
  slab_price_paise: number;
  high_rate_percent: number;
  return_days: number;
}

export interface ApparelCategory {
  id: string;
  name: string;
  sort_order: number;
  is_active: boolean;
}

export interface ApparelVariant {
  id: string;
  product_id: string;
  size: string;
  colour: string;
  sku?: string;
  barcode?: string;
  mrp_paise?: number;
  price_paise: number;
  sort_order: number;
  is_active: boolean;
  /** Stock in the branch asked for; undefined when none was asked for. */
  on_hand?: number;
}

export type ApparelTaxMode = 'slab' | 'fixed';

export interface ApparelProduct {
  id: string;
  category_id: string;
  category_name?: string;
  name: string;
  brand?: string;
  style_code?: string;
  hsn_code?: string;
  tax_mode: ApparelTaxMode;
  tax_rate_percent?: number;
  description?: string;
  is_active: boolean;
  variants: ApparelVariant[];
}

export interface ApparelVariantInput {
  size: string;
  colour?: string;
  price_paise: number;
  mrp_paise?: number;
  sku?: string;
  barcode?: string;
}

/** Variants as sizes x colours at one price, or an explicit list. */
export interface ApparelVariantGrid {
  sizes?: string[];
  colours?: string[];
  price_paise?: number;
  mrp_paise?: number;
  variants?: ApparelVariantInput[];
}

export interface ApparelProductInput extends ApparelVariantGrid {
  category_id: string;
  name: string;
  brand?: string;
  style_code?: string;
  hsn_code?: string;
  tax_mode?: ApparelTaxMode;
  tax_rate_percent?: number;
  description?: string;
}

export interface ApparelProductChanges {
  category_id?: string;
  name?: string;
  brand?: string | null;
  style_code?: string | null;
  hsn_code?: string | null;
  tax_mode?: ApparelTaxMode;
  tax_rate_percent?: number | null;
  description?: string | null;
  is_active?: boolean;
}

export interface ApparelVariantChanges {
  size?: string;
  colour?: string;
  price_paise?: number;
  mrp_paise?: number | null;
  sku?: string | null;
  barcode?: string | null;
  sort_order?: number;
  is_active?: boolean;
}

export interface ApparelLookup {
  variant: ApparelVariant;
  product: ApparelProduct;
}

export interface ApparelImportResult {
  products_created: number;
  products_updated: number;
  variants_created: number;
  variants_updated: number;
  categories_created: number;
  errors: { row: number; errors: string[]; data: Record<string, string> }[];
  dry_run: boolean;
}

export interface ApparelPage<T> {
  items: T[];
  total: number;
}

// ---- Adapters ---------------------------------------------------------------------------------------------------------------

const optPaise = (v: unknown) => (v === null || v === undefined ? undefined : toPaise(v as string));
const optRupees = (v: number | null | undefined) => (v === null ? null : v === undefined ? undefined : toRupees(v));
const optRate = (v: number | null | undefined) => (v === null ? null : v === undefined ? undefined : v.toFixed(2));
const blank = (v: string | null | undefined) => (v === undefined ? undefined : v && v.trim() ? v.trim() : null);

export function adaptApparelVariant(raw: Raw): ApparelVariant {
  return {
    id: raw.id,
    product_id: raw.product_id,
    size: raw.size,
    colour: raw.colour,
    sku: raw.sku ?? undefined,
    barcode: raw.barcode ?? undefined,
    mrp_paise: optPaise(raw.mrp),
    price_paise: toPaise(raw.price),
    sort_order: raw.sort_order ?? 0,
    is_active: raw.is_active,
    on_hand: raw.on_hand ?? undefined,
  };
}

export function adaptApparelProduct(raw: Raw): ApparelProduct {
  return {
    id: raw.id,
    category_id: raw.category_id,
    category_name: raw.category_name ?? undefined,
    name: raw.name,
    brand: raw.brand ?? undefined,
    style_code: raw.style_code ?? undefined,
    hsn_code: raw.hsn_code ?? undefined,
    tax_mode: raw.tax_mode,
    tax_rate_percent: raw.tax_rate === null || raw.tax_rate === undefined ? undefined : Number(raw.tax_rate),
    description: raw.description ?? undefined,
    is_active: raw.is_active,
    variants: (raw.variants ?? []).map(adaptApparelVariant),
  };
}

function adaptSettings(raw: Raw): ApparelSettings {
  return {
    prices_include_tax: raw.prices_include_tax,
    round_to_rupee: raw.round_to_rupee,
    low_rate_percent: Number(raw.low_rate),
    slab_price_paise: toPaise(raw.slab_price),
    high_rate_percent: Number(raw.high_rate),
    return_days: raw.return_days,
  };
}

function gridBody(g: ApparelVariantGrid): Raw {
  if (g.variants?.length) {
    return {
      variants: g.variants.map((v) => ({
        size: v.size, colour: v.colour || 'Default', price: toRupees(v.price_paise), mrp: optRupees(v.mrp_paise),
        sku: v.sku?.trim() || undefined, barcode: v.barcode?.trim() || undefined,
      })),
    };
  }
  return {
    sizes: g.sizes ?? [], colours: g.colours ?? [],
    price: g.price_paise === undefined ? undefined : toRupees(g.price_paise), mrp: optRupees(g.mrp_paise),
  };
}

// ---- Client functions -------------------------------------------------------------------------------------------------------

export const apparelApi = {
  async settings(): Promise<ApparelSettings> {
    return adaptSettings(await apiClient.request<Raw>('GET', `${P}/settings`));
  },
  async updateSettings(changes: Partial<ApparelSettings>): Promise<ApparelSettings> {
    const body: Raw = {};
    if (changes.prices_include_tax !== undefined) body.prices_include_tax = changes.prices_include_tax;
    if (changes.round_to_rupee !== undefined) body.round_to_rupee = changes.round_to_rupee;
    if (changes.low_rate_percent !== undefined) body.low_rate = changes.low_rate_percent.toFixed(2);
    if (changes.high_rate_percent !== undefined) body.high_rate = changes.high_rate_percent.toFixed(2);
    if (changes.slab_price_paise !== undefined) body.slab_price = toRupees(changes.slab_price_paise);
    if (changes.return_days !== undefined) body.return_days = changes.return_days;
    return adaptSettings(await apiClient.request<Raw>('PUT', `${P}/settings`, { body }));
  },
  categories(includeInactive = false): Promise<ApparelCategory[]> {
    return apiClient.request<ApparelCategory[]>('GET', `${P}/categories`, { query: { include_inactive: includeInactive ? 'true' : undefined } });
  },
  createCategory(name: string): Promise<ApparelCategory> {
    return apiClient.request<ApparelCategory>('POST', `${P}/categories`, { body: { name } });
  },
  updateCategory(id: string, changes: Partial<Pick<ApparelCategory, 'name' | 'sort_order' | 'is_active'>>): Promise<ApparelCategory> {
    return apiClient.request<ApparelCategory>('PATCH', `${P}/categories/${id}`, { body: changes });
  },
  async products(params: { search?: string; category_id?: string; include_inactive?: boolean; limit?: number; offset?: number } = {}): Promise<ApparelPage<ApparelProduct>> {
    const raw = await apiClient.request<Raw>('GET', `${P}/products`, {
      query: { search: params.search, category_id: params.category_id, include_inactive: params.include_inactive ? 'true' : undefined,
        limit: params.limit ?? 100, offset: params.offset },
    });
    return { items: raw.items.map(adaptApparelProduct), total: raw.total };
  },
  async product(id: string, branchId?: string): Promise<ApparelProduct> {
    return adaptApparelProduct(await apiClient.request<Raw>('GET', `${P}/products/${id}`, { query: { branch_id: branchId } }));
  },
  async createProduct(input: ApparelProductInput): Promise<ApparelProduct> {
    const body: Raw = {
      category_id: input.category_id, name: input.name.trim(), brand: blank(input.brand) ?? undefined,
      style_code: blank(input.style_code) ?? undefined, hsn_code: blank(input.hsn_code) ?? undefined,
      tax_mode: input.tax_mode ?? 'slab', tax_rate: optRate(input.tax_rate_percent) ?? undefined,
      description: blank(input.description) ?? undefined, ...gridBody(input),
    };
    return adaptApparelProduct(await apiClient.request<Raw>('POST', `${P}/products`, { body }));
  },
  async updateProduct(id: string, changes: ApparelProductChanges): Promise<ApparelProduct> {
    const body: Raw = {};
    for (const key of ['category_id', 'name', 'tax_mode', 'is_active'] as const) if (changes[key] !== undefined) body[key] = changes[key];
    for (const key of ['brand', 'style_code', 'hsn_code', 'description'] as const) if (changes[key] !== undefined) body[key] = blank(changes[key]);
    if (changes.tax_rate_percent !== undefined) body.tax_rate = optRate(changes.tax_rate_percent);
    return adaptApparelProduct(await apiClient.request<Raw>('PATCH', `${P}/products/${id}`, { body }));
  },
  async addVariants(productId: string, grid: ApparelVariantGrid): Promise<ApparelProduct> {
    return adaptApparelProduct(await apiClient.request<Raw>('POST', `${P}/products/${productId}/variants`, { body: gridBody(grid) }));
  },
  async updateVariant(id: string, changes: ApparelVariantChanges): Promise<ApparelProduct> {
    const body: Raw = {};
    for (const key of ['size', 'colour', 'sort_order', 'is_active'] as const) if (changes[key] !== undefined) body[key] = changes[key];
    for (const key of ['sku', 'barcode'] as const) if (changes[key] !== undefined) body[key] = blank(changes[key]);
    if (changes.price_paise !== undefined) body.price = toRupees(changes.price_paise);
    if (changes.mrp_paise !== undefined) body.mrp = optRupees(changes.mrp_paise);
    return adaptApparelProduct(await apiClient.request<Raw>('PATCH', `${P}/variants/${id}`, { body }));
  },
  async lookup(code: string, branchId?: string): Promise<ApparelLookup> {
    const raw = await apiClient.request<Raw>('GET', `${P}/variants/lookup`, { query: { code, branch_id: branchId } });
    return { variant: adaptApparelVariant(raw.variant), product: adaptApparelProduct(raw.product) };
  },
  async search(params: { q?: string; category_id?: string; branch_id?: string; limit?: number }): Promise<ApparelProduct[]> {
    const raw = await apiClient.request<Raw[]>('GET', `${P}/catalog/search`, { query: { ...params } });
    return raw.map(adaptApparelProduct);
  },
  importCatalogue(csv: string, dryRun = false, branchId?: string): Promise<ApparelImportResult> {
    return apiClient.request<ApparelImportResult>('POST', `${P}/import`, { body: { csv, dry_run: dryRun, branch_id: branchId }, timeoutMs: 120000 });
  },
};

// ---- Hooks ------------------------------------------------------------------------------------------------------------------

export const APPAREL_KEYS = {
  all: ['apparel'] as const,
  settings: ['apparel', 'settings'] as const,
  categories: (includeInactive: boolean) => ['apparel', 'categories', includeInactive] as const,
  products: (params: object) => ['apparel', 'products', params] as const,
  product: (id: string, branchId?: string) => ['apparel', 'product', id, branchId ?? ''] as const,
  search: (params: object) => ['apparel', 'search', params] as const,
};

function useApparelMutation<A, R>(fn: (args: A) => Promise<R>) {
  const queryClient = useQueryClient();
  return useMutation({ mutationFn: fn, onSuccess: () => queryClient.invalidateQueries({ queryKey: APPAREL_KEYS.all }) });
}

export function useApparelSettings(enabled = true) {
  return useQuery({ queryKey: APPAREL_KEYS.settings, queryFn: apparelApi.settings, enabled });
}

export function useUpdateApparelSettings() {
  return useApparelMutation((changes: Partial<ApparelSettings>) => apparelApi.updateSettings(changes));
}

export function useApparelCategories(includeInactive = false, enabled = true) {
  return useQuery({ queryKey: APPAREL_KEYS.categories(includeInactive), queryFn: () => apparelApi.categories(includeInactive), enabled });
}

export function useCreateApparelCategory() {
  return useApparelMutation((name: string) => apparelApi.createCategory(name));
}

export function useUpdateApparelCategory() {
  return useApparelMutation(({ id, changes }: { id: string; changes: Partial<Pick<ApparelCategory, 'name' | 'sort_order' | 'is_active'>> }) =>
    apparelApi.updateCategory(id, changes));
}

export function useApparelProducts(params: { search?: string; category_id?: string; include_inactive?: boolean } = {}, enabled = true) {
  return useQuery({ queryKey: APPAREL_KEYS.products(params), queryFn: () => apparelApi.products(params), enabled });
}

export function useApparelProduct(id: string | undefined, branchId?: string) {
  return useQuery({ queryKey: APPAREL_KEYS.product(id ?? '', branchId), queryFn: () => apparelApi.product(id!, branchId), enabled: !!id });
}

export function useCreateApparelProduct() {
  return useApparelMutation((input: ApparelProductInput) => apparelApi.createProduct(input));
}

export function useUpdateApparelProduct() {
  return useApparelMutation(({ id, changes }: { id: string; changes: ApparelProductChanges }) => apparelApi.updateProduct(id, changes));
}

export function useAddApparelVariants() {
  return useApparelMutation(({ productId, grid }: { productId: string; grid: ApparelVariantGrid }) => apparelApi.addVariants(productId, grid));
}

export function useUpdateApparelVariant() {
  return useApparelMutation(({ id, changes }: { id: string; changes: ApparelVariantChanges }) => apparelApi.updateVariant(id, changes));
}

/** Counter search; empty `q` lists the first products (of the category). */
export function useApparelSearch(params: { q?: string; category_id?: string; branch_id?: string }, enabled = true) {
  return useQuery({ queryKey: APPAREL_KEYS.search(params), queryFn: () => apparelApi.search({ ...params, limit: 30 }), enabled, staleTime: 15000 });
}

export function useApparelImport() {
  return useApparelMutation(({ csv, dryRun, branchId }: { csv: string; dryRun?: boolean; branchId?: string }) =>
    apparelApi.importCatalogue(csv, dryRun, branchId));
}

// ---- Stock (stage A2) -------------------------------------------------------------------------------------------------------

export interface ApparelStockRow {
  variant_id: string;
  product_id: string;
  product_name: string;
  category_id: string;
  category_name: string;
  size: string;
  colour: string;
  sku?: string;
  barcode?: string;
  price_paise: number;
  quantity: number;
  low_stock_level?: number;
  is_low: boolean;
  is_out: boolean;
  /** Owners and managers only. */
  cost_price_paise?: number;
  stock_value_paise?: number;
}

export interface ApparelPurchaseLine {
  variant_id: string;
  product_name: string;
  size: string;
  colour: string;
  quantity: number;
  unit_cost_paise?: number;
  line_cost_paise?: number;
}

export interface ApparelPurchase {
  id: string;
  branch_id: string;
  supplier_name?: string;
  invoice_number?: string;
  received_on: string;
  notes?: string;
  total_cost_paise: number;
  created_by?: string;
  created_at: string;
  lines: ApparelPurchaseLine[];
}

export interface ApparelPurchaseInput {
  branch_id?: string;
  supplier_name?: string;
  invoice_number?: string;
  received_on?: string;
  notes?: string;
  lines: { variant_id: string; quantity: number; unit_cost_paise?: number }[];
}

export type ApparelMovementKind = 'purchase' | 'sale' | 'sale_void' | 'return' | 'adjustment' | 'count';

export interface ApparelMovement {
  id: string;
  variant_id: string;
  product_name: string;
  size: string;
  colour: string;
  kind: ApparelMovementKind;
  quantity: number;
  balance_after: number;
  unit_cost_paise?: number;
  reason?: string;
  purchase_id?: string;
  created_by?: string;
  created_at: string;
}

export interface ApparelAdjustInput {
  branch_id?: string;
  variant_id: string;
  /** Add or remove pieces; or `counted`, the physical count. Exactly one. */
  delta?: number;
  counted?: number;
  reason: string;
}

function adaptStockRow(raw: Raw): ApparelStockRow {
  return {
    variant_id: raw.variant_id, product_id: raw.product_id, product_name: raw.product_name, category_id: raw.category_id,
    category_name: raw.category_name, size: raw.size, colour: raw.colour, sku: raw.sku ?? undefined,
    barcode: raw.barcode ?? undefined, price_paise: toPaise(raw.price), quantity: raw.quantity,
    low_stock_level: raw.low_stock_level ?? undefined, is_low: raw.is_low, is_out: raw.is_out,
    cost_price_paise: optPaise(raw.cost_price), stock_value_paise: optPaise(raw.stock_value),
  };
}

function adaptPurchase(raw: Raw): ApparelPurchase {
  return {
    id: raw.id, branch_id: raw.branch_id, supplier_name: raw.supplier_name ?? undefined,
    invoice_number: raw.invoice_number ?? undefined, received_on: raw.received_on, notes: raw.notes ?? undefined,
    total_cost_paise: toPaise(raw.total_cost), created_by: raw.created_by ?? undefined, created_at: raw.created_at,
    lines: (raw.lines ?? []).map((l: Raw) => ({
      variant_id: l.variant_id, product_name: l.product_name, size: l.size, colour: l.colour, quantity: l.quantity,
      unit_cost_paise: optPaise(l.unit_cost), line_cost_paise: optPaise(l.line_cost),
    })),
  };
}

function adaptMovement(raw: Raw): ApparelMovement {
  return {
    id: raw.id, variant_id: raw.variant_id, product_name: raw.product_name, size: raw.size, colour: raw.colour, kind: raw.kind,
    quantity: raw.quantity, balance_after: raw.balance_after, unit_cost_paise: optPaise(raw.unit_cost),
    reason: raw.reason ?? undefined, purchase_id: raw.purchase_id ?? undefined, created_by: raw.created_by ?? undefined,
    created_at: raw.created_at,
  };
}

export const apparelStockApi = {
  async stock(params: { branch_id?: string; search?: string; low_only?: boolean; category_id?: string; product_id?: string } = {}): Promise<ApparelStockRow[]> {
    const raw = await apiClient.request<Raw[]>('GET', `${P}/stock`, {
      query: { branch_id: params.branch_id, search: params.search, low_only: params.low_only ? 'true' : undefined,
        category_id: params.category_id, product_id: params.product_id },
    });
    return raw.map(adaptStockRow);
  },
  async updateStock(variantId: string, changes: { branch_id?: string; low_stock_level?: number | null; cost_price_paise?: number | null }): Promise<ApparelStockRow> {
    const body: Raw = { branch_id: changes.branch_id };
    if (changes.low_stock_level !== undefined) body.low_stock_level = changes.low_stock_level;
    if (changes.cost_price_paise !== undefined) body.cost_price = optRupees(changes.cost_price_paise);
    return adaptStockRow(await apiClient.request<Raw>('PUT', `${P}/stock/${variantId}`, { body }));
  },
  /** Pass the same `idempotencyKey` when retrying so a delivery is not received twice. */
  async receive(input: ApparelPurchaseInput, idempotencyKey: string): Promise<ApparelPurchase> {
    const body = {
      branch_id: input.branch_id, supplier_name: input.supplier_name?.trim() || undefined,
      invoice_number: input.invoice_number?.trim() || undefined, received_on: input.received_on, notes: input.notes?.trim() || undefined,
      lines: input.lines.map((l) => ({ variant_id: l.variant_id, quantity: l.quantity,
        unit_cost: l.unit_cost_paise === undefined ? undefined : toRupees(l.unit_cost_paise) })),
    };
    return adaptPurchase(await apiClient.request<Raw>('POST', `${P}/purchases`, { body, idempotent: idempotencyKey }));
  },
  async purchases(branchId?: string): Promise<ApparelPage<ApparelPurchase>> {
    const raw = await apiClient.request<Raw>('GET', `${P}/purchases`, { query: { branch_id: branchId, limit: 50 } });
    return { items: raw.items.map(adaptPurchase), total: raw.total };
  },
  async adjust(input: ApparelAdjustInput, idempotencyKey: string): Promise<ApparelStockRow> {
    return adaptStockRow(await apiClient.request<Raw>('POST', `${P}/adjustments`, { body: { ...input, reason: input.reason.trim() }, idempotent: idempotencyKey }));
  },
  async movements(params: { branch_id?: string; variant_id?: string; kind?: ApparelMovementKind; limit?: number } = {}): Promise<ApparelPage<ApparelMovement>> {
    const raw = await apiClient.request<Raw>('GET', `${P}/movements`, { query: { ...params, limit: params.limit ?? 50 } });
    return { items: raw.items.map(adaptMovement), total: raw.total };
  },
};

export function useApparelStock(params: { branch_id?: string; search?: string; low_only?: boolean; category_id?: string; product_id?: string }, enabled = true) {
  return useQuery({ queryKey: ['apparel', 'stock', params], queryFn: () => apparelStockApi.stock(params), enabled, refetchInterval: 30000 });
}

export function useUpdateApparelStock() {
  return useApparelMutation(({ variantId, changes }: { variantId: string; changes: { branch_id?: string; low_stock_level?: number | null; cost_price_paise?: number | null } }) =>
    apparelStockApi.updateStock(variantId, changes));
}

export function useReceiveApparelStock() {
  return useApparelMutation(({ input, idempotencyKey }: { input: ApparelPurchaseInput; idempotencyKey: string }) =>
    apparelStockApi.receive(input, idempotencyKey));
}

export function useApparelPurchases(branchId?: string, enabled = true) {
  return useQuery({ queryKey: ['apparel', 'purchases', branchId ?? ''], queryFn: () => apparelStockApi.purchases(branchId), enabled });
}

export function useAdjustApparelStock() {
  return useApparelMutation(({ input, idempotencyKey }: { input: ApparelAdjustInput; idempotencyKey: string }) =>
    apparelStockApi.adjust(input, idempotencyKey));
}

export function useApparelMovements(params: { branch_id?: string; variant_id?: string; kind?: ApparelMovementKind }, enabled = true) {
  return useQuery({ queryKey: ['apparel', 'movements', params], queryFn: () => apparelStockApi.movements(params), enabled });
}

// ---- Sales (stage A3/A4) ----------------------------------------------------------------------------------------------------

export type ApparelSaleStatus = 'unpaid' | 'partially_paid' | 'paid' | 'voided';

export interface ApparelSaleLine {
  id: string;
  variant_id: string;
  name: string;
  size: string;
  colour: string;
  barcode?: string;
  quantity: number;
  mrp_paise?: number;
  unit_price_paise: number;
  line_discount_paise: number;
  /** Line discount plus this line's share of the bill discount. */
  discount_paise: number;
  tax_rate_percent: number;
  taxable_paise: number;
  line_total_paise: number;
  returned_quantity: number;
}

export interface ApparelSalePayment {
  id: string;
  payment_mode: string;
  amount_paise: number;
  status: 'success' | 'pending' | 'failed' | 'reversed';
  reference_id?: string | null;
  provider: string;
  billing_ref_no?: string;
  terminal?: Record<string, unknown>;
}

export interface ApparelSale {
  id: string;
  number: string;
  business_date: string;
  status: ApparelSaleStatus;
  subtotal_paise: number;
  discount_paise: number;
  taxable_paise: number;
  cgst_paise: number;
  sgst_paise: number;
  round_off_paise: number;
  total_paise: number;
  paid_paise: number;
  balance_due_paise: number;
  lines: ApparelSaleLine[];
  payments: ApparelSalePayment[];
  created_at: string;
}

export interface ApparelSaleLineInput {
  variant_id: string;
  quantity: number;
  /** Only when the price differs from the variant's: needs a manager PIN. */
  unit_price_paise?: number;
  line_discount_paise?: number;
}

export interface ApparelSaleInput {
  branch_id?: string;
  lines: ApparelSaleLineInput[];
  bill_discount_paise?: number;
  discount_reason?: string;
  customer_id?: string;
  salesperson_id?: string;
  client_sale_id?: string;
  approver_pin?: string;
  notes?: string;
}

export function adaptApparelSale(raw: Raw): ApparelSale {
  return {
    id: raw.id,
    number: raw.sale_number,
    business_date: raw.business_date,
    status: raw.status,
    subtotal_paise: toPaise(raw.subtotal),
    discount_paise: toPaise(raw.discount_amount),
    taxable_paise: toPaise(raw.taxable_value),
    cgst_paise: toPaise(raw.tax_cgst),
    sgst_paise: toPaise(raw.tax_sgst),
    round_off_paise: toPaise(raw.round_off),
    total_paise: toPaise(raw.grand_total),
    paid_paise: toPaise(raw.paid_amount),
    balance_due_paise: toPaise(raw.balance_due),
    lines: (raw.lines ?? []).map((l: Raw): ApparelSaleLine => ({
      id: l.id, variant_id: l.variant_id, name: l.name_snapshot, size: l.size_snapshot, colour: l.colour_snapshot,
      barcode: l.barcode_snapshot ?? undefined, quantity: l.quantity, mrp_paise: optPaise(l.mrp_snapshot),
      unit_price_paise: toPaise(l.unit_price), line_discount_paise: toPaise(l.line_discount), discount_paise: toPaise(l.discount),
      tax_rate_percent: Number(l.tax_rate), taxable_paise: toPaise(l.taxable_value), line_total_paise: toPaise(l.line_total),
      returned_quantity: l.returned_quantity,
    })),
    payments: (raw.payments ?? []).map((p: Raw): ApparelSalePayment => ({
      id: p.id, payment_mode: p.payment_mode, amount_paise: toPaise(p.amount), status: p.status,
      reference_id: p.reference_id ?? null, provider: p.provider,
      billing_ref_no: p.provider_meta?.billing_ref_no ?? undefined, terminal: p.provider_meta ?? undefined,
    })),
    created_at: raw.created_at,
  };
}

const adaptSalePayment = (sale: ApparelSale, id: string) => sale.payments.find((p) => p.id === id)!;

export const apparelSalesApi = {
  /** Makes the invoice and takes the pieces out of stock. Safe to retry with the same key. */
  async create(input: ApparelSaleInput, idempotencyKey: string): Promise<ApparelSale> {
    const body: Raw = {
      branch_id: input.branch_id,
      lines: input.lines.map((l) => ({
        variant_id: l.variant_id, quantity: l.quantity,
        unit_price: l.unit_price_paise === undefined ? undefined : toRupees(l.unit_price_paise),
        line_discount: toRupees(l.line_discount_paise ?? 0),
      })),
      bill_discount: toRupees(input.bill_discount_paise ?? 0),
      discount_reason: input.discount_reason?.trim() || undefined,
      customer_id: input.customer_id, salesperson_id: input.salesperson_id, client_sale_id: input.client_sale_id,
      approver_pin: input.approver_pin || undefined, notes: input.notes?.trim() || undefined,
    };
    return adaptApparelSale(await apiClient.request<Raw>('POST', `${P}/sales`, { body, idempotent: idempotencyKey }));
  },
  async sale(id: string): Promise<ApparelSale> {
    return adaptApparelSale(await apiClient.request<Raw>('GET', `${P}/sales/${id}`));
  },
  async list(params: { branch_id?: string; on_date?: string; status?: ApparelSaleStatus; search?: string; limit?: number } = {}): Promise<ApparelPage<ApparelSale>> {
    const raw = await apiClient.request<Raw>('GET', `${P}/sales`, { query: { ...params, limit: params.limit ?? 50 } });
    return { items: raw.items.map(adaptApparelSale), total: raw.total };
  },
  async pay(saleId: string, input: { mode: string; amountPaise: number; reference?: string }, idempotencyKey: string): Promise<{ changePaise: number }> {
    const raw = await apiClient.request<Raw>('POST', `${P}/sales/${saleId}/payments`, {
      idempotent: idempotencyKey,
      body: { payment_mode: input.mode, amount: toRupees(input.amountPaise), reference_id: input.reference },
    });
    return { changePaise: toPaise(raw.change_due) };
  },
  async split(saleId: string, tenders: { mode: string; amountPaise: number; reference?: string }[], idempotencyKey: string): Promise<void> {
    await apiClient.request<Raw>('POST', `${P}/sales/${saleId}/payments/split`, {
      idempotent: idempotencyKey,
      body: { payments: tenders.map((t) => ({ payment_mode: t.mode, amount: toRupees(t.amountPaise), reference_id: t.reference })) },
    });
  },
  async startTerminal(saleId: string, input: { mode: 'card' | 'upi'; amountPaise: number }, idempotencyKey: string): Promise<ApparelSalePayment> {
    const raw = await apiClient.request<Raw>('POST', `${P}/sales/${saleId}/payments/terminal`, {
      idempotent: idempotencyKey,
      body: { payment_mode: input.mode, amount: toRupees(input.amountPaise), provider: 'pinelabs' },
    });
    return adaptSalePayment(adaptApparelSale(raw.sale), raw.payment.id);
  },
  async completeTerminal(paymentId: string, outcome: object): Promise<void> {
    await apiClient.request<Raw>('POST', `${P}/payments/${paymentId}/terminal/complete`, { body: outcome });
  },
  async reverse(paymentId: string, body: { reason: string; approver_pin?: string; details?: object; check_only?: boolean }): Promise<void> {
    await apiClient.request<Raw>('POST', `${P}/payments/${paymentId}/reverse`, { body: { ...body, approver_pin: body.approver_pin || undefined } });
  },
  /** Reason and PIN are only needed once a payment was attempted; a sale nobody tried to pay is cancelled on confirmation. */
  async void(saleId: string, reason?: string, approverPin?: string): Promise<ApparelSale> {
    return adaptApparelSale(await apiClient.request<Raw>('POST', `${P}/sales/${saleId}/void`, {
      body: { reason: reason?.trim() || undefined, approver_pin: approverPin || undefined },
    }));
  },
  async receiptPrint(saleId: string, paperMm: 58 | 80): Promise<PrintablePage> {
    const raw = await apiClient.request<Raw>('GET', `${P}/sales/${saleId}/receipt`, { query: { paper_mm: paperMm } });
    return {
      reference: raw.sale_number, paper_mm: raw.paper_mm === 58 ? 58 : 80,
      rows: (raw.rows ?? []).map((r: Raw): PrintRow => ({ style: r.style, align: r.align, text: r.text })), text: raw.text ?? '',
    };
  },
  receiptHtml(saleId: string, paperMm: 58 | 80): Promise<string> {
    return apiClient.request<string>('GET', `${P}/sales/${saleId}/receipt`, { query: { format: 'html', paper_mm: paperMm }, text: true });
  },
};

export function useApparelSale(id: string | undefined) {
  return useQuery({ queryKey: ['apparel', 'sale', id], queryFn: () => apparelSalesApi.sale(id!), enabled: !!id });
}

/** Creates a sale. Stock changes, so every apparel query (stock, search) is refreshed. */
export function useCreateApparelSale() {
  return useApparelMutation(({ input, idempotencyKey }: { input: ApparelSaleInput; idempotencyKey: string }) =>
    apparelSalesApi.create(input, idempotencyKey));
}

export function useVoidApparelSale() {
  return useApparelMutation(({ saleId, reason, approverPin }: { saleId: string; reason?: string; approverPin?: string }) =>
    apparelSalesApi.void(saleId, reason, approverPin));
}

// ---- Returns and store credit (stage A5) ------------------------------------------------------------------------------------

export type ApparelRefundMode = 'cash' | 'store_credit';

export interface ApparelReturnLine {
  id: string;
  sale_line_id: string;
  quantity: number;
  amount_paise: number;
  restock: boolean;
}

export interface ApparelReturn {
  id: string;
  number: string;
  sale_id: string;
  business_date: string;
  reason: string;
  refund_mode: ApparelRefundMode;
  total_paise: number;
  /** The code printed on the credit note, when the customer took store credit. */
  store_credit_code?: string;
  lines: ApparelReturnLine[];
  created_at: string;
}

export interface ApparelStoreCredit {
  id: string;
  code: string;
  amount_paise: number;
  balance_paise: number;
  status: 'open' | 'used' | 'cancelled';
}

export interface ApparelReturnInput {
  sale_id: string;
  lines: { sale_line_id: string; quantity: number; restock: boolean }[];
  reason: string;
  refund_mode: ApparelRefundMode;
  client_return_id?: string;
  approver_pin?: string;
}

export function adaptApparelReturn(raw: Raw): ApparelReturn {
  return {
    id: raw.id,
    number: raw.return_number,
    sale_id: raw.sale_id,
    business_date: raw.business_date,
    reason: raw.reason,
    refund_mode: raw.refund_mode,
    total_paise: toPaise(raw.total_amount),
    store_credit_code: raw.store_credit_code ?? undefined,
    lines: (raw.lines ?? []).map((l: Raw): ApparelReturnLine => ({
      id: l.id, sale_line_id: l.sale_line_id, quantity: l.quantity, amount_paise: toPaise(l.amount), restock: l.restock,
    })),
    created_at: raw.created_at,
  };
}

export function adaptApparelStoreCredit(raw: Raw): ApparelStoreCredit {
  return { id: raw.id, code: raw.code, amount_paise: toPaise(raw.amount), balance_paise: toPaise(raw.balance), status: raw.status };
}

export const apparelReturnsApi = {
  /** Safe to retry with the same key (and the same client_return_id). */
  async create(input: ApparelReturnInput, idempotencyKey: string): Promise<ApparelReturn> {
    const body = {
      sale_id: input.sale_id, lines: input.lines, reason: input.reason.trim(), refund_mode: input.refund_mode,
      client_return_id: input.client_return_id, approver_pin: input.approver_pin || undefined,
    };
    return adaptApparelReturn(await apiClient.request<Raw>('POST', `${P}/returns`, { body, idempotent: idempotencyKey }));
  },
  async list(params: { branch_id?: string; sale_id?: string; on_date?: string; limit?: number } = {}): Promise<ApparelPage<ApparelReturn>> {
    const raw = await apiClient.request<Raw>('GET', `${P}/returns`, { query: { ...params, limit: params.limit ?? 50 } });
    return { items: raw.items.map(adaptApparelReturn), total: raw.total };
  },
  /** The sale to take goods back from: by invoice number, or by the customer's phone. Newest first. */
  async findSales(params: { search?: string; phone?: string; branch_id?: string }): Promise<ApparelSale[]> {
    const raw = await apiClient.request<Raw>('GET', `${P}/sales`, { query: { ...params, status: 'paid', limit: 20 } });
    return raw.items.map(adaptApparelSale);
  },
  async storeCredit(code: string): Promise<ApparelStoreCredit> {
    return adaptApparelStoreCredit(await apiClient.request<Raw>('GET', `${P}/store-credits/${encodeURI(code.trim())}`));
  },
  /** Spend store credit on a sale: the credit pays what it has, up to `amountPaise` and what the sale owes. */
  async payWithCredit(saleId: string, code: string, amountPaise: number, idempotencyKey: string): Promise<void> {
    await apiClient.request<Raw>('POST', `${P}/sales/${saleId}/payments`, {
      idempotent: idempotencyKey,
      body: { payment_mode: 'credit_note', amount: toRupees(amountPaise), credit_note_code: code.trim() },
    });
  },
  async receiptPrint(returnId: string, paperMm: 58 | 80): Promise<PrintablePage> {
    const raw = await apiClient.request<Raw>('GET', `${P}/returns/${returnId}/receipt`, { query: { paper_mm: paperMm } });
    return {
      reference: raw.sale_number, paper_mm: raw.paper_mm === 58 ? 58 : 80,
      rows: (raw.rows ?? []).map((r: Raw): PrintRow => ({ style: r.style, align: r.align, text: r.text })), text: raw.text ?? '',
    };
  },
  receiptHtml(returnId: string, paperMm: 58 | 80): Promise<string> {
    return apiClient.request<string>('GET', `${P}/returns/${returnId}/receipt`, { query: { format: 'html', paper_mm: paperMm }, text: true });
  },
};

export function useCreateApparelReturn() {
  return useApparelMutation(({ input, idempotencyKey }: { input: ApparelReturnInput; idempotencyKey: string }) =>
    apparelReturnsApi.create(input, idempotencyKey));
}

// ---- Labels and reports (stage A6) ------------------------------------------------------------------------------------------

export interface ApparelDayReport {
  date: string;
  sales: { count: number; units: number; subtotal_paise: number; discount_paise: number; taxable_paise: number; cgst_paise: number;
    sgst_paise: number; round_off_paise: number; total_paise: number };
  tax_by_rate: { rate_percent: number; taxable_paise: number; tax_paise: number }[];
  payments: { mode: string; count: number; amount_paise: number }[];
  returns: { count: number; total_paise: number; cash_paise: number; store_credit_paise: number };
  net_cash_paise: number;
  top_products: { name: string; units: number; value_paise: number }[];
  units_by_size: { size: string; units: number }[];
}

/** One person's shift: invoices paid in it, how they were paid and goods taken back. */
export interface ApparelShiftReport {
  shift_id: string;
  staff_name: string;
  status: string;
  start_time: string;
  end_time?: string;
  sales: { count: number; units: number; subtotal_paise: number; discount_paise: number; total_paise: number };
  payments: { mode: string; count: number; amount_paise: number }[];
  returns: { count: number; total_paise: number; cash_paise: number; store_credit_paise: number };
  net_cash_paise: number;
}

export interface ApparelProductSalesRow {
  name: string;
  units: number;
  value_paise: number;
  sizes: { size: string; units: number; value_paise: number }[];
}

export interface ApparelProductSales {
  date_from: string;
  date_to: string;
  units: number;
  value_paise: number;
  products: ApparelProductSalesRow[];
}

export interface ApparelLabelItem {
  variant_id: string;
  copies: number;
}

export function adaptApparelDayReport(raw: Raw): ApparelDayReport {
  const s = raw.sales;
  return {
    date: raw.date,
    sales: { count: s.count, units: s.units, subtotal_paise: toPaise(s.subtotal), discount_paise: toPaise(s.discount_amount),
      taxable_paise: toPaise(s.taxable_value), cgst_paise: toPaise(s.tax_cgst), sgst_paise: toPaise(s.tax_sgst),
      round_off_paise: toPaise(s.round_off), total_paise: toPaise(s.grand_total) },
    tax_by_rate: raw.tax_by_rate.map((t: Raw) => ({ rate_percent: Number(t.rate), taxable_paise: toPaise(t.taxable_value), tax_paise: toPaise(t.tax) })),
    payments: raw.payments.map((p: Raw) => ({ mode: p.mode, count: p.count, amount_paise: toPaise(p.amount) })),
    returns: { count: raw.returns.count, total_paise: toPaise(raw.returns.total), cash_paise: toPaise(raw.returns.cash_total),
      store_credit_paise: toPaise(raw.returns.store_credit_total) },
    net_cash_paise: toPaise(raw.net_cash),
    top_products: raw.top_products.map((p: Raw) => ({ name: p.name, units: p.units, value_paise: toPaise(p.value) })),
    units_by_size: raw.units_by_size.map((z: Raw) => ({ size: z.size, units: z.units })),
  };
}

export function adaptApparelShiftReport(raw: Raw): ApparelShiftReport {
  return {
    shift_id: raw.shift_id, staff_name: raw.staff_name, status: raw.status, start_time: raw.start_time, end_time: raw.end_time ?? undefined,
    sales: { count: raw.sales.count, units: raw.sales.units, subtotal_paise: toPaise(raw.sales.subtotal),
      discount_paise: toPaise(raw.sales.discount_amount), total_paise: toPaise(raw.sales.grand_total) },
    payments: raw.payments.map((p: Raw) => ({ mode: p.mode, count: p.count, amount_paise: toPaise(p.amount) })),
    returns: { count: raw.returns.count, total_paise: toPaise(raw.returns.total), cash_paise: toPaise(raw.returns.cash_total),
      store_credit_paise: toPaise(raw.returns.store_credit_total) },
    net_cash_paise: toPaise(raw.net_cash),
  };
}

export const apparelReportsApi = {
  /** The signed-in person's open shift (404 when none), or a given shift for a manager. */
  async shift(shiftId?: string): Promise<ApparelShiftReport> {
    return adaptApparelShiftReport(await apiClient.request<Raw>('GET', `${P}/reports/shift`, { query: shiftId ? { shift_id: shiftId } : {} }));
  },
  async day(params: { date?: string; branch_id?: string } = {}): Promise<ApparelDayReport> {
    return adaptApparelDayReport(await apiClient.request<Raw>('GET', `${P}/reports/day`, { query: { ...params } }));
  },
  async salesByProduct(params: { from: string; to: string; branch_id?: string }): Promise<ApparelProductSales> {
    const raw = await apiClient.request<Raw>('GET', `${P}/reports/sales-by-product`, { query: { ...params } });
    return {
      date_from: raw.date_from, date_to: raw.date_to, units: raw.units, value_paise: toPaise(raw.value),
      products: raw.products.map((p: Raw): ApparelProductSalesRow => ({
        name: p.name, units: p.units, value_paise: toPaise(p.value),
        sizes: p.sizes.map((z: Raw) => ({ size: z.size, units: z.units, value_paise: toPaise(z.value) })),
      })),
    };
  },
  /** The label sheet as a printable HTML page: open it in a window and print (A4 sticker sheets). */
  labelsHtml(items: ApparelLabelItem[], options: { columns?: number; label_height_mm?: number } = {}): Promise<string> {
    return apiClient.request<string>('POST', `${P}/labels`, { body: { items, ...options }, text: true });
  },
  async customerSales(customerId: string): Promise<ApparelPage<ApparelSale>> {
    const raw = await apiClient.request<Raw>('GET', `${P}/customers/${customerId}/sales`, { query: { limit: 50 } });
    return { items: raw.items.map(adaptApparelSale), total: raw.total };
  },
};

export function useApparelShiftReport(shiftId: string | undefined, enabled = true) {
  return useQuery({ queryKey: ['apparel', 'report', 'shift', shiftId ?? 'current'], queryFn: () => apparelReportsApi.shift(shiftId), enabled, refetchInterval: 30000 });
}

export function useApparelDayReport(params: { date?: string; branch_id?: string }, enabled = true) {
  return useQuery({ queryKey: ['apparel', 'report', 'day', params], queryFn: () => apparelReportsApi.day(params), enabled, refetchInterval: 60000 });
}

export function useApparelSalesByProduct(params: { from: string; to: string; branch_id?: string }, enabled = true) {
  return useQuery({ queryKey: ['apparel', 'report', 'products', params], queryFn: () => apparelReportsApi.salesByProduct(params), enabled });
}

export function useApparelCustomerSales(customerId: string | undefined) {
  return useQuery({ queryKey: ['apparel', 'customer-sales', customerId], queryFn: () => apparelReportsApi.customerSales(customerId!), enabled: !!customerId });
}
