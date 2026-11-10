/**
 * Platform API client.
 *
 * - The access token lives in memory only (never localStorage), the refresh token in an HttpOnly cookie.
 * - Refreshing is single-flight and cross-tab (Web Locks): the server treats a replayed refresh token as theft and
 *   ends the session, so two tabs must never refresh with the same cookie at the same time.
 * - A 403 `step_up_required` asks the user to re-authenticate, then retries the call once.
 */

const BASE = '/api/v1/platform';

// ---------------------------------------------------------------------------------------------------- types

export type Role = 'super_admin' | 'billing' | 'support' | 'read_only';
export type BillingTerm = 'monthly' | 'yearly';
export type Effective = 'now' | 'next_renewal';

export interface Admin {
  id: string;
  email: string;
  full_name: string;
  role: Role;
  is_active: boolean;
  last_login_at: string | null;
  created_at: string;
}

export interface Subscription {
  id: string;
  plan_id: string;
  plan_code: string | null;
  billing_term: BillingTerm;
  status: string;
  effective_status: string | null;
  price_amount: string;
  currency: string;
  started_at: string;
  current_period_start: string;
  current_period_end: string | null;
  trial_ends_at: string | null;
  grace_until: string | null;
  auto_renew: boolean;
  cancel_at_period_end: boolean;
  pending_billing_term: BillingTerm | null;
  pending_price_amount: string | null;
  credit_balance: string;
  max_branches_override: number | null;
  max_staff_override: number | null;
  version: number;
}

export interface ModuleState {
  key: string;
  name: string;
  is_core: boolean;
  enabled: boolean;
  source: 'core' | 'plan' | 'override' | 'platform_disabled' | 'off' | 'dependency_missing';
  override: { enabled: boolean; reason: string | null; expires_at: string | null; active: boolean } | null;
}

export interface OrgEvent {
  type: string;
  at: string;
  by: string | null;
  data: Record<string, unknown>;
}

/** Workflow steps a business uses; its category presets them and each can be overridden per business. */
export type SettingKey = 'table_service' | 'kitchen_tickets' | 'quick_sale' | 'barcode' | 'weighed_items' | 'tokens';
export type BusinessSettings = Record<SettingKey, boolean>;

export interface BusinessCategories {
  categories: { key: string; name: string; settings: BusinessSettings }[];
  setting_labels: Record<SettingKey, string>;
}

export interface OrgDetail {
  id: string;
  name: string;
  slug: string;
  legal_name: string | null;
  billing_email: string | null;
  country: string | null;
  status: string;
  category: string;
  /** Effective: the category's preset with this business's overrides applied. */
  settings: BusinessSettings;
  settings_overrides: Partial<BusinessSettings>;
  suspended_at: string | null;
  suspension_reason: string | null;
  created_at: string;
  subscription: Subscription | null;
  access: 'full' | 'read_only' | 'blocked';
  modules: ModuleState[];
  branches: number;
  active_staff: number;
  recent_events: OrgEvent[];
}

export interface OrgSummary {
  id: string;
  name: string;
  slug: string;
  status: string;
  category: string;
  billing_email: string | null;
  created_at: string;
  plan_code: string | null;
  billing_term: BillingTerm | null;
  subscription_status: string | null;
  current_period_end: string | null;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface Plan {
  id: string;
  code: string;
  name: string;
  is_active: boolean;
  max_branches: number | null;
  max_staff: number | null;
  currency: string;
  price_monthly: string | null;
  price_yearly: string | null;
  module_keys: string[];
  subscribers: number;
}

export interface GlobalModule {
  key: string;
  name: string;
  description: string;
  is_core: boolean;
  depends_on: string[];
  globally_enabled: boolean;
  reason: string | null;
  organizations_with_access: number;
}

export interface ChangePreview {
  effective: Effective;
  effective_at: string;
  current_term: BillingTerm;
  new_term: BillingTerm;
  current_price: string;
  new_price: string;
  credit_for_unused_time: string;
  amount_due_now: string;
  account_credit_remaining: string;
  new_period_start: string;
  new_period_end: string;
}

export interface Dashboard {
  organizations_by_status: Record<string, number>;
  subscriptions_by_status: Record<string, number>;
  subscriptions_by_term: Record<string, number>;
  mrr: Record<string, string>;
  arr: Record<string, string>;
  renewals_next_30_days: Array<Record<string, string | boolean>>;
  trials_ending_next_7_days: Array<Record<string, string>>;
  past_due: number;
  outstanding: Record<string, string>;
  open_invoices: number;
  overdue_invoices: number;
}

export interface Approval {
  id: string;
  action: string;
  summary: string;
  payload: Record<string, unknown>;
  reason: string;
  status: 'pending' | 'executed' | 'rejected' | 'cancelled' | 'expired' | 'failed';
  requested_by: string;
  requested_at: string;
  expires_at: string;
  decided_by: string | null;
  decided_at: string | null;
  decision_note: string | null;
  result: Record<string, unknown> | null;
}

export interface Alert {
  id: string;
  kind: string;
  severity: 'info' | 'warning' | 'critical';
  message: string;
  data: Record<string, unknown>;
  created_at: string;
  notified_at: string | null;
  acknowledged_at: string | null;
  acknowledged_by: string | null;
}

/** Dangerous actions answer 202 with an approval instead of running when a second super admin must approve first. */
export function isApproval(value: unknown): value is Approval {
  return typeof value === 'object' && value !== null && 'action' in value && 'requested_by' in value && 'expires_at' in value;
}

export type PaymentMethod = 'bank_transfer' | 'upi' | 'card_offline' | 'cash' | 'cheque' | 'other';

export interface Payment {
  id: string;
  amount: string;
  currency: string;
  method: PaymentMethod;
  reference: string | null;
  received_at: string;
  note: string | null;
}

export interface Invoice {
  id: string;
  number: string;
  organization_id: string;
  organization_name: string | null;
  kind: 'renewal' | 'activation' | 'plan_change' | 'manual';
  status: 'open' | 'paid' | 'void';
  billing_term: BillingTerm;
  period_start: string;
  period_end: string;
  currency: string;
  amount: string;
  credit_applied: string;
  amount_due: string;
  amount_paid: string;
  balance: string;
  overdue: boolean;
  description: string | null;
  issued_at: string;
  due_at: string;
  paid_at: string | null;
  voided_at: string | null;
  void_reason: string | null;
  payments: Payment[];
}

export interface PaymentResult {
  invoice: Invoice;
  payment: Payment;
  settled: boolean;
  renewed: boolean;
  credit_added: string;
}

export interface AuditEntry {
  seq: number;
  created_at: string;
  admin_id: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  reason: string | null;
  ip: string | null;
  request_id: string | null;
  hash: string;
}

export interface SessionInfo {
  id: string;
  admin_id: string;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  ip: string | null;
  user_agent: string | null;
  current: boolean;
}

export interface BulkResult {
  dry_run: boolean;
  changed: Array<{ organization_id: string; before: boolean; after: boolean }>;
  unchanged: number;
  blocked: Array<Record<string, unknown>>;
}

// ---------------------------------------------------------------------------------------------------- errors

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

export async function toApiError(res: Response): Promise<ApiError> {
  let body: any = null;
  try {
    body = await res.json();
  } catch {
    /* not JSON */
  }
  if (body?.error) return new ApiError(res.status, body.error.code, body.error.message, body.error.details ?? {});
  if (Array.isArray(body?.detail)) {
    const first = body.detail[0];
    const where = Array.isArray(first?.loc) ? first.loc.filter((p: unknown) => p !== 'body').join('.') : '';
    return new ApiError(res.status, 'validation_failed', `${where ? where + ': ' : ''}${first?.msg ?? 'Invalid request'}`);
  }
  return new ApiError(res.status, 'http_error', res.statusText || `Request failed (${res.status})`);
}

// ---------------------------------------------------------------------------------------------------- session

type Listener = () => void;
let accessToken: string | null = null;
let currentAdmin: Admin | null = null;
let refreshTimer: ReturnType<typeof setTimeout> | undefined;
let inflightRefresh: Promise<boolean> | null = null;
let stepUpHandler: (() => Promise<void>) | null = null;
const listeners = new Set<Listener>();

export const session = {
  get admin() {
    return currentAdmin;
  },
  subscribe(fn: Listener) {
    listeners.add(fn);
    return () => listeners.delete(fn);
  },
  setStepUpHandler(fn: (() => Promise<void>) | null) {
    stepUpHandler = fn;
  },
};

function notify() {
  listeners.forEach((fn) => fn());
}

export function readCookie(name: string, cookieString: string = document.cookie): string {
  const match = cookieString.split('; ').find((c) => c.startsWith(name + '='));
  return match ? decodeURIComponent(match.slice(name.length + 1)) : '';
}

interface TokenResponse {
  access_token: string;
  expires_in: number;
  csrf_token: string;
  admin: Admin;
}

function adopt(t: TokenResponse) {
  accessToken = t.access_token;
  currentAdmin = t.admin;
  clearTimeout(refreshTimer);
  // Renew well before the access token expires; the refresh cookie rotates each time.
  refreshTimer = setTimeout(() => void refreshSession(), Math.max(t.expires_in * 800, 5000));
  notify();
}

function endSession() {
  accessToken = null;
  currentAdmin = null;
  clearTimeout(refreshTimer);
  notify();
}

/** Rotate the refresh cookie and get a new access token. Returns false when there is no valid session. */
export function refreshSession(): Promise<boolean> {
  if (inflightRefresh) return inflightRefresh;
  const run = async (): Promise<boolean> => {
    const csrf = readCookie('pa_csrf');
    if (!csrf) return false;
    try {
      const res = await fetch(`${BASE}/auth/refresh`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'X-CSRF-Token': csrf },
      });
      if (!res.ok) return false;
      adopt((await res.json()) as TokenResponse);
      return true;
    } catch {
      return false;
    }
  };
  // Web Locks makes tabs take turns, so a second tab always sends the cookie the first tab just received.
  const locked = typeof navigator !== 'undefined' && navigator.locks
    ? navigator.locks.request('platform-console-refresh', run)
    : run();
  inflightRefresh = locked.then((ok) => {
    inflightRefresh = null;
    if (!ok) endSession();
    return ok;
  });
  return inflightRefresh;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw await toApiError(res);
  return (await res.json()) as T;
}

export const auth = {
  /** Step 1. Returns the challenge to complete with a code. */
  login: (email: string, password: string) => postJson<{ mfa_token: string; expires_in: number }>('/auth/login', { email, password }),
  /** Step 2. Provide either `code` (authenticator) or `recovery_code`. */
  async verify(mfaToken: string, factor: { code: string } | { recovery_code: string }) {
    adopt(await postJson<TokenResponse>('/auth/2fa', { mfa_token: mfaToken, ...factor }));
  },
  async logout() {
    const csrf = readCookie('pa_csrf');
    try {
      await fetch(`${BASE}/auth/logout`, { method: 'POST', credentials: 'same-origin', headers: { 'X-CSRF-Token': csrf } });
    } finally {
      endSession();
    }
  },
  async stepUp(password: string, code: string) {
    await request<void>('POST', '/auth/step-up', { body: { password, code }, noStepUp: true });
  },
  /** On page load: resume the session from the refresh cookie, if there is one. */
  resume: () => refreshSession(),
};

// ---------------------------------------------------------------------------------------------------- requests

interface Options {
  body?: unknown;
  query?: Record<string, string | number | boolean | undefined | null>;
  idempotencyKey?: string;
  noStepUp?: boolean;
}

export const newKey = () => crypto.randomUUID();

export async function request<T>(method: string, path: string, opts: Options = {}, retried = { auth: false, stepUp: false }): Promise<T> {
  const url = new URL(`${BASE}${path}`, window.location.origin);
  for (const [k, v] of Object.entries(opts.query ?? {})) if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, String(v));
  const headers: Record<string, string> = {};
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
  if (opts.idempotencyKey) headers['X-Idempotency-Key'] = opts.idempotencyKey;

  const res = await fetch(url, {
    method,
    headers,
    credentials: 'same-origin',
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  if (res.ok) return res.status === 204 ? (undefined as T) : ((await res.json()) as T);

  const err = await toApiError(res);
  if (res.status === 401 && !retried.auth && path !== '/auth/step-up') {
    if (await refreshSession()) return request<T>(method, path, opts, { ...retried, auth: true });
    throw err;
  }
  if (err.code === 'step_up_required' && !retried.stepUp && !opts.noStepUp && stepUpHandler) {
    await stepUpHandler(); // rejects if the user cancels
    return request<T>(method, path, opts, { ...retried, stepUp: true });
  }
  throw err;
}

const get = <T>(path: string, query?: Options['query']) => request<T>('GET', path, { query });

export const api = {
  me: () => get<Admin>('/me'),
  dashboard: () => get<Dashboard>('/dashboard'),

  organizations: (query: Record<string, string | number | undefined>) => get<Page<OrgSummary>>('/organizations', query),
  organization: (id: string) => get<OrgDetail>(`/organizations/${id}`),
  businessCategories: () => get<BusinessCategories>('/business-categories'),
  createOrganization: (body: unknown, key: string) =>
    request<{ organization: OrgDetail; amount_due: string | null; invoice_number: string | null }>('POST', '/organizations', { body, idempotencyKey: key }),
  updateOrganization: (id: string, body: unknown) => request<OrgDetail>('PATCH', `/organizations/${id}`, { body }),
  suspend: (id: string, reason: string) => request<OrgDetail>('POST', `/organizations/${id}/suspend`, { body: { reason } }),
  reinstate: (id: string, reason: string) => request<OrgDetail>('POST', `/organizations/${id}/reinstate`, { body: { reason } }),
  cancel: (id: string, reason: string, atPeriodEnd: boolean) =>
    request<OrgDetail | Approval>('POST', `/organizations/${id}/cancel`, { body: { reason, at_period_end: atPeriodEnd } }),
  activate: (id: string, reason: string, key: string) =>
    request<{ amount_due: string; currency: string; invoice_number: string; credit_applied: string }>('POST', `/organizations/${id}/activate`, { body: { reason }, idempotencyKey: key }),
  previewChange: (id: string, body: unknown) => request<ChangePreview>('POST', `/organizations/${id}/subscription/preview-change`, { body }),
  updateSubscription: (id: string, body: unknown, key: string) =>
    request<{ subscription: Subscription; change: ChangePreview | null; invoice_number: string | null }>('PUT', `/organizations/${id}/subscription`, { body, idempotencyKey: key }),
  extend: (id: string, body: unknown, key: string) =>
    request<Subscription>('POST', `/organizations/${id}/subscription/extend`, { body, idempotencyKey: key }),
  setModule: (id: string, key: string, body: unknown) => request<OrgDetail>('PUT', `/organizations/${id}/modules/${key}`, { body }),
  clearModule: (id: string, key: string, reason: string) =>
    request<OrgDetail>('DELETE', `/organizations/${id}/modules/${key}`, { query: { reason } }),
  impersonate: (id: string, body: unknown) =>
    request<{ access_token: string; expires_in: number; staff_id: string; username: string }>('POST', `/organizations/${id}/impersonate`, { body }),

  plans: () => get<Plan[]>('/plans'),
  createPlan: (body: unknown) => request<Plan>('POST', '/plans', { body }),
  updatePlan: (id: string, body: unknown) => request<Plan>('PATCH', `/plans/${id}`, { body }),

  modules: () => get<GlobalModule[]>('/modules'),
  switchModule: (key: string, body: unknown) => request<Approval | undefined>('PATCH', `/modules/${key}`, { body }),
  bulkModules: (body: unknown) => request<BulkResult | Approval>('POST', '/modules/bulk', { body }),

  approvals: (status?: string) => get<Approval[]>('/approvals', { status }),
  approve: (id: string, note?: string) => request<Approval>('POST', `/approvals/${id}/approve`, { body: { note: note || null } }),
  rejectApproval: (id: string, note: string) => request<Approval>('POST', `/approvals/${id}/reject`, { body: { note } }),
  cancelApproval: (id: string) => request<Approval>('POST', `/approvals/${id}/cancel`),
  alerts: (query: Record<string, string | number | boolean | undefined>) => get<Alert[]>('/alerts', query as Record<string, string | number | undefined>),
  alertSummary: () => get<{ open_alerts: Record<string, number>; pending_approvals: number }>('/alerts/summary'),
  acknowledgeAlert: (id: string) => request<Alert>('POST', `/alerts/${id}/acknowledge`),

  invoices: (query: Record<string, string | number | undefined>) => get<Page<Invoice>>('/invoices', query),
  orgInvoices: (orgId: string) => get<Invoice[]>(`/organizations/${orgId}/invoices`),
  issueRenewal: (orgId: string) => request<Invoice>('POST', `/organizations/${orgId}/invoices/renewal`),
  recordPayment: (invoiceId: string, body: unknown, key: string) =>
    request<PaymentResult>('POST', `/invoices/${invoiceId}/payments`, { body, idempotencyKey: key }),
  voidInvoice: (invoiceId: string, reason: string) => request<Invoice>('POST', `/invoices/${invoiceId}/void`, { body: { reason } }),
  runBilling: () => request<{ result: Record<string, number> }>('POST', '/billing/run'),

  audit: (query: Record<string, string | number | undefined>) => get<AuditEntry[]>('/audit-logs', query),
  verifyAudit: () => get<{ ok: boolean; checked: number; head_hash: string; first_bad_seq: number | null }>('/audit-logs/verify'),

  admins: () => get<Admin[]>('/admins'),
  createAdmin: (body: unknown) =>
    request<{ admin: Admin; totp_secret: string; provisioning_uri: string; recovery_codes: string[] }>('POST', '/admins', { body }),
  updateAdmin: (id: string, body: unknown) => request<Admin>('PATCH', `/admins/${id}`, { body }),
  sessions: () => get<SessionInfo[]>('/sessions'),
  revokeSession: (id: string) => request<void>('DELETE', `/sessions/${id}`),
};
