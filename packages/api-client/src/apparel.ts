/**
 * The dress shop module's client (`/apparel`): types, adapters, client functions on the shared `apiClient`, and TanStack Query
 * hooks. The API speaks rupee strings; the apps use integer paise, converted here.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient, toPaise, toRupees } from './client';

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
