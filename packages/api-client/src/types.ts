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
  order_id: string;
  table_number: string;
  requested_by_user_id: string;
  requested_by_user_name: string;
  item_name: string;
  quantity: number;
  amount_paise: number;
  wastage_reason: WastageReason | string;
  status: VoidApprovalStatus;
  requested_at: string;
  reviewed_at?: string;
  reviewed_by_user_id?: string;
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
}

export interface OrderDetail {
  id: string;
  order_number: string;
  order_type: 'dine_in' | 'takeaway' | 'delivery';
  status: 'active' | 'billed' | 'completed' | 'cancelled';
  table_id?: string;
  guest_count: number;
  version: number;
  subtotal_paise: number;
  created_at: string;
  lines: OrderLine[];
}

export interface KitchenTicket {
  id: string;
  kot_number: string;
  station: KitchenStation;
  status: 'queued' | 'preparing' | 'ready' | 'served' | 'voided';
  items: { order_item_id: string; name: string; quantity: number; notes?: string; status: string }[];
  created_at: string;
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
