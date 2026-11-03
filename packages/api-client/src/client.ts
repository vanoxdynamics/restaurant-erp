/**
 * HTTP client for the Restaurant ERP FastAPI backend.
 *
 * The backend speaks rupee decimal strings ("525.00") and lower-case enums; the apps use integer
 * paise and the enums in @repo/shared-utils. All conversion happens in the adapters below so
 * screens never see raw API shapes.
 */
import {
  KitchenStation,
  ShiftStatus,
  StaffRole,
  TableStatus,
  VoidApprovalStatus,
} from '@repo/shared-utils';
import {
  AuthSession,
  CartLine,
  KitchenTicket,
  Bill,
  BillPayment,
  BoardTicket,
  OrderDetail,
  OrderLine,
  PaymentInput,
  PaymentOutcome,
  BranchSummary,
  Category,
  DiningTable,
  Floor,
  FraudAuditLog,
  LiveKPIs,
  MenuItem,
  MenuItemUpdate,
  PaymentBreakdown,
  ShiftClosureAudit,
  RosterEntry,
  DeviceLink,
  DeviceSetupCode,
  StaffUser,
  TopSellingItem,
  VoidApprovalItem,
  ZReport,
} from './types';

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

// ---------------------------------------------------------------------------
// Conversions
// ---------------------------------------------------------------------------

/** "525.50" | 525.5 -> 52550 */
export function toPaise(value: string | number | null | undefined): number {
  if (value === null || value === undefined || value === '') return 0;
  return Math.round(Number(value) * 100);
}

/** 52550 -> "525.50" */
export function toRupees(paise: number): string {
  return (Math.round(paise) / 100).toFixed(2);
}

const TABLE_STATUS: Record<string, TableStatus> = {
  vacant: TableStatus.AVAILABLE,
  occupied: TableStatus.OCCUPIED,
  billed: TableStatus.BILLED,
  reserved: TableStatus.RESERVED,
};

const STATION_FROM_API: Record<string, KitchenStation> = {
  kitchen: KitchenStation.MAIN_KITCHEN,
  grill: KitchenStation.GRILL,
  dessert: KitchenStation.DESSERT,
};

/** Stations the backend supports, in display order. */
export const SUPPORTED_STATIONS: KitchenStation[] = [
  KitchenStation.MAIN_KITCHEN,
  KitchenStation.GRILL,
  KitchenStation.DESSERT,
];

function stationToApi(station: KitchenStation | string | undefined): string {
  const entry = Object.entries(STATION_FROM_API).find(([, s]) => s === station);
  return entry ? entry[0] : 'kitchen';
}

const roleFromApi = (role: string): StaffRole => (role.toUpperCase() as StaffRole);
const roleToApi = (role: StaffRole | string): string => String(role).toLowerCase();

// ---------------------------------------------------------------------------
// Raw API shapes (only the fields the adapters read)
// ---------------------------------------------------------------------------

type Raw = Record<string, any>;
interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

// ---------------------------------------------------------------------------
// Adapters
// ---------------------------------------------------------------------------

function adaptStaff(raw: Raw): StaffUser {
  return {
    id: raw.id,
    username: raw.username,
    email: raw.email ?? undefined,
    full_name: raw.full_name,
    role: roleFromApi(raw.role),
    branch_id: raw.branch_id ?? undefined,
    is_active: raw.is_active,
  };
}

function adaptMenuItem(raw: Raw): MenuItem {
  return {
    id: raw.id,
    category_id: raw.category_id,
    name: raw.name,
    description: raw.description ?? undefined,
    base_price_paise: toPaise(raw.base_price),
    tax_rate_percent: Number(raw.tax_rate),
    station: STATION_FROM_API[raw.station] ?? KitchenStation.MAIN_KITCHEN,
    is_active: raw.is_active,
    is_available_86: raw.is_available_86,
    image_url: raw.image_url ?? undefined,
    variants: (raw.variants ?? [])
      .filter((v: Raw) => v.is_active !== false)
      .map((v: Raw) => ({
        id: v.id,
        item_id: v.item_id,
        name: v.name,
        price_paise: toPaise(v.price),
        is_available_86: v.is_available_86,
      })),
    modifier_group_ids: (raw.modifier_groups ?? []).map((g: Raw) => g.id),
    modifier_groups: (raw.modifier_groups ?? []).map((g: Raw) => ({
      id: g.id,
      name: g.name,
      min_selection: g.min_selection,
      max_selection: g.max_selection,
      is_forced: g.min_selection > 0,
      modifiers: (g.modifiers ?? []).map((m: Raw) => ({
        id: m.id,
        group_id: m.group_id,
        name: m.name,
        price_paise: toPaise(m.price),
        is_available: m.is_available,
      })),
    })),
    created_at: raw.created_at,
    updated_at: raw.updated_at,
  };
}

function adaptCategory(raw: Raw): Category {
  const items = (raw.items ?? []).map(adaptMenuItem);
  return {
    id: raw.id,
    name: raw.name,
    description: raw.description ?? undefined,
    sort_order: raw.sort_order,
    station_default: items[0]?.station ?? KitchenStation.MAIN_KITCHEN,
    tax_rate_percent: items[0]?.tax_rate_percent ?? 5,
    is_active: raw.is_active,
    items,
  };
}

const AUDIT_HIGH_RISK = new Set(['kot_void', 'bill_void', 'high_discount', 'cash_variance', 'login_lockout']);

function rupees(value: unknown): string {
  return `₹${Math.round(Number(value ?? 0)).toLocaleString('en-IN')}`;
}

/** One-line description of an audit entry, built from its structured details. */
function describeAudit(raw: Raw): string {
  const d: Raw = raw.details ?? {};
  switch (raw.action_type) {
    case 'kot_void':
      return `Voided ${d.kot_number ?? 'KOT'}${d.reason ? ` — ${d.reason}` : ''}`;
    case 'order_cancel':
      return `Cancelled order ${d.order_number ?? ''}${d.reason ? ` — ${d.reason}` : ''}`.trim();
    case 'order_item_cancel':
      return `Cancelled ${d.item ?? 'item'} on ${d.order_number ?? 'order'}`;
    case 'bill_void':
      return `Voided bill ${d.bill_number ?? ''}${d.reason ? ` — ${d.reason}` : ''}`.trim();
    case 'high_discount':
      return `Discount ${rupees(raw.amount)} on ${d.bill_number ?? 'bill'} (subtotal ${rupees(d.subtotal)})`;
    case 'table_transfer':
      return 'Table transfer';
    case 'table_merge':
      return 'Tables merged';
    case 'login_lockout':
      return `Sign-in locked after failed attempts${d.username ? ` (${d.username})` : ''}`;
    case 'cash_variance':
      return `Cash variance at shift close: expected ${rupees(d.expected)}, counted ${rupees(d.dropped)}`;
    default:
      return String(raw.action_type).replace(/_/g, ' ');
  }
}

function adaptAudit(raw: Raw): FraudAuditLog {
  return {
    id: raw.id,
    action_type: raw.action_type,
    actor_id: raw.actor_id ?? '',
    actor_name: raw.actor_name ?? 'System',
    details: describeAudit(raw),
    amount_paise: raw.amount != null ? Math.abs(toPaise(raw.amount)) : undefined,
    risk_level: AUDIT_HIGH_RISK.has(raw.action_type) ? 'HIGH' : 'MEDIUM',
    timestamp: raw.created_at,
  };
}

function adaptVoidRequest(raw: Raw): VoidApprovalItem {
  const items: Raw[] = raw.items ?? [];
  return {
    id: raw.id,
    kot_id: raw.kot_id,
    kot_number: raw.kot_number,
    station: STATION_FROM_API[raw.station] ?? KitchenStation.MAIN_KITCHEN,
    kot_status: raw.kot_status,
    order_id: raw.order_id,
    table_number: raw.table_number ?? 'Takeaway',
    requested_by_user_id: raw.requested_by_id,
    requested_by_user_name: raw.requested_by_name,
    item_name: items.map((i) => i.name).join(', '),
    quantity: items.reduce((sum, i) => sum + (i.quantity ?? 0), 0),
    items: items.map((i) => ({ name: i.name, quantity: i.quantity })),
    amount_paise: toPaise(raw.amount),
    wastage_reason: raw.reason,
    status: String(raw.status).toUpperCase() as VoidApprovalStatus,
    requested_at: raw.created_at,
    reviewed_at: raw.reviewed_at ?? undefined,
    reviewed_by_user_id: raw.reviewed_by_name ?? undefined,
    review_note: raw.review_note ?? undefined,
  };
}

function adaptBoardTicket(raw: Raw): BoardTicket {
  return {
    id: raw.id,
    kot_number: raw.kot_number,
    station: STATION_FROM_API[raw.station] ?? KitchenStation.MAIN_KITCHEN,
    status: raw.status,
    table_label: raw.table_number ?? 'Takeaway',
    order_id: raw.order_id,
    order_number: raw.order_number ?? '',
    items: (raw.items ?? [])
      .filter((i: Raw) => i.status !== 'voided')
      .map((i: Raw) => ({ name: i.item_name, quantity: i.quantity, notes: i.notes ?? undefined })),
    created_at: raw.created_at,
  };
}

function adaptOrderLine(raw: Raw): OrderLine {
  return {
    id: raw.id,
    item_id: raw.item_id,
    variant_id: raw.variant_id ?? undefined,
    name: raw.name_snapshot,
    variant_name: raw.variant_name_snapshot ?? undefined,
    modifiers: (raw.modifiers ?? []).map((m: Raw) => m.name_snapshot),
    quantity: raw.quantity,
    unit_price_paise: toPaise(raw.unit_price_snapshot),
    line_total_paise: toPaise(raw.line_total),
    status: raw.status,
    notes: raw.notes ?? undefined,
    station: STATION_FROM_API[raw.station_snapshot] ?? KitchenStation.MAIN_KITCHEN,
  };
}

function adaptOrder(raw: Raw): OrderDetail {
  return {
    id: raw.id,
    order_number: raw.order_number,
    order_type: raw.order_type,
    status: raw.status,
    table_id: raw.table_id ?? undefined,
    guest_count: raw.guest_count,
    version: raw.version,
    subtotal_paise: toPaise(raw.subtotal),
    created_at: raw.created_at,
    lines: (raw.items ?? []).map(adaptOrderLine),
  };
}

function adaptTicket(raw: Raw): KitchenTicket {
  return {
    id: raw.id,
    kot_number: raw.kot_number,
    station: STATION_FROM_API[raw.station] ?? KitchenStation.MAIN_KITCHEN,
    status: raw.status,
    items: (raw.items ?? []).map((i: Raw) => ({
      order_item_id: i.order_item_id,
      name: i.item_name,
      quantity: i.quantity,
      notes: i.notes ?? undefined,
      status: i.status,
    })),
    created_at: raw.created_at,
  };
}

const cartToApi = (line: CartLine) => ({
  item_id: line.item_id,
  variant_id: line.variant_id,
  quantity: line.quantity,
  notes: line.notes || undefined,
  modifier_ids: line.modifier_ids,
});

function adaptShift(raw: Raw): ShiftClosureAudit {
  return {
    id: raw.id,
    user_id: raw.user_id,
    cashier_name: raw.user_name,
    branch_id: raw.branch_id,
    start_time: raw.start_time,
    end_time: raw.end_time ?? undefined,
    opening_float_paise: toPaise(raw.opening_cash),
    cash_sales_paise: toPaise(raw.cash_sales),
    card_sales_paise: 0,
    upi_sales_paise: 0,
    expected_cash_paise: toPaise(raw.expected_cash),
    blind_drop_cash_paise: toPaise(raw.blind_drop_cash),
    variance_paise: toPaise(raw.cash_variance),
    status: String(raw.status).toUpperCase() as ShiftStatus,
  };
}

function adaptPayments(raw: Raw | undefined): PaymentBreakdown {
  return {
    cash_paise: toPaise(raw?.cash),
    card_paise: toPaise(raw?.card),
    upi_paise: toPaise(raw?.upi),
    total_paise: toPaise(raw?.total),
  };
}

function adaptTopItems(rows: Raw[] | undefined): TopSellingItem[] {
  return (rows ?? []).map((r) => ({
    item_id: r.name,
    name: r.name,
    quantity: r.quantity,
    total_sales_paise: toPaise(r.total),
    category_name: r.category ?? '',
  }));
}

function adaptBill(raw: Raw): Bill {
  return {
    id: raw.id,
    order_id: raw.order_id,
    bill_number: raw.bill_number,
    status: raw.status,
    subtotal_paise: toPaise(raw.subtotal),
    cgst_paise: toPaise(raw.tax_cgst),
    sgst_paise: toPaise(raw.tax_sgst),
    discount_paise: toPaise(raw.discount_amount),
    grand_total_paise: toPaise(raw.grand_total),
    paid_paise: toPaise(raw.paid_amount),
    balance_due_paise: toPaise(raw.balance_due),
    settled_at: raw.settled_at ?? undefined,
    payments: (raw.payments ?? []).map(
      (p: Raw): BillPayment => ({
        id: p.id,
        payment_mode: p.payment_mode,
        amount_paise: toPaise(p.amount),
        reference_id: p.reference_id ?? undefined,
        provider: p.provider ?? 'manual',
        status: p.status,
        created_at: p.created_at,
      }),
    ),
    created_at: raw.created_at,
  };
}

function paymentBody(p: PaymentInput): Raw {
  return { payment_mode: p.payment_mode, amount: toRupees(p.amount_paise), reference_id: p.reference_id || undefined };
}

// ---------------------------------------------------------------------------
// Client
// ---------------------------------------------------------------------------

export interface ApiClientOptions {
  baseUrl?: string;
  token?: string;
  onUnauthorized?: () => void;
}

export function newIdempotencyKey(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

export class RestaurantApiClient {
  private baseUrl: string;
  private token?: string;
  private onUnauthorized?: () => void;

  constructor(options: ApiClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? 'http://localhost:8000').replace(/\/$/, '');
    this.token = options.token;
    this.onUnauthorized = options.onUnauthorized;
  }

  configure(options: ApiClientOptions) {
    if (options.baseUrl) this.baseUrl = options.baseUrl.replace(/\/$/, '');
    if ('token' in options) this.token = options.token;
    if (options.onUnauthorized) this.onUnauthorized = options.onUnauthorized;
  }

  setToken(token?: string) {
    this.token = token;
  }

  private async request<T>(
    method: string,
    path: string,
    options: { body?: unknown; query?: Record<string, string | number | undefined>;
      idempotent?: boolean | string;
      headers?: Record<string, string>;
    } = {},
  ): Promise<T> {
    const query = Object.entries(options.query ?? {})
      .filter(([, v]) => v !== undefined && v !== '')
      .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
      .join('&');
    const url = `${this.baseUrl}/api/v1${path}${query ? `?${query}` : ''}`;

    const headers: Record<string, string> = { Accept: 'application/json', ...options.headers };
    if (options.body !== undefined) headers['Content-Type'] = 'application/json';
    if (this.token) headers.Authorization = `Bearer ${this.token}`;
    if (options.idempotent) {
      headers['X-Idempotency-Key'] = typeof options.idempotent === 'string' ? options.idempotent : newIdempotencyKey();
    }

    let response: Response;
    try {
      response = await fetch(url, {
        method,
        headers,
        body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      });
    } catch {
      throw new ApiError(0, 'network_error', 'Cannot reach the server. Check your connection.');
    }

    if (response.status === 204) return undefined as T;
    const text = await response.text();
    const data = text ? JSON.parse(text) : undefined;

    if (!response.ok) {
      const err = data?.error;
      if (response.status === 401 && this.token) this.onUnauthorized?.();
      const message =
        err?.message ??
        (Array.isArray(data?.detail) ? data.detail.map((d: Raw) => d.msg).join('; ') : undefined) ??
        `Request failed (${response.status})`;
      throw new ApiError(response.status, err?.code ?? 'request_failed', message, err?.details ?? {});
    }
    return data as T;
  }

  // ---- Auth & branches --------------------------------------------------------

  /** Staff PIN sign-in on a branch device (POS). */
  /** Staff PIN sign-in on a branch device. Pass `staffId` to check the PIN against the person chosen on screen. */
  async pinLogin(branchId: string, pin: string, deviceId?: string, staffId?: string): Promise<AuthSession> {
    const raw = await this.request<Raw>('POST', '/staff/auth/pin', {
      body: { branch_id: branchId, pin, device_id: deviceId, staff_id: staffId },
    });
    this.token = raw.access_token;
    const me = await this.getMe();
    return { access_token: raw.access_token, expires_at: Date.now() + raw.expires_in * 1000, user: me };
  }

  /** A manager makes a one-time code (valid 15 minutes) for linking a new tablet. */
  async createDeviceSetupCode(branchId?: string): Promise<DeviceSetupCode> {
    return this.request<DeviceSetupCode>('POST', '/staff/devices/setup-codes', { body: { branch_id: branchId } });
  }

  /** A new tablet trades its setup code for a device token. No sign-in is involved. */
  async linkDevice(code: string, deviceId: string): Promise<DeviceLink> {
    return this.request<DeviceLink>('POST', '/staff/devices/link', { body: { code, device_id: deviceId } });
  }

  /** Staff who can sign in at this branch's tablets, before anyone is signed in. */
  async getSignInRoster(deviceToken: string): Promise<RosterEntry[]> {
    const rows = await this.request<Raw[]>('GET', '/staff/auth/roster', { headers: { 'X-Device-Token': deviceToken } });
    return rows.map((r) => ({ id: r.id, full_name: r.full_name, role: roleFromApi(r.role) }));
  }

  async login(username: string, password: string): Promise<AuthSession> {
    const raw = await this.request<Raw>('POST', '/staff/auth/login', { body: { username, password } });
    this.token = raw.access_token;
    const me = await this.getMe();
    return { access_token: raw.access_token, expires_at: Date.now() + raw.expires_in * 1000, user: me };
  }

  async getMe(): Promise<StaffUser> {
    return adaptStaff(await this.request<Raw>('GET', '/staff/me'));
  }

  async getBranches(): Promise<BranchSummary[]> {
    const rows = await this.request<Raw[]>('GET', '/floor/branches');
    return rows.map((b) => ({ id: b.id, name: b.name, code: b.code, is_active: b.is_active }));
  }

  // ---- Floor ---------------------------------------------------------------------

  /** Floors and tables, with each busy table's running bill taken from its active order. */
  async getFloorLayout(branchId: string): Promise<Floor[]> {
    const [floors, active, billed] = await Promise.all([
      this.request<Raw[]>('GET', `/floor/branches/${branchId}/floors`),
      this.request<Page<Raw>>('GET', '/orders', { query: { branch_id: branchId, status: 'active', limit: 200 } }),
      this.request<Page<Raw>>('GET', '/orders', { query: { branch_id: branchId, status: 'billed', limit: 200 } }),
    ]);
    const orderByTable = new Map<string, Raw>();
    for (const order of [...active.items, ...billed.items]) {
      if (order.table_id) orderByTable.set(order.table_id, order);
    }

    return floors.map((floor) => ({
      id: floor.id,
      branch_id: floor.branch_id,
      name: floor.name,
      section: floor.name,
      sort_order: floor.sort_order,
      tables: (floor.tables ?? []).map((t: Raw): DiningTable => {
        const order = orderByTable.get(t.id);
        const liveItems = (order?.items ?? []).filter((i: Raw) => i.status !== 'cancelled');
        return {
          id: t.id,
          floor_id: t.floor_id,
          table_number: t.table_number,
          capacity: t.capacity,
          status: TABLE_STATUS[t.status] ?? TableStatus.AVAILABLE,
          section: floor.name,
          merged_into_table_id: t.merged_into_table_id ?? undefined,
          running_order_id: order?.id,
          running_bill_paise: order ? toPaise(order.total_amount) : undefined,
          occupied_at: order?.created_at,
          item_count: order ? liveItems.reduce((n: number, i: Raw) => n + i.quantity, 0) : undefined,
        };
      }),
    }));
  }

  // ---- Catalog --------------------------------------------------------------------

  async getCatalog(): Promise<Category[]> {
    const raw = await this.request<Raw>('GET', '/catalog/menu');
    return (raw.categories ?? []).map(adaptCategory);
  }

  async toggleItem86(itemId: string, isAvailable: boolean): Promise<MenuItem> {
    return adaptMenuItem(
      await this.request<Raw>('PATCH', `/catalog/items/${itemId}/toggle-86`, { body: { is_available_86: isAvailable } }),
    );
  }

  async createMenuItem(item: {
    category_id: string;
    name: string;
    base_price_paise: number;
    description?: string;
    station?: KitchenStation;
    tax_rate_percent?: number;
  }): Promise<MenuItem> {
    return adaptMenuItem(
      await this.request<Raw>('POST', '/catalog/items', {
        body: {
          category_id: item.category_id,
          name: item.name,
          description: item.description || undefined,
          base_price: toRupees(item.base_price_paise),
          tax_rate: (item.tax_rate_percent ?? 5).toFixed(2),
          station: stationToApi(item.station),
        },
      }),
    );
  }

  async updateMenuItem(itemId: string, changes: MenuItemUpdate): Promise<MenuItem> {
    const body: Raw = {};
    if (changes.name !== undefined) body.name = changes.name;
    if (changes.description !== undefined) body.description = changes.description;
    if (changes.base_price_paise !== undefined) body.base_price = toRupees(changes.base_price_paise);
    if (changes.tax_rate_percent !== undefined) body.tax_rate = changes.tax_rate_percent.toFixed(2);
    if (changes.station !== undefined) body.station = stationToApi(changes.station);
    if (changes.category_id !== undefined) body.category_id = changes.category_id;
    if (changes.is_active !== undefined) body.is_active = changes.is_active;
    return adaptMenuItem(await this.request<Raw>('PATCH', `/catalog/items/${itemId}`, { body }));
  }

  async createCategory(category: { name: string; sort_order?: number }): Promise<Category> {
    return adaptCategory(await this.request<Raw>('POST', '/catalog/categories', { body: category }));
  }

  // ---- Void approvals ------------------------------------------------------------

  async getVoidRequests(branchId: string, status?: 'pending' | 'approved' | 'rejected'): Promise<VoidApprovalItem[]> {
    const page = await this.request<Page<Raw>>('GET', '/approvals/void-requests', {
      query: { branch_id: branchId, status, limit: 100 },
    });
    return page.items.map(adaptVoidRequest);
  }

  /** Cancellation requests raised on one order, newest first, so floor staff can see the kitchen's answer. */
  async getOrderVoidRequests(orderId: string): Promise<VoidApprovalItem[]> {
    const rows = await this.request<Raw[]>('GET', `/orders/${orderId}/void-requests`);
    return rows.map(adaptVoidRequest);
  }

  async approveVoidRequest(requestId: string, note?: string): Promise<VoidApprovalItem> {
    return adaptVoidRequest(
      await this.request<Raw>('POST', `/approvals/void-requests/${requestId}/approve`, { body: { note } }),
    );
  }

  async rejectVoidRequest(requestId: string, note?: string): Promise<VoidApprovalItem> {
    return adaptVoidRequest(
      await this.request<Raw>('POST', `/approvals/void-requests/${requestId}/reject`, { body: { note } }),
    );
  }

  // ---- Staff & shifts ---------------------------------------------------------------

  async getStaff(branchId: string): Promise<StaffUser[]> {
    const page = await this.request<Page<Raw>>('GET', '/staff', { query: { branch_id: branchId, limit: 200 } });
    return page.items.map(adaptStaff);
  }

  async createStaff(staff: {
    full_name: string;
    username: string;
    role: StaffRole;
    pin: string;
    password?: string;
    email?: string;
    branch_id?: string;
  }): Promise<StaffUser> {
    return adaptStaff(
      await this.request<Raw>('POST', '/staff', {
        body: { ...staff, role: roleToApi(staff.role), password: staff.password || undefined, email: staff.email || undefined },
      }),
    );
  }

  async resetStaffPin(userId: string, pin: string): Promise<StaffUser> {
    return adaptStaff(await this.request<Raw>('POST', `/staff/${userId}/reset-pin`, { body: { pin } }));
  }

  async setStaffActive(userId: string, isActive: boolean): Promise<StaffUser> {
    return adaptStaff(await this.request<Raw>('PATCH', `/staff/${userId}`, { body: { is_active: isActive } }));
  }

  async getShiftHistory(branchId: string): Promise<ShiftClosureAudit[]> {
    const page = await this.request<Page<Raw>>('GET', '/staff/shifts', {
      query: { branch_id: branchId, status: 'closed', limit: 50 },
    });
    return page.items.map(adaptShift);
  }

  // ---- Orders & kitchen tickets (waiter) ------------------------------------------------

  async createOrder(input: {
    tableId?: string;
    orderType: 'dine_in' | 'takeaway';
    guestCount: number;
  }): Promise<OrderDetail> {
    return adaptOrder(
      await this.request<Raw>('POST', '/orders', {
        idempotent: true,
        body: {
          order_type: input.orderType,
          table_id: input.orderType === 'dine_in' ? input.tableId : undefined,
          guest_count: input.guestCount,
          items: [],
        },
      }),
    );
  }

  async getOrder(orderId: string): Promise<OrderDetail> {
    return adaptOrder(await this.request<Raw>('GET', `/orders/${orderId}`));
  }

  /** Active takeaway orders (no table) for the waiter's branch. */
  async getOpenTakeaways(branchId: string): Promise<OrderDetail[]> {
    const page = await this.request<Page<Raw>>('GET', '/orders', {
      query: { branch_id: branchId, status: 'active', limit: 100 },
    });
    return page.items.filter((o) => !o.table_id).map(adaptOrder);
  }

  // ---- Billing -----------------------------------------------------------------------

  /** The live (non-voided) bill of an order, if one has been generated. */
  async getBillForOrder(orderId: string): Promise<Bill | null> {
    const page = await this.request<Page<Raw>>('GET', '/billing/bills', { query: { order_id: orderId, limit: 10 } });
    const live = page.items.find((b) => b.status !== 'voided');
    return live ? adaptBill(live) : null;
  }

  /** Bill an order. Safe to retry with the same key. */
  async generateBill(orderId: string, idempotencyKey: string): Promise<Bill> {
    return adaptBill(
      await this.request<Raw>('POST', '/billing/bills', { idempotent: idempotencyKey, body: { order_id: orderId } }),
    );
  }

  /**
   * Record one tender. `idempotencyKey` must stay the same across retries of the same attempt so a
   * dropped connection can never take the money twice.
   */
  async recordPayment(billId: string, payment: PaymentInput, idempotencyKey: string): Promise<PaymentOutcome> {
    const raw = await this.request<Raw>('POST', `/billing/bills/${billId}/payments`, {
      idempotent: idempotencyKey,
      body: paymentBody(payment),
    });
    return { bill: adaptBill(raw.bill), change_due_paise: toPaise(raw.change_due) };
  }

  /** Open tickets (queued, preparing, ready) for the branch, oldest first. */
  async getKitchenBoard(branchId: string): Promise<BoardTicket[]> {
    const rows = await this.request<Raw[]>('GET', '/kots', { query: { branch_id: branchId } });
    return rows.map(adaptBoardTicket);
  }

  /** The kitchen starts and finishes tickets; floor staff mark a ready ticket served. */
  async advanceTicket(kotId: string, status: 'preparing' | 'ready' | 'served'): Promise<void> {
    await this.request<Raw>('PATCH', `/kots/${kotId}/status`, { body: { status } });
  }

  async getOrderTickets(orderId: string): Promise<KitchenTicket[]> {
    const rows = await this.request<Raw[]>('GET', '/kots', { query: { order_id: orderId } });
    return rows.map(adaptTicket);
  }

  /**
   * Add the cart to the order and fire every unsent line to its kitchen station.
   * Fires even when the cart is empty, so lines left unsent earlier also go out.
   */
  async sendToKitchen(orderId: string, version: number, cart: CartLine[]): Promise<KitchenTicket[]> {
    if (cart.length > 0) {
      await this.request<Raw>('POST', `/orders/${orderId}/items`, {
        body: { version, items: cart.map(cartToApi) },
      });
    }
    const rows = await this.request<Raw[]>('POST', '/kots/fire', { idempotent: true, body: { order_id: orderId } });
    return rows.map(adaptTicket);
  }

  async cancelOrderLine(orderId: string, lineId: string, reason: string): Promise<OrderDetail> {
    return adaptOrder(
      await this.request<Raw>('POST', `/orders/${orderId}/items/${lineId}/cancel`, { body: { reason } }),
    );
  }

  async requestVoid(kotId: string, reason: string): Promise<void> {
    await this.request<Raw>('POST', `/kots/${kotId}/void-requests`, { body: { reason } });
  }

  async transferTable(sourceTableId: string, targetTableId: string, reason?: string): Promise<void> {
    await this.request<Raw>('POST', '/floor/tables/transfer', {
      body: { source_table_id: sourceTableId, target_table_id: targetTableId, reason: reason || undefined },
    });
  }

  // ---- Analytics ----------------------------------------------------------------------

  async getLiveKPIs(branchId: string): Promise<LiveKPIs> {
    const [kpi, sales] = await Promise.all([
      this.request<Raw>('GET', '/analytics/kpis/live', { query: { branch_id: branchId } }),
      this.request<Raw>('GET', '/analytics/sales/today', { query: { branch_id: branchId } }),
    ]);
    return {
      branch_id: kpi.branch_id,
      active_tables_count: kpi.active_tables_count,
      vacant_tables_count: kpi.vacant_tables_count,
      billed_tables_count: 0,
      dirty_tables_count: 0,
      open_orders_count: kpi.open_orders_count,
      pending_kots_count: kpi.pending_kots_count,
      total_sales_today_paise: toPaise(kpi.total_sales_today),
      average_ticket_size_paise: toPaise(kpi.average_ticket_size),
      sales_velocity_paise_per_hour: 0,
      timestamp: kpi.timestamp,
      hourly_sales: (sales.hourly ?? []).map((h: Raw) => ({
        hour: h.hour,
        sales_paise: toPaise(h.sales),
        orders_count: h.bills,
      })),
    };
  }

  async getAuditLog(branchId: string): Promise<FraudAuditLog[]> {
    const page = await this.request<Page<Raw>>('GET', '/analytics/audit-log', {
      query: { branch_id: branchId, limit: 20 },
    });
    return page.items.map(adaptAudit);
  }

  async getZReport(branchId: string, date?: string): Promise<ZReport> {
    const [z, sales] = await Promise.all([
      this.request<Raw>('GET', '/analytics/reports/z-report', { query: { branch_id: branchId, report_date: date } }),
      this.request<Raw>('GET', '/analytics/sales/today', { query: { branch_id: branchId } }),
    ]);
    const net = toPaise(z.net_sales);
    const cgst = toPaise(z.total_tax_cgst);
    const sgst = toPaise(z.total_tax_sgst);
    const isToday = !date || date === sales.business_date;
    return {
      branch_id: z.branch_id,
      report_date: z.report_date,
      opening_time: '',
      closing_time: '',
      gross_sales_paise: toPaise(z.gross_sales),
      net_sales_paise: net,
      total_tax_cgst_paise: cgst,
      total_tax_sgst_paise: sgst,
      total_tax_paise: cgst + sgst,
      total_discounts_paise: toPaise(z.total_discounts),
      total_service_charges_paise: 0,
      round_off_paise: 0,
      payment_breakdown: {
        cash_paise: toPaise(z.cash_collected),
        card_paise: toPaise(z.card_collected),
        upi_paise: toPaise(z.upi_collected),
        total_paise: toPaise(z.cash_collected) + toPaise(z.card_collected) + toPaise(z.upi_collected),
      },
      voided_orders_count: z.voided_orders_count + z.voided_bills_count,
      voided_amount_paise: 0,
      total_bills_count: z.total_bills_count,
      // Same definition as the live dashboard: amount guests paid per bill (after discount, incl. GST).
      average_bill_value_paise: z.total_bills_count
        ? Math.round((toPaise(z.cash_collected) + toPaise(z.card_collected) + toPaise(z.upi_collected)) / z.total_bills_count)
        : 0,
      // Top items are only tracked for the current business day.
      top_selling_items: isToday ? adaptTopItems(sales.top_items) : [],
      generated_at: z.generated_at,
    };
  }

  /** Today's payment split, available before the day is closed. */
  async getTodayPayments(branchId: string): Promise<PaymentBreakdown> {
    const sales = await this.request<Raw>('GET', '/analytics/sales/today', { query: { branch_id: branchId } });
    return adaptPayments(sales.payments);
  }
}

// Expo inlines EXPO_PUBLIC_* variables at build time; declared here so the package needs no Node types.
declare const process: { env?: Record<string, string | undefined> } | undefined;

const defaultBaseUrl =
  (typeof process !== 'undefined' && process.env?.EXPO_PUBLIC_API_URL) || 'http://localhost:8000';

export const apiClient = new RestaurantApiClient({ baseUrl: defaultBaseUrl });
