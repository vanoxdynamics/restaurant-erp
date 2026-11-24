/**
 * The hardware shop module's client: types, adapters (the API speaks rupee strings, the apps use integer paise where the server
 * rounds to paise), client functions that take the shared `apiClient`, and TanStack Query hooks. Endpoints are added stage by stage.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient, toPaise } from './client';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Raw = Record<string, any>;

export const HARDWARE_API_PREFIX = '/hardware';
const P = HARDWARE_API_PREFIX;

export interface HardwarePage<T> {
  items: T[];
  total: number;
}

// ---- Stock (stage H2) -------------------------------------------------------------------------------------------------------
// Quantities are in the product's base unit (up to 3 decimals): numbers when read, strings when written, so a typed "12.5"
// reaches the server exactly. A cost per base unit can be a fraction of a paisa (33.3333 a metre), so costs are rupee numbers
// here, not integer paise; only stock value and delivery totals, which the server rounds to paise, are paise.

export interface HardwareStockUnit {
  unit: string;
  factor: number;
}

export interface HardwareStockRow {
  product_id: string;
  product_name: string;
  brand?: string;
  size_spec?: string;
  category_id: string;
  category_name: string;
  sku?: string;
  barcode?: string;
  base_unit: string;
  allow_fraction: boolean;
  price: number;
  quantity: number;
  low_stock_level?: number;
  is_low: boolean;
  is_out: boolean;
  /** Rupees per base unit. Owners and managers only. */
  cost_price?: number;
  stock_value_paise?: number;
  units: HardwareStockUnit[];
}

export interface HardwarePurchaseLine {
  product_id: string;
  product_name: string;
  size_spec?: string;
  unit: string;
  quantity: number;
  unit_cost?: number;
  line_cost_paise?: number;
  base_unit: string;
  base_quantity: number;
  base_unit_cost?: number;
}

export interface HardwarePurchase {
  id: string;
  branch_id: string;
  supplier_name?: string;
  invoice_number?: string;
  received_on: string;
  notes?: string;
  total_cost_paise: number;
  created_by?: string;
  created_at: string;
  lines: HardwarePurchaseLine[];
}

export interface HardwarePurchaseInput {
  branch_id?: string;
  supplier_name?: string;
  invoice_number?: string;
  received_on?: string;
  notes?: string;
  /** `unit` is the product's base unit when left out; `unit_cost` is rupees for one of that unit. */
  lines: { product_id: string; unit?: string; quantity: string; unit_cost?: string }[];
}

export type HardwareMovementKind = 'purchase' | 'sale' | 'sale_void' | 'return' | 'adjustment' | 'count';

export interface HardwareMovement {
  id: string;
  product_id: string;
  product_name: string;
  size_spec?: string;
  base_unit: string;
  kind: HardwareMovementKind;
  quantity: number;
  balance_after: number;
  unit_cost?: number;
  reason?: string;
  purchase_id?: string;
  created_by?: string;
  created_at: string;
}

export interface HardwareAdjustInput {
  branch_id?: string;
  product_id: string;
  /** Base units to add or remove; or `counted`, the physical count. Exactly one. */
  delta?: string;
  counted?: string;
  reason: string;
}

export interface HardwareStockQuery {
  branch_id?: string;
  search?: string;
  low_only?: boolean;
  category_id?: string;
  product_id?: string;
}

const optNumber = (v: unknown) => (v === null || v === undefined ? undefined : Number(v));
const optPaise = (v: unknown) => (v === null || v === undefined ? undefined : toPaise(v as string));

export function adaptHardwareStockRow(raw: Raw): HardwareStockRow {
  return {
    product_id: raw.product_id, product_name: raw.product_name, brand: raw.brand ?? undefined, size_spec: raw.size_spec ?? undefined,
    category_id: raw.category_id, category_name: raw.category_name, sku: raw.sku ?? undefined, barcode: raw.barcode ?? undefined,
    base_unit: raw.base_unit, allow_fraction: raw.allow_fraction, price: Number(raw.price), quantity: raw.quantity,
    low_stock_level: raw.low_stock_level ?? undefined, is_low: raw.is_low, is_out: raw.is_out,
    cost_price: optNumber(raw.cost_price), stock_value_paise: optPaise(raw.stock_value),
    units: (raw.units ?? []).map((u: Raw) => ({ unit: u.unit, factor: Number(u.factor) })),
  };
}

function adaptHardwarePurchase(raw: Raw): HardwarePurchase {
  return {
    id: raw.id, branch_id: raw.branch_id, supplier_name: raw.supplier_name ?? undefined,
    invoice_number: raw.invoice_number ?? undefined, received_on: raw.received_on, notes: raw.notes ?? undefined,
    total_cost_paise: toPaise(raw.total_cost), created_by: raw.created_by ?? undefined, created_at: raw.created_at,
    lines: (raw.lines ?? []).map((l: Raw) => ({
      product_id: l.product_id, product_name: l.product_name, size_spec: l.size_spec ?? undefined, unit: l.unit,
      quantity: l.quantity, unit_cost: optNumber(l.unit_cost), line_cost_paise: optPaise(l.line_cost),
      base_unit: l.base_unit, base_quantity: l.base_quantity, base_unit_cost: optNumber(l.base_unit_cost),
    })),
  };
}

function adaptHardwareMovement(raw: Raw): HardwareMovement {
  return {
    id: raw.id, product_id: raw.product_id, product_name: raw.product_name, size_spec: raw.size_spec ?? undefined,
    base_unit: raw.base_unit, kind: raw.kind, quantity: raw.quantity, balance_after: raw.balance_after,
    unit_cost: optNumber(raw.unit_cost), reason: raw.reason ?? undefined, purchase_id: raw.purchase_id ?? undefined,
    created_by: raw.created_by ?? undefined, created_at: raw.created_at,
  };
}

type StockChanges = { branch_id?: string; low_stock_level?: string | null; cost_price?: string | null };

export const hardwareStockApi = {
  async stock(params: HardwareStockQuery = {}): Promise<HardwareStockRow[]> {
    const raw = await apiClient.request<Raw[]>('GET', `${P}/stock`, {
      query: {
        branch_id: params.branch_id, search: params.search, low_only: params.low_only ? 'true' : undefined,
        category_id: params.category_id, product_id: params.product_id,
      },
    });
    return raw.map(adaptHardwareStockRow);
  },
  /** `low_stock_level` in base units; `cost_price` in rupees per base unit. `null` clears. */
  async updateStock(productId: string, changes: StockChanges): Promise<HardwareStockRow> {
    return adaptHardwareStockRow(await apiClient.request<Raw>('PUT', `${P}/stock/${productId}`, { body: changes }));
  },
  /** Pass the same `idempotencyKey` when retrying so a delivery is not received twice. */
  async receive(input: HardwarePurchaseInput, idempotencyKey: string): Promise<HardwarePurchase> {
    const body = {
      branch_id: input.branch_id, supplier_name: input.supplier_name?.trim() || undefined,
      invoice_number: input.invoice_number?.trim() || undefined, received_on: input.received_on, notes: input.notes?.trim() || undefined,
      lines: input.lines,
    };
    return adaptHardwarePurchase(await apiClient.request<Raw>('POST', `${P}/purchases`, { body, idempotent: idempotencyKey }));
  },
  async purchases(branchId?: string): Promise<HardwarePage<HardwarePurchase>> {
    const raw = await apiClient.request<Raw>('GET', `${P}/purchases`, { query: { branch_id: branchId, limit: 50 } });
    return { items: raw.items.map(adaptHardwarePurchase), total: raw.total };
  },
  async adjust(input: HardwareAdjustInput, idempotencyKey: string): Promise<HardwareStockRow> {
    return adaptHardwareStockRow(await apiClient.request<Raw>('POST', `${P}/adjustments`, {
      body: { ...input, reason: input.reason.trim() }, idempotent: idempotencyKey,
    }));
  },
  async movements(params: { branch_id?: string; product_id?: string; kind?: HardwareMovementKind; limit?: number } = {}): Promise<HardwarePage<HardwareMovement>> {
    const raw = await apiClient.request<Raw>('GET', `${P}/movements`, { query: { ...params, limit: params.limit ?? 50 } });
    return { items: raw.items.map(adaptHardwareMovement), total: raw.total };
  },
};

// ---- Hooks ------------------------------------------------------------------------------------------------------------------

export const HARDWARE_KEYS = { all: ['hardware'] as const };

function useHardwareMutation<A, R>(fn: (args: A) => Promise<R>) {
  const queryClient = useQueryClient();
  return useMutation({ mutationFn: fn, onSuccess: () => queryClient.invalidateQueries({ queryKey: HARDWARE_KEYS.all }) });
}

export function useHardwareStock(params: HardwareStockQuery, enabled = true) {
  return useQuery({ queryKey: ['hardware', 'stock', params], queryFn: () => hardwareStockApi.stock(params), enabled, refetchInterval: 30000 });
}

export function useUpdateHardwareStock() {
  return useHardwareMutation(({ productId, changes }: { productId: string; changes: StockChanges }) =>
    hardwareStockApi.updateStock(productId, changes));
}

export function useReceiveHardwareStock() {
  return useHardwareMutation(({ input, idempotencyKey }: { input: HardwarePurchaseInput; idempotencyKey: string }) =>
    hardwareStockApi.receive(input, idempotencyKey));
}

export function useHardwarePurchases(branchId?: string, enabled = true) {
  return useQuery({ queryKey: ['hardware', 'purchases', branchId ?? ''], queryFn: () => hardwareStockApi.purchases(branchId), enabled });
}

export function useAdjustHardwareStock() {
  return useHardwareMutation(({ input, idempotencyKey }: { input: HardwareAdjustInput; idempotencyKey: string }) =>
    hardwareStockApi.adjust(input, idempotencyKey));
}

export function useHardwareMovements(params: { branch_id?: string; product_id?: string; kind?: HardwareMovementKind }, enabled = true) {
  return useQuery({ queryKey: ['hardware', 'movements', params], queryFn: () => hardwareStockApi.movements(params), enabled });
}
