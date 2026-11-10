import {
  TableStatus,
  StaffRole,
  KOTStatus,
  KitchenStation,
  PaymentMode,
  FloorSection,
  VoidApprovalStatus,
  ShiftStatus,
  WastageReason,
} from '@repo/shared-utils';

// ==========================================
// 1. FLOOR & TABLES
// ==========================================

export interface DiningTable {
  id: string;
  floor_id: string;
  table_number: string;
  capacity: number;
  status: TableStatus;
  section: string; // floor / area name
  running_order_id?: string;
  running_bill_paise?: number;
  occupied_at?: string; // ISO string
  active_captain_id?: string;
  active_captain_name?: string;
  item_count?: number;
  merged_into_table_id?: string;
}

export interface Floor {
  id: string;
  branch_id: string;
  name: string;
  section: string;
  sort_order: number;
  tables: DiningTable[];
}

export interface Branch {
  id: string;
  name: string;
  code: string;
  address?: string;
  phone?: string;
  is_active: boolean;
  floors: Floor[];
}

export interface TableTransferRequest {
  source_table_id: string;
  target_table_id: string;
  reason?: string;
}

export interface TableMergeRequest {
  primary_table_id: string;
  secondary_table_ids: string[];
}

// ==========================================
// 2. CATALOG & 86 CONTROLLER
// ==========================================

export interface ModifierItem {
  id: string;
  group_id: string;
  name: string;
  price_paise: number;
  is_available: boolean;
}

export interface ModifierGroup {
  id: string;
  name: string;
  min_selection: number;
  max_selection: number;
  is_forced: boolean;
  modifiers: ModifierItem[];
}

export interface ItemVariant {
  id: string;
  item_id: string;
  name: string; // e.g. "Half", "Full"
  price_paise: number;
  is_available_86: boolean;
}

/** The unit an item is priced in. Quantities may have 3 decimals for items sold by weight (0.75 kg). */
export type ItemUnit = 'pcs' | 'kg' | 'g' | 'l' | 'ml';

export interface MenuItem {
  id: string;
  category_id: string;
  name: string;
  description?: string;
  base_price_paise: number;
  tax_rate_percent: number;
  station: KitchenStation;
  is_active: boolean;
  is_available_86: boolean;
  image_url?: string;
  barcode?: string;
  sku?: string;
  hsn_code?: string;
  unit: ItemUnit;
  /** Loose items: the quantity is weighed or measured and may have decimals. */
  sold_by_weight: boolean;
  variants: ItemVariant[];
  modifier_group_ids?: string[];
  modifier_groups?: ModifierGroup[];
  created_at: string;
  updated_at: string;
}

export interface Category {
  id: string;
  name: string;
  description?: string;
  sort_order: number;
  station_default: KitchenStation;
  tax_rate_percent: number;
  is_active: boolean;
  items: MenuItem[];
}

export interface Toggle86Request {
  item_id: string;
  is_available_86: boolean;
  reason?: string;
}

// ==========================================
// 3. KOTS & VOID APPROVALS
// ==========================================

export interface KOTItem {
  id: string;
  kot_id: string;
  order_item_id: string;
  item_name: string;
  variant_name?: string;
  quantity: number;
  notes?: string;
  status: KOTStatus;
  void_reason?: string;
}

export interface KOT {
  id: string;
  order_id: string;
  table_number: string;
  kot_number: string;
  station: KitchenStation;
  status: KOTStatus;
  items: KOTItem[];
  created_at: string;
  updated_at: string;
  void_reason?: string;
}

export interface VoidApprovalItem {
  id: string;
  kot_id: string;
  kot_number: string;
  station: KitchenStation;
  /** Where the ticket is in the kitchen now: a 'ready' ticket is already cooked. */
  kot_status: 'queued' | 'preparing' | 'ready' | 'served' | 'voided';
  order_id: string;
  table_number: string;
  requested_by_user_id: string;
  requested_by_user_name: string;
  item_name: string;
  quantity: number;
  items: { name: string; quantity: number }[];
  amount_paise: number;
  wastage_reason: WastageReason | string;
  status: VoidApprovalStatus;
  requested_at: string;
  reviewed_at?: string;
  reviewed_by_user_id?: string;
  /** Why the kitchen declined (or any note left on approval). */
  review_note?: string;
}

// ==========================================
// 4. PRINTER SETUP
// ==========================================

export interface PrinterConfig {
  id: string;
  station: KitchenStation;
  station_name: string;
  primary_printer_ip: string;
  primary_printer_port: number;
  fallback_printer_ip: string;
  fallback_printer_port: number;
  is_active: boolean;
  auto_failover_enabled: boolean;
  status: 'ONLINE' | 'FAILOVER_ACTIVE' | 'OFFLINE';
  last_ping_ms?: number;
}

// ==========================================
// 5. TAX & DISCOUNT CONFIGURATION
// ==========================================

export interface TaxRuleConfig {
  id: string;
  name: string;
  is_inclusive: boolean;
  cgst_percent: number;
  sgst_percent: number;
  total_gst_percent: number;
  service_charge_percent: number;
  is_service_charge_enabled: boolean;
  is_default: boolean;
}

export interface DiscountRuleConfig {
  id: string;
  code: string;
  title: string;
  type: 'FLAT' | 'PERCENTAGE';
  value: number; // paise or percent
  max_cap_paise?: number;
  min_order_paise?: number;
  requires_manager_pin: boolean;
  mandatory_audit_reason: boolean;
  is_active: boolean;
}

// ==========================================
// 6. STAFF, AUTH & SHIFTS
// ==========================================

export interface StaffUser {
  id: string;
  username: string;
  email?: string;
  full_name: string;
  role: StaffRole;
  branch_id?: string;
  pos_pin?: string; // never returned by the API
  supervisor_pin?: string; // legacy, unused
  is_active: boolean;
  phone?: string;
  last_login?: string;
}

/** One person on the pre-login 'who is working' screen: no PIN, username or contact details. */
export interface RosterEntry {
  id: string;
  full_name: string;
  role: StaffRole;
  /** 4, 5 or 6: how many digits this person's PIN has. Unknown for PINs set before it was recorded. */
  pin_length?: number;
}

/** A one-time code a manager shows so a new tablet can be linked to the branch. */
export interface DeviceSetupCode {
  code: string;
  branch_id: string;
  branch_name: string;
  expires_in: number;
}

/** What linking hands back; kept on the tablet to unlock the roster. */
export interface DeviceLink {
  device_token: string;
  branch_id: string;
  branch_name: string;
}

export interface ShiftClosureAudit {
  id: string;
  user_id: string;
  cashier_name: string;
  branch_id: string;
  start_time: string;
  end_time?: string;
  opening_float_paise: number;
  cash_sales_paise: number;
  card_sales_paise: number;
  upi_sales_paise: number;
  expected_cash_paise: number;
  blind_drop_cash_paise: number;
  variance_paise: number; // blind_drop - expected
  status: ShiftStatus;
  notes?: string;
}

export interface WaiterPerformanceMetric {
  id: string;
  staff_id: string;
  name: string;
  tables_served: number;
  total_sales_paise: number;
  average_turnover_minutes: number;
  customer_rating: number;
}

// ==========================================
// 7. RECIPE BOM & INVENTORY
// ==========================================

export interface BOMIngredient {
  ingredient_id: string;
  ingredient_name: string;
  quantity_required: number;
  unit: 'g' | 'kg' | 'ml' | 'L' | 'pcs';
  cost_paise: number;
}

export interface RecipeBOM {
  id: string;
  menu_item_id: string;
  item_name: string;
  ingredients: BOMIngredient[];
  total_recipe_cost_paise: number;
  portion_size: string;
}

export interface InventoryStockItem {
  id: string;
  name: string;
  current_quantity: number;
  unit: 'g' | 'kg' | 'ml' | 'L' | 'pcs';
  minimum_threshold: number;
  is_low_stock: boolean;
  cost_per_unit_paise: number;
  last_restocked_at: string;
}

// ==========================================
// 8. LIVE PULSE & Z-REPORTS
// ==========================================

export interface LiveKPIs {
  branch_id: string;
  active_tables_count: number;
  vacant_tables_count: number;
  billed_tables_count: number;
  dirty_tables_count: number;
  open_orders_count: number;
  pending_kots_count: number;
  total_sales_today_paise: number;
  average_ticket_size_paise: number;
  sales_velocity_paise_per_hour: number;
  timestamp: string;
  hourly_sales: Array<{ hour: string; sales_paise: number; orders_count: number }>;
}

export interface PaymentBreakdown {
  cash_paise: number;
  card_paise: number;
  upi_paise: number;
  total_paise: number;
}

export interface TopSellingItem {
  item_id: string;
  name: string;
  quantity: number;
  total_sales_paise: number;
  category_name: string;
}

export interface ZReport {
  branch_id: string;
  report_date: string;
  opening_time: string;
  closing_time: string;
  gross_sales_paise: number;
  net_sales_paise: number;
  total_tax_cgst_paise: number;
  total_tax_sgst_paise: number;
  total_tax_paise: number;
  total_discounts_paise: number;
  total_service_charges_paise: number;
  round_off_paise: number;
  payment_breakdown: PaymentBreakdown;
  voided_orders_count: number;
  voided_amount_paise: number;
  total_bills_count: number;
  average_bill_value_paise: number;
  top_selling_items: TopSellingItem[];
  generated_at: string;
}

export interface FraudAuditLog {
  id: string;
  action_type: 'kot_void' | 'bill_void' | 'high_discount' | 'table_transfer' | 'manual_tax_override' | 'price_override';
  actor_id: string;
  actor_name: string;
  details: string;
  amount_paise?: number;
  risk_level: 'LOW' | 'MEDIUM' | 'HIGH';
  timestamp: string;
}

// ==========================================
// AUTH & BRANCHES
// ==========================================

export interface AuthSession {
  access_token: string;
  expires_at: number; // epoch ms
  user: StaffUser;
}

export interface BranchSummary {
  id: string;
  name: string;
  code: string;
  is_active: boolean;
}

export interface MenuItemUpdate {
  name?: string;
  description?: string;
  base_price_paise?: number;
  tax_rate_percent?: number;
  station?: KitchenStation;
  category_id?: string;
  is_active?: boolean;
  /** null clears the code. */
  barcode?: string | null;
  sku?: string | null;
  hsn_code?: string | null;
  unit?: ItemUnit;
  sold_by_weight?: boolean;
}

/** Fields of a new item; the shop fields are optional. */
export interface MenuItemCreate {
  category_id: string;
  name: string;
  base_price_paise: number;
  description?: string;
  station?: KitchenStation;
  tax_rate_percent?: number;
  barcode?: string;
  sku?: string;
  hsn_code?: string;
  unit?: ItemUnit;
  sold_by_weight?: boolean;
}

export interface ItemImportResult {
  created: number;
  updated: number;
  categories_created: number;
  dry_run: boolean;
  /** Rows that were not imported: the file's line number and the reasons. */
  errors: { row: number; errors: string[]; data: Record<string, string> }[];
}

// ==========================================
// WAITER / ORDERING
// ==========================================

export type OrderLineStatus = 'ordered' | 'fired' | 'preparing' | 'ready' | 'served' | 'cancelled';

export interface OrderLine {
  id: string;
  item_id: string;
  variant_id?: string;
  name: string;
  variant_name?: string;
  modifiers: string[];
  quantity: number;
  unit_price_paise: number;
  line_total_paise: number;
  status: OrderLineStatus;
  notes?: string;
  station: KitchenStation;
  unit: ItemUnit;
}

export type OrderType = 'dine_in' | 'takeaway' | 'delivery' | 'counter_sale';

export interface OrderDetail {
  id: string;
  order_number: string;
  order_type: OrderType;
  /** Daily number called out when a counter order is ready (businesses with token numbers). */
  token_number?: number;
  status: 'active' | 'billed' | 'completed' | 'cancelled';
  table_id?: string;
  guest_count: number;
  version: number;
  subtotal_paise: number;
  created_at: string;
  lines: OrderLine[];
}

/** A ticket on the kitchen board: what to cook, for which table, and how long it has waited. */
export interface BoardTicket {
  id: string;
  kot_number: string;
  station: KitchenStation;
  status: 'queued' | 'preparing' | 'ready' | 'served' | 'voided';
  fulfilment: 'screen' | 'slip';
  print_count: number;
  /** Table number, or 'Takeaway'. */
  table_label: string;
  order_id: string;
  order_number: string;
  /** Who took the order (their phone is told when it is ready). */
  waiter_id?: string;
  items: { name: string; quantity: number; notes?: string }[];
  created_at: string;
}

export interface KitchenTicket {
  id: string;
  kot_number: string;
  station: KitchenStation;
  status: 'queued' | 'preparing' | 'ready' | 'served' | 'voided';
  /** 'screen' tickets go to the kitchen display; 'slip' tickets go on paper and are marked served by the floor. */
  fulfilment: 'screen' | 'slip';
  /** Confirmed prints of the slip; 0 means the paper has not come out yet. */
  print_count: number;
  /** Table number or 'Takeaway', and the order number (filled in by listings such as the print queue). */
  table_label?: string;
  order_number?: string;
  /** A cancelled paper KOT: whether its VOID notice has printed for the kitchen. */
  void_printed?: boolean;
  items: { order_item_id: string; name: string; quantity: number; notes?: string; status: string }[];
  created_at: string;
}

export type NoticeKind =
  | 'food_ready'
  | 'kot_cancelled'
  | 'kot_printed'
  | 'cancel_approved'
  | 'cancel_declined'
  | 'bill_paid'
  | 'sold_out';

/** Something floor staff should know about now (see GET /notices). */
export interface Notice {
  /** Stable: the same event always has the same id. */
  id: string;
  kind: NoticeKind;
  title: string;
  detail?: string;
  order_id?: string;
  at: string;
}

export type OverviewPeriod = 'today' | 'yesterday' | '7d' | '30d' | '90d';

export interface OverviewPoint {
  /** An ISO date, "13:00", or a weekday ("Mon"). */
  label: string;
  sales_paise: number;
  bills: number;
}

export interface OverviewSlice {
  name: string;
  amount_paise: number;
  /** Bills, or item lines for categories. */
  count: number;
}

export interface OverviewTotals {
  net_sales_paise: number;
  gross_sales_paise: number;
  discounts_paise: number;
  tax_paise: number;
  bills: number;
  average_bill_paise: number;
  guests: number;
  cancellations: number;
  cancelled_paise: number;
}

/** The business over a period, against the same-length period before it. */
export interface BusinessOverview {
  period: OverviewPeriod;
  start: string;
  end: string;
  branches: number;
  totals: OverviewTotals;
  previous: OverviewTotals;
  /** Percent change per headline figure; null when the period before had nothing. */
  changes: { net_sales: number | null; bills: number | null; average_bill: number | null };
  /** By hour for a single day, else by day. */
  trend: OverviewPoint[];
  by_hour: OverviewPoint[];
  by_weekday: OverviewPoint[];
  categories: OverviewSlice[];
  top_items: { name: string; category?: string; quantity: number; total_paise: number }[];
  staff: OverviewSlice[];
  order_types: OverviewSlice[];
  payments: PaymentBreakdown;
}

/** How the business appears on its bills: set by the business itself (owners and managers). */
export interface BusinessProfile {
  /** What the bill prints: the business's own name, or the registered name when it has not set one. */
  name: string;
  display_name?: string;
  registered_name: string;
  /** data:image/png;base64,... */
  logo_data_url?: string;
}

export type BusinessCategory = 'restaurant' | 'cafe' | 'grocery' | 'retail';

/** Workflow steps a business uses. Its category presets them; the platform can override each one per business. */
export interface BusinessSettings {
  /** Floor, tables, guests and waiters' phones. */
  table_service: boolean;
  /** Sales make KOTs for the kitchen (on a screen with the `kds` module, else printed). */
  kitchen_tickets: boolean;
  /** The counter opens on the quick-sale screen. */
  quick_sale: boolean;
  barcode: boolean;
  weighed_items: boolean;
  /** Counter sales get a daily token number. */
  tokens: boolean;
}

export const RESTAURANT_SETTINGS: BusinessSettings = {
  table_service: true, kitchen_tickets: true, quick_sale: false, barcode: false, weighed_items: false, tokens: false,
};

/** The parts of the subscription the apps act on. */
export interface Entitlements {
  access: 'full' | 'read_only' | 'blocked';
  plan_code?: string;
  /** Module keys, e.g. 'pos', 'kds' (kitchen display), 'analytics'. */
  modules: string[];
  category: BusinessCategory;
  settings: BusinessSettings;
}

/** An item the waiter has added but not yet sent to the kitchen. */
export interface CartLine {
  key: string;
  item_id: string;
  name: string;
  variant_id?: string;
  variant_name?: string;
  modifier_ids: string[];
  modifier_names: string[];
  unit_price_paise: number;
  quantity: number;
  notes?: string;
}

// ==========================================
// BILLING
// ==========================================

export interface BillPayment {
  id: string;
  payment_mode: PaymentMode;
  amount_paise: number;
  reference_id?: string;
  /** Who captured it: 'manual' (cashier keyed it in), or a card terminal such as 'pinelabs'. */
  provider: string;
  /** pending: a terminal is charging it (not counted yet); reversed: voided on the terminal the same day. */
  status: 'pending' | 'success' | 'failed' | 'reversed';
  /** Terminal payments: the reference given to the terminal (at most 10 characters). */
  billing_ref_no?: string;
  /** Terminal payments: the terminal's (masked) transaction details, e.g. ApprovalCode, InvoiceNumber, CardType. */
  terminal?: Record<string, unknown>;
  created_at: string;
}

export interface Bill {
  id: string;
  order_id: string;
  bill_number: string;
  status: 'unpaid' | 'partially_paid' | 'paid' | 'voided' | 'refunded';
  subtotal_paise: number;
  cgst_paise: number;
  sgst_paise: number;
  discount_paise: number;
  grand_total_paise: number;
  paid_paise: number;
  balance_due_paise: number;
  settled_at?: string;
  payments: BillPayment[];
  created_at: string;
}

/** One printed line, already wrapped to the paper: 'big' is double width (so at most half a line of characters). */
export interface PrintRow {
  style: 'normal' | 'bold' | 'big';
  align: 'left' | 'center';
  text: string;
}

/** Something to print on a line printer: a KOT or a customer bill, rendered by the server for the paper width. */
export interface PrintablePage {
  /** A short reference for the printer's log (KOT or bill number). */
  reference: string;
  paper_mm: 58 | 80;
  rows: PrintRow[];
  text: string;
}

export interface BillDiscount {
  amount_paise: number;
  reason: string;
  /** A manager's PIN when the discount is above the approval threshold. */
  approver_pin?: string;
}

/** The terminal's answer for a pending card/UPI payment. */
export interface TerminalOutcome {
  outcome: 'approved' | 'declined';
  approval_code?: string;
  message?: string;
  /** 'manual' when the cashier resolves an unconfirmed payment from the charge slip. */
  source?: 'terminal' | 'manual';
  details?: Record<string, string | number | boolean | null>;
}

export interface CashShift {
  id: string;
  start_time: string;
  opening_cash_paise: number;
  status: string;
  /** Shown to managers only. */
  expected_cash_paise?: number;
  variance_paise?: number;
}

/**
 * The branch's cash day: opened at the counter with the cash in the drawer, closed with a blind count. One per branch, whoever
 * is at the counter. Expected cash and the difference are only sent to managers and owners.
 */
export interface CashDay {
  status: 'not_open' | 'open' | 'closed';
  business_date: string;
  id?: string;
  opened_at?: string;
  opened_by?: string;
  opening_cash_paise?: number;
  closed_at?: string;
  counted_cash_paise?: number;
  expected_cash_paise?: number;
  variance_paise?: number;
  /** Still open from an earlier day: it was not closed. */
  stale: boolean;
}

/** A tender the cashier is recording. UPI and card need the UTR / approval code as reference. */
export interface PaymentInput {
  payment_mode: PaymentMode;
  amount_paise: number;
  reference_id?: string;
}

export interface PaymentOutcome {
  bill: Bill;
  /** Cash handed over beyond the balance, to be returned to the guest. */
  change_due_paise: number;
}
