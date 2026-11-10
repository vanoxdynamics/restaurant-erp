# Vanox billing platform

Multi-business (SaaS) billing and point of sale for restaurants, cafés, grocery and retail shops, with the Pine Labs card terminal
built in. Each business has a category that switches on the steps it needs (tables and KOTs, quick sale, barcodes, weighed items,
tokens); see [docs/idea.md](docs/idea.md). One server, three apps:

| App | Folder | Who uses it | Local address |
|---|---|---|---|
| **API** (FastAPI, PostgreSQL 16, Redis 7) | `services/api-fastapi` | every app | http://localhost:8000/docs |
| **POS app**, counter build | `apps/pos-app` | cashier at the Pine Labs device: bills, card/UPI, prints every KOT and bill | http://localhost:8093 |
| **POS app**, waiter build | `apps/pos-app` | waiters' phones: tables, orders, KOTs (printed at the counter) | http://localhost:8094 |
| **POS app**, retail build | `apps/pos-app` (`src/retail`) | cafés and shops: quick sale, barcodes, weighed items, bill and pay | http://localhost:8095 |
| **Admin app** | `apps/admin-app` | owner and manager: menu, staff, devices, reports | http://localhost:8090 |
| **Platform console** | `apps/platform-console` | you (the SaaS owner): subscribers, plans, modules, billing | http://localhost:5180 |

What each restaurant may use is set per subscriber in the platform console (plans and modules, e.g. the kitchen display).

---

## Requirements

- **Node.js 20+** and **pnpm** (`npm install -g pnpm`)
- **Python 3.11+**
- **Docker** for PostgreSQL and Redis (Docker Desktop on Windows), or a local PostgreSQL 16 and Redis 7

---

## Run it locally

Commands are given for **Windows (PowerShell)** and **Linux/macOS (bash)**. Each long-running command needs its own terminal.

### 1. Code and JavaScript packages

```bash
git clone https://github.com/vanoxdynamics/restaurant-erp.git
cd restaurant-erp
pnpm install
```

### 2. PostgreSQL and Redis

```bash
docker compose up -d postgres redis
```

On Windows, start Docker Desktop first. If PostgreSQL is already installed locally on port 5432, start only `redis` and put your
own database user and password in `DATABASE_URL` (step 3).

### 3. API

Windows (PowerShell):

```powershell
cd services\api-fastapi
python -m venv venv
.\venv\Scripts\python -m pip install -e ".[dev]"
copy .env.example .env
.\venv\Scripts\python -m alembic upgrade head
.\venv\Scripts\python -m uvicorn app.main:app --reload --port 8000
```

Linux/macOS:

```bash
cd services/api-fastapi
python3 -m venv venv
venv/bin/python -m pip install -e ".[dev]"
cp .env.example .env
venv/bin/python -m alembic upgrade head
venv/bin/python -m uvicorn app.main:app --reload --port 8000
```

`.env.example` works as-is on your own machine. Never use its keys on a shared or production server.

### 4. Demo restaurant

With the API running, in a second terminal in `services/api-fastapi`:

```powershell
.\venv\Scripts\python -m scripts.seed_preview      # Windows
```

```bash
venv/bin/python -m scripts.seed_preview            # Linux/macOS
```

It creates the business *Spice Route*, branch *BLR1 Indiranagar*, tables, a 13-item menu, six staff accounts and some bills.

For a shop instead, `python -m scripts.seed_shop` creates the grocery *Fresh Mart* (branch *FM1*, owner `shopowner` /
`preview-shop-2026`, PIN 5831) with 15 items imported from CSV, some sold by weight. Shops use the **retail** app, not the restaurant counter.
Set `SHOP_CATEGORY=cafe` or `retail` to switch its business type.

### 5. Apps

From the repository root, one terminal each.

**Counter** (with a simulated Pine Labs terminal and printer):

```powershell
cd apps\pos-app
$env:EXPO_PUBLIC_APP_VARIANT="counter"; $env:EXPO_PUBLIC_PINELABS_SIMULATOR="1"; $env:EXPO_PUBLIC_API_URL="http://localhost:8000"
npx expo start --web --port 8093
```

```bash
cd apps/pos-app
EXPO_PUBLIC_APP_VARIANT=counter EXPO_PUBLIC_PINELABS_SIMULATOR=1 EXPO_PUBLIC_API_URL=http://localhost:8000 npx expo start --web --port 8093
```

**Waiter:** the same, with `EXPO_PUBLIC_APP_VARIANT=waiter`, no simulator, and `--port 8094`.

**Retail** (cafés and shops; a separate app from the restaurant ones): the counter command with `EXPO_PUBLIC_APP_VARIANT=retail`
and `--port 8095`.

**Admin app:**

```powershell
cd apps\admin-app
$env:EXPO_PUBLIC_API_URL="http://localhost:8000"; npx expo start --web --port 8090
```

```bash
cd apps/admin-app
EXPO_PUBLIC_API_URL=http://localhost:8000 npx expo start --web --port 8090
```

**Platform console** (same on both systems):

```bash
pnpm --filter @restaurant-erp/platform-console dev
```

### 6. First sign-in

- **Link a POS device** (once per device or browser): sign in to the admin app as `owner`, open **Staff**, tap **Link a device**,
  and type the code into the POS app.
- **Platform console:** create your operator account once (it prints an authenticator key for Google Authenticator or similar):

  ```bash
  cd services/api-fastapi
  venv/bin/python -m app.cli create-platform-admin --email you@example.com --full-name "Your Name"
  ```

  On Windows use `.\venv\Scripts\python` instead of `venv/bin/python`.

---

## Demo sign-ins (local only)

| Person | Role | Username / password (admin app) | POS PIN |
|---|---|---|---|
| Arjun Mehta | Owner | `owner` / `preview-owner-2026` | 4821 |
| Neha Kapoor | Manager | `neha.manager` / `preview-manager-2026` | 5521 |
| Imran Ali | Cashier | | 6630 |
| Ravi Kumar | Captain | | 3104 |
| Sana Shaikh | Waiter | | 3871 |
| Deepak Rao | Kitchen | | 7409 |

A manager's PIN (5521) approves large discounts, cancellations and payment reversals.

---

## Tests

API (needs PostgreSQL and Redis running; uses its own test database):

```powershell
cd services\api-fastapi
.\venv\Scripts\python -m pytest -q
```

```bash
cd services/api-fastapi
venv/bin/python -m pytest -q
```

Apps, from the repository root:

```bash
pnpm typecheck
pnpm --filter @restaurant-erp/pos-app test
```

---

## Repository layout

```text
apps/
  pos-app/            React Native (Expo): counter and waiter builds; Pine Labs module in modules/pinelabs
  admin-app/          React Native (Expo): owner/manager app
  platform-console/   React + Vite: SaaS owner console
packages/
  api-client/         typed API client and React Query hooks shared by the apps
  shared-ui/          design tokens and components
  shared-utils/       enums and helpers
services/
  api-fastapi/        API: app/ (core, models, schemas, modules), alembic/ migrations, tests/, scripts/
docs/                 API reference, operations, POS device and Pine Labs plans
docker-compose.yml    PostgreSQL, Redis, API and billing worker
```

## More documentation

- [docs/idea.md](docs/idea.md): the product on one page
- [docs/api.md](docs/api.md): authentication, idempotency, errors, WebSockets, terminal payments, subscription billing, migrations
- [docs/operations.md](docs/operations.md): production checklist, secrets, alerts, incidents, deploying the platform console
- [docs/pos-devices/](docs/pos-devices/01-counter-app-plan.md): counter and waiter apps, [Pine Labs integration](docs/pos-devices/02-pinelabs-integration.md),
  [Android builds](docs/pos-devices/03-android-builds.md), [billing for every kind of shop](docs/pos-devices/04-neutral-billing-plan.md)

## Troubleshooting

| Problem | Fix |
|---|---|
| `ConnectionRefusedError` from the API or tests | PostgreSQL or Redis is not running: `docker compose up -d postgres redis` |
| `docker compose` cannot start postgres: port 5432 in use | a local PostgreSQL is running; use it (step 2) or stop it |
| An app says "Cannot reach the server" | the API is not running, or the app's address is missing from `ALLOWED_ORIGINS` in `.env` |
| A phone or tablet cannot reach the API | start the API with `--host 0.0.0.0`, use your computer's LAN IP in `EXPO_PUBLIC_API_URL`, allow port 8000 in the firewall |
| Setup code rejected | codes last 15 minutes and work once; make a new one in the admin app |
| Kitchen staff cannot sign in | the restaurant's plan has no kitchen display; this is expected |
