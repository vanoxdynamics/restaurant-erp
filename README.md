# Restaurant ERP

A multi-restaurant (SaaS) ERP for Indian restaurants: table service, KOTs, billing with GST, staff and shifts, reports, and a
platform console from which the SaaS owner decides what each restaurant gets.

One monorepo holds a FastAPI backend (PostgreSQL 16 + Redis 7), two React Native apps for restaurants, and a web console for the
platform operators.

> **Run it on your machine:** [RUNNING_LOCALLY.md](RUNNING_LOCALLY.md) has every step, the demo data and all local sign-ins.

---

## 1. The pieces

| App | Who uses it | Runs on | What it does |
|---|---|---|---|
| **POS app** (`apps/pos-app`) | waiters, captains, cashiers, kitchen staff | shared tablets, handheld Android POS devices, web | tables, orders, KOTs, serving, billing and payment; the kitchen screen for kitchen staff |
| **Admin app** (`apps/admin-app`) | restaurant owner, manager | phone | menu, staff and PINs, linking devices, approvals, live sales, Z-reports |
| **Platform console** (`apps/platform-console`) | the SaaS owner and their team | browser | subscribers, plans and modules, billing terms and invoices, security alerts, audit log, operator accounts |
| **API** (`services/api-fastapi`) | all of the above | server | every rule is enforced here; the apps only show or hide what the server allows |

## 2. Plans and modules: what each restaurant gets

Every restaurant (subscriber) is on a **plan**, and a plan is a set of **modules**. In the platform console the SaaS owner can
create plans, move a restaurant between plans, or switch a single module on or off for one restaurant. Changes reach that
restaurant's devices within a minute, with no app update.

| Module | What it switches on |
|---|---|
| `pos` | taking orders, KOTs and billing (core) |
| `kds` | **kitchen display**: kitchen screen, ticket stages, kitchen staff accounts |
| `analytics` | reports and analytics |

The same app serves every restaurant: features are switched by **plan/module** (what they pay for), **role** (who is signed in) and
**device** (what it runs on), never by shipping separate apps.

### Two kitchen modes

| | With kitchen screen (`kds` on) | Without kitchen screen (`kds` off) |
|---|---|---|
| Waiter's button | **Send to kitchen**: one ticket per station, on the kitchen screen | **Print KOT**: one printed KOT for the whole table, handed to the kitchen |
| Kitchen | marks tickets preparing, then ready | no screen and no kitchen accounts |
| Serving | waiter marks served once ready | waiter marks served |
| Cancelling a sent item | kitchen approves the request | manager PIN on the spot; a VOID KOT prints |
| Bill and payment | owner, manager, cashier | owner, manager, cashier **and waiter** |

KOTs print as plain text, an 80 mm or 58 mm HTML page, or ESC/POS bytes for thermal printers. Every print is confirmed, reprints are
counted and audited, and an order whose KOT never printed is flagged on the floor.

## 3. Repository layout

```text
restaurant-erp/
├── apps/
│   ├── pos-app/               # React Native (Expo): POS for staff devices; also runs on web
│   ├── admin-app/             # React Native (Expo): owner/manager app
│   └── platform-console/      # React + Vite web console for platform operators
├── packages/
│   ├── api-client/            # typed API client and TanStack Query hooks shared by the apps
│   ├── shared-ui/             # design tokens and components (Button, Input, Badge, Modal, ...)
│   └── shared-utils/          # enums, money and time helpers
├── services/
│   └── api-fastapi/           # FastAPI backend
│       ├── alembic/           # database migrations
│       ├── app/
│       │   ├── core/          # config, auth (password, PIN, platform TOTP), modules and entitlements, RLS, idempotency
│       │   ├── models/        # SQLAlchemy 2.0 async models
│       │   ├── schemas/       # Pydantic v2 request/response schemas
│       │   ├── modules/
│       │   │   ├── catalog/       # categories, items, variants, modifiers, out-of-stock (86)
│       │   │   ├── floor/         # branches, floors, tables, merge/transfer
│       │   │   ├── orders/        # order lifecycle, price snapshots, optimistic locking
│       │   │   ├── kots/          # firing, kitchen screen vs printed KOTs, slip rendering, voids
│       │   │   ├── approvals/     # cancel requests and manager approvals
│       │   │   ├── billing/       # GST, discounts, split payments, UPI QR
│       │   │   ├── staff/         # staff, roles, PINs, devices, shifts, cash drops
│       │   │   ├── analytics/     # live KPIs, Z-report, menu engineering, fraud audit
│       │   │   ├── entitlements/  # what the caller's plan includes (/me/entitlements)
│       │   │   └── platform/      # platform console API: subscribers, plans, modules, billing, security, audit
│       │   ├── realtime/      # WebSocket manager + Redis pub/sub
│       │   ├── worker/        # background tasks
│       │   └── cli.py         # create-owner, create-platform-admin, run-billing, verify-audit, ...
│       ├── scripts/           # seed_preview (demo restaurant), benchmarks
│       └── tests/             # pytest suite against real Postgres and Redis
├── docs/
│   ├── backend-remediation/   # backend design notes
│   └── platform-admin/        # platform plan, operations runbooks, kitchen module plan
├── docker-compose.yml         # Postgres 16, Redis 7, API
└── RUNNING_LOCALLY.md         # full local setup and demo credentials
```

## 4. Quick start

Short version; details, sign-ins and troubleshooting are in [RUNNING_LOCALLY.md](RUNNING_LOCALLY.md).

```bash
pnpm install
```

```bash
docker compose up -d postgres redis
```

From `services/api-fastapi`, with a virtual environment active and `.env` copied from `.env.example`:

```bash
pip install -e ".[dev]"
```

```bash
python -m alembic upgrade head
```

```bash
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

In a second terminal, load the demo restaurant (owner `owner` / `preview-owner-2026`, waiter PIN `3871`):

```bash
python -m scripts.seed_preview
```

Start the apps:

```bash
cd apps/pos-app && npx expo start --web --port 8091
```

```bash
cd apps/admin-app && npx expo start --web --port 8090
```

```bash
pnpm --filter @restaurant-erp/platform-console dev
```

API docs are at http://127.0.0.1:8000/docs.

## 5. Security in brief

- **Staff:** owners and managers sign in with username and password; staff sign in on a linked device with a PIN. Devices are linked
  to a branch with a one-time setup code from the admin app.
- **Platform operators:** email, password and an authenticator (TOTP) code. Sensitive actions ask again at most once an hour;
  second-person approval for dangerous actions is optional (`PLATFORM_FOUR_EYES=true`).
- **Tenant isolation:** PostgreSQL row-level security keeps each restaurant's data apart; the test suite also runs as a restricted
  database role to prove it.
- **Audit:** platform actions go to a tamper-evident audit log (`python -m app.cli verify-audit`); reprints, voids and approvals are
  audited on the restaurant side.

Operating the platform (secrets, backups, scheduled jobs, key rotation, incidents): see
[docs/platform-admin/01-runbooks.md](docs/platform-admin/01-runbooks.md).

## 6. Tests

From `services/api-fastapi`:

```bash
python -m pytest -q
```

Type-check every app and package from the repo root:

```bash
pnpm typecheck
```

## 7. API client

The apps share `packages/api-client`: a hand-written, typed client (`src/client.ts`) and TanStack Query hooks (`src/hooks.ts`) that
adapt the API's responses for the apps. `pnpm generate:api` can produce raw OpenAPI types into `src/generated` from a running API,
but the apps do not use generated code today.

## 8. Status and next steps

- Done: orders, KOTs, billing, staff, analytics, platform console (subscribers, plans, modules, billing, security), the kitchen
  module with printed KOTs, and the POS Print KOT workflow (checked on web).
- Next: **Pine Labs handheld POS**. The Android package name, signing key and UAT build are still to be set up, and the payment and
  printer integration depends on Pine Labs' integration method.
  On the device: tables, orders, Print KOT on the built-in printer, serving, billing and payment. Menu, staff and reports stay in
  the admin app.
- Not yet tried on a real tablet or thermal printer.
