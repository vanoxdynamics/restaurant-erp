# Platform Admin Panel — Plan

Goal: a **platform operator** (you, the SaaS owner) can enable/disable ERP modules and have full control over
every **subscriber** (restaurant business), including choosing each subscriber's billing term from monthly to yearly.

## 0. Findings that shape the plan

- The backend is **single-tenant today**: `Branch` is the top level, `owner` role spans all branches, there is no
  organization/subscriber/plan/module concept (grep for tenant/subscription/feature flags returns nothing).
- Auth is JWT (HS256) with `token_version` revocation, `amr` (pwd/pin), Redis login rate-limiter, `AuditLog` keyed by `branch_id`.
- Existing `admin-app` (React Native) is the *restaurant owner's* app. The platform operator console must be a **separate surface**.

So the real work is: introduce a **tenant layer above branches**, then build the control plane on it.

## 1. Architecture decisions

| Decision | Choice | Why |
|---|---|---|
| Tenant model | New `organizations` table; `branches.organization_id NOT NULL` | Minimal change; every existing table already hangs off `branch_id`, so isolation is inherited via branch |
| Control-plane identity | Separate `platform_admins` table + separate JWT audience (`aud=platform`) and signing key | A tenant token can never be valid on `/platform/*`, and vice versa |
| Console UI | New web app `apps/platform-console` (React + Vite), not inside tenant `admin-app` | Different trust boundary, desktop-first, can sit behind VPN/IP allowlist |
| API surface | New `app/modules/platform/` mounted at `/api/v1/platform/*` | Isolated router, own dependency (`require_platform_admin`) |
| Module gating | Entitlement check in one place (`require_module("kds")`) used as a router dependency | One enforcement point, cannot be forgotten per-endpoint if applied at router level |
| Isolation backstop | Postgres RLS on `organization_id`-scoped tables (phase 2) | Defence in depth if an app-layer filter is missed |

## 2. Data model (new Alembic migration)

- `organizations`: id, name, legal_name, slug, status (`trial|active|past_due|suspended|cancelled`), billing_email, country, timezone, created_at, suspended_at, suspension_reason.
- `modules` (catalog, seeded in code): key (`pos`, `kds`, `inventory`, `analytics`, `loyalty`, `reservations`, `multi_branch`, `api_access`, …), name, description, `is_core` (core cannot be disabled), `depends_on[]`.
- `plans`: id, code, name, `is_active`, included module keys, limits (max_branches, max_staff, max_devices), per-term prices.
- `subscriptions`: id, organization_id, plan_id, **`billing_term`** (`monthly|quarterly|half_yearly|yearly`), `price_amount`, `currency`, `started_at`, `current_period_start/end`, `auto_renew`, `status` (`trialing|active|past_due|paused|cancelled|expired`), `grace_until`, `cancel_at_period_end`, `trial_ends_at`.
- `organization_modules` (overrides): organization_id, module_key, `enabled`, `source` (`plan|override`), `expires_at`, `set_by`, `reason`. Effective entitlement = plan modules ± overrides.
- `subscription_events` (append-only): every term change, renewal, proration, extension, suspension.
- `platform_audit_logs` (append-only, hash-chained): id, actor_admin_id, action, target_type/id, `before`/`after` JSONB, ip, user_agent, request_id, `prev_hash`, `hash`. DB role gets INSERT/SELECT only (no UPDATE/DELETE).
- `platform_admins`: id, email, password_hash (argon2id), `totp_secret_enc`, role (`super_admin|support|billing|read_only`), is_active, token_version, last_login_at, failed_attempts; plus `platform_admin_sessions` and `platform_recovery_codes`.
- Migration backfill: create one org per existing deployment, attach all branches, create an `active` subscription on a "legacy-all-modules" plan so nothing breaks.

## 3. Admin capabilities (API + console)

**Subscribers**
- Create / edit / list (search, filter by status, term, plan, expiry) / view detail.
- Lifecycle: start trial, activate, **suspend** (instant, revokes sessions), reinstate, cancel (end of period or immediate), soft-delete with a retention window; hard purge is a separate two-person-approved action.
- **Term control:** set or change `billing_term` monthly ↔ yearly at any time; choose effective *now* or *at next renewal*; proration preview before confirm; manual period extension/credit with mandatory reason; toggle auto-renew.
- Change plan, adjust limits (branches/staff/devices), custom price override.
- **Impersonation (support):** read-only by default, time-boxed (≤30 min), requires reason, banner shown in the tenant app, fully audited, blocked for `read_only` role.

**Modules**
- Global catalog view: enable/disable a module platform-wide (kill switch).
- Per-subscriber: toggle any module, with optional expiry and a reason; dependency check (cannot disable `inventory` if `recipes` depends on it); core modules locked.
- Bulk apply to a plan or a selection of subscribers (dry-run diff first).

**Platform operations:** admin user management and role assignment, audit-log viewer/export, active sessions with remote revoke, dashboard (MRR/ARR, active/trial/past-due counts, upcoming renewals, churn).

## 4. Enforcement in the tenant API

- Tokens gain `org` claim; `resolve_token` loads the org + subscription state.
- `require_active_subscription`: blocks writes when `suspended|cancelled|expired`; `past_due` gets a configurable grace period, reads allowed (restaurants must still be able to close a bill and export data).
- `require_module("<key>")` applied per router (KDS, inventory, analytics, …); returns `403 module_disabled` with a stable code so apps can show an upgrade state.
- `GET /me/entitlements` returns modules + limits + subscription status; both apps hide disabled modules from navigation (UI is cosmetic, server is authoritative).
- Limit enforcement (branches/staff/devices) at create time.
- Entitlements cached in Redis (TTL 60 s) with **instant invalidation** on any admin change; realtime `entitlements.changed` event to connected clients. Fail closed if the cache and DB are both unreachable for write paths.
- Suspend/disable bumps `token_version` for the org's users and closes their WebSockets.

## 5. Security design (production level)

**Authentication**
- Argon2id passwords, min length 14, breached-password check; **mandatory TOTP 2FA** (WebAuthn/passkeys as phase-2 upgrade); one-time recovery codes.
- Short-lived access token (10–15 min) + rotating refresh token in `HttpOnly; Secure; SameSite=Strict` cookie with reuse detection (reuse revokes the whole session family).
- Separate `PLATFORM_SECRET_KEY` (asymmetric ES256/EdDSA preferred), `aud=platform`, `iss` check; tenant tokens rejected outright.
- Login rate limiting per IP **and** per account with progressive lockout; generic error messages; constant-time compare.
- Idle timeout 15 min, absolute session 8 h; step-up re-auth (fresh password+TOTP within 5 min) for destructive actions.

**Authorization**
- RBAC on `platform_admins.role`: `super_admin` (everything), `billing` (subscriptions/terms/prices), `support` (view, impersonate read-only, reset tenant owner password), `read_only`.
- Dangerous actions (suspend, cancel, purge, disable a module platform-wide, change admin roles) need **step-up auth + reason**; purge/bulk-disable need **second-admin approval** (four-eyes).

**Network and transport**
- Console and `/platform/*` served on a **separate hostname**; optional IP allowlist / VPN / Cloudflare Access in front; `/platform/*` not exposed on the public tenant host.
- Strict CORS (single console origin, no wildcard), CSRF double-submit token for cookie-authenticated calls, HSTS, CSP (`default-src 'self'`, no inline), `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, Cache-Control `no-store` on API responses.

**Data and integrity**
- Pydantic strict schemas, parameterised queries only (SQLAlchemy), idempotency keys on billing mutations (reuse `core/idempotency.py`), optimistic locking (`version`) on subscription rows to stop concurrent edits.
- Tamper-evident audit chain (hash of previous row); daily verifier job alerts on a break; audit exported to WORM storage.
- TOTP secrets and any provider keys encrypted at rest (envelope encryption / KMS); secrets never logged; PII masked in logs.
- Principle of least privilege DB roles: app role cannot UPDATE/DELETE audit tables; separate read-only role for reporting.
- Tenant isolation test suite: every endpoint tested with a token from org A against org B data (must 404).

**Operations**
- Alerts on: failed-login spikes, new admin created, role change, platform-wide module disable, audit-chain break, impersonation started.
- Backups with tested restore, dependency scanning (pip-audit, npm audit) + SAST in CI, secrets via env/secret manager, no default credentials; first admin created only via a one-time CLI command (`python -m app.cli create-platform-admin`), never via an open endpoint.

## 6. Billing and renewal engine

- Term price table per plan (monthly, yearly with discount); the admin picks term per subscriber.
- Background worker (existing `app/worker`): daily renewal sweep → `past_due` → grace → `suspended`; reminder events at T-14/T-7/T-1; idempotent and safe to re-run.
- Proration rules on mid-period term/plan change (credit unused time, charge new term) with a preview endpoint; all money via `core/money.py` Decimals.
- Payment provider is a pluggable interface (Razorpay/Stripe) with signature-verified webhooks; phase 1 supports **manual/offline payment recording** by the billing role so the console is useful before a gateway is wired.

## 7. API sketch (`/api/v1/platform`)

```
POST   /auth/login | /auth/2fa | /auth/refresh | /auth/logout | /auth/step-up
GET    /dashboard
GET    /modules                      PATCH /modules/{key}            (global kill switch)
GET    /plans                        POST/PATCH /plans
GET    /organizations                POST /organizations
GET    /organizations/{id}           PATCH /organizations/{id}
POST   /organizations/{id}/suspend | /reinstate | /cancel
PUT    /organizations/{id}/modules/{key}        (enable/disable + expiry + reason)
PUT    /organizations/{id}/subscription         (plan, billing_term, auto_renew, price override)
POST   /organizations/{id}/subscription/preview-change   (proration)
POST   /organizations/{id}/subscription/extend
POST   /organizations/{id}/impersonate
GET    /audit-logs  | /audit-logs/verify
GET/POST/PATCH /admins   ,  DELETE /sessions/{id}
POST   /approvals/{id}/approve          (four-eyes)
```

## 8. Delivery phases

1. **Tenant foundation** — `organizations`, backfill migration, `org` claim in tokens, isolation tests. *Exit: existing app unchanged, all tests green.*
2. **Entitlements** — modules/plans/subscriptions/overrides tables, `require_module`, `require_active_subscription`, `/me/entitlements`, Redis cache + invalidation.
3. **Platform auth and security core** — platform_admins, 2FA, sessions, RBAC, step-up, hash-chained audit, CLI bootstrap.
4. **Platform API** — subscriber CRUD/lifecycle, module toggles, term change + proration preview, extend/suspend/cancel, impersonation.
5. **Console UI** — `apps/platform-console`: dashboard, subscriber list/detail (term selector Monthly/Yearly, module switches, limits), modules catalog, audit viewer, admins.
6. **Billing engine** — renewal worker, grace/suspend flow, reminders, manual payment recording, then gateway + webhooks.
7. **Hardening** — RLS, four-eyes approvals, WebAuthn, alerts, pen-test pass, load test of the entitlement path, runbooks.

## 9. Testing

- Unit: entitlement resolution (plan ± override, expiry, dependencies), proration math, term transitions, state machine for subscription status.
- Integration: disabled module → 403 `module_disabled`; suspended org → writes blocked, reads allowed; cache invalidated immediately after admin change; token revoked on suspend.
- Security: tenant token on `/platform/*` rejected; support role cannot suspend; replayed refresh token kills session; audit-chain tamper detected; rate-limit lockout; CSRF; IDOR across orgs.
- Migration: backfill on a copy of current data, upgrade and downgrade.

## 10. Decisions (resolved)

1. Console: **web only** (`apps/platform-console`).
2. Payments: **manual/offline recording first**; gateway (Razorpay/Stripe) is a later phase behind the pluggable interface.
3. Billing terms: **monthly and yearly** only (schema keeps `billing_term` an enum so quarterly/half-yearly can be added later).
4. Defaults: **14-day trial, 7-day grace** (configurable per plan/subscriber).
5. Existing deployments: migrated to a **"legacy all modules" plan** (default assumed, not yet confirmed).
6. Hosting: **separate subdomain behind IP allowlist / Cloudflare Access** (default assumed, not yet confirmed).

## 11. Delivery status

| Phase | State |
|---|---|
| 1 Tenant foundation | done: organizations, backfill migration, org-scoped auth and data |
| 2 Entitlements | done: plans, subscriptions, module overrides, cached `require_module`, limits |
| 3 Platform auth and security core | done: operators, TOTP 2FA, sessions with refresh rotation, RBAC, step-up, hash-chained audit, CLI bootstrap |
| 4 Platform API | done: subscribers, terms with proration, modules, plans, impersonation, dashboard |
| 5 Console UI | done: `apps/platform-console` |
| 6 Billing engine | done: invoices, manual payments, renewals, reminders, past-due/grace, worker; gateway not built |
| 7 Hardening | done except passkeys: alerts and webhook, four-eyes approvals, audit verifier, break-glass 2FA reset, key rotation, row-level security (full suite verified as a restricted role), security scan and attack tests, request size limit, entitlement benchmark, runbooks |

Deferred on purpose, see `01-runbooks.md` section 8: passkeys/WebAuthn, breached-password check, real email delivery, payment gateway and refunds,
subscriber retention/purge flow.
