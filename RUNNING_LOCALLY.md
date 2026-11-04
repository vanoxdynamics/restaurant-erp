# Running Restaurant ERP locally

Everything you need to run the whole stack on your own machine: the API, the waiter (POS) app, the owner/manager admin app
and the platform console, plus the demo data and every sign-in that comes with it.

> **All passwords and PINs in this file are for local demo data only.** They are created by `scripts/seed_preview.py` on
> your machine. Never use them on a shared, staging or production server.

---

## 1. What runs where

| Piece | Folder | Local address | Who uses it |
|---|---|---|---|
| PostgreSQL 16 | Docker or a local install | `localhost:5432` | the API |
| Redis 7 | Docker | `localhost:6379` | the API (sessions, entitlements, idempotency, realtime) |
| API (FastAPI) | `services/api-fastapi` | http://127.0.0.1:8000 (docs at `/docs`) | every app |
| Admin app (owner, manager) | `apps/admin-app` | http://localhost:8090 (web) or Expo Go | owner and manager phones |
| POS app (waiter, cashier, captain, kitchen) | `apps/pos-app` | http://localhost:8091 (web) or Expo Go | restaurant tablets |
| Platform console (operators) | `apps/platform-console` | http://localhost:5180 | the people running the platform |

"POS app" and "waiter app" are the same thing: the tablet app the floor staff use.

---

## 2. Prerequisites

- **Node.js 18+** and **pnpm 8+**
- **Python 3.11+**
- **Docker Desktop** (for Redis, and Postgres if you don't have it installed)
- For phones and tablets: **Expo Go** on the device, on the same Wi-Fi as your computer

Install the JavaScript dependencies once, from the repo root:

```bash
pnpm install
```

---

## 3. Start Postgres and Redis

```bash
docker compose up -d postgres redis
```

This gives you Postgres with user `postgres`, password `postgres` and database `restaurant_erp`, and Redis on port 6379.

**Already have Postgres installed on Windows** (the `postgresql-x64-16` service)? Then it already holds port 5432. Start only
Redis and use your own Postgres's username and password in `.env`:

```bash
docker compose up -d redis
```

Check both are reachable:

```bash
docker exec restaurant-erp-redis redis-cli ping
```

It should print `PONG`. If Docker reports it cannot connect, open Docker Desktop first and wait for it to say "Engine running".

---

## 4. Run the API

From `services/api-fastapi`:

```bash
cd services/api-fastapi
python -m venv venv
```

Activate the virtual environment. On Windows (Git Bash):

```bash
source venv/Scripts/activate
```

On macOS or Linux:

```bash
source venv/bin/activate
```

Install the API with its development tools, and create your settings file:

```bash
pip install -e ".[dev]"
```

```bash
cp .env.example .env
```

Edit `.env` for local use:

| Setting | Local value | Why |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@localhost:5432/restaurant_erp` | change user/password if you use your own Postgres |
| `REDIS_URL` | `redis://localhost:6379/0` | |
| `ALLOWED_ORIGINS` | `http://localhost:8090,http://localhost:8091,http://localhost:8092,http://localhost:8081` | lets the web apps call the API |
| `PLATFORM_COOKIE_SECURE` | `false` | the console runs on plain `http://localhost`; some browsers drop `Secure` cookies there |

The other values in `.env.example` are development defaults and work as they are. **Never** use them outside your own machine.

Create the tables, then start the API:

```bash
python -m alembic upgrade head
```

```bash
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000/docs to see every endpoint.

---

## 5. Load the demo restaurant

With the API running, in a second terminal (venv active, in `services/api-fastapi`):

```bash
python -m scripts.seed_preview
```

It creates, through the real API:

- organization **Spice Route**, branch **BLR1 · Indiranagar**
- floors **Main hall** (tables T1–T6) and **Terrace** (P1–P3)
- a menu of 13 items in 5 categories across the `kitchen`, `grill` and `dessert` stations (Kulfi is marked out of stock)
- six staff accounts (below)
- a few paid bills, one billed table and two open tables, so the reports and the floor have data

Running it again does nothing once the data exists. To start over, drop and recreate the `restaurant_erp` database and run the
migrations again.

---

## 6. Credentials (local demo only)

### Restaurant staff

Owners and managers sign in to the **admin app** with a username and password. Everyone signs in on the **POS tablet** with a
4-digit PIN after choosing their name.

| Name | Role | Username | Password (admin app) | POS PIN |
|---|---|---|---|---|
| Arjun Mehta | Owner | `owner` | `preview-owner-2026` | `4821` |
| Neha Kapoor | Manager | `neha.manager` | `preview-manager-2026` | `5521` |
| Ravi Kumar | Captain | `ravi.captain` | — | `3104` |
| Sana Shaikh | Waiter | `sana.waiter` | — | `3871` |
| Imran Ali | Cashier | `imran.cashier` | — | `6630` |
| Deepak Rao | Kitchen | `deepak.kitchen` | — | `7409` |

**Manager PIN for approvals:** `5521` (Neha). The POS asks for it for large discounts and for cancelling a ticket on the
slip plan.

### Platform operators (console)

There are no default operator accounts: you create your own (section 9). Each one gets a password and an authenticator app
(TOTP) code, printed once when you create it.

### Infrastructure

| What | Value |
|---|---|
| Postgres (Docker) | user `postgres`, password `postgres`, database `restaurant_erp`, port 5432 |
| Redis | `redis://localhost:6379/0`, no password |

---

## 7. Run the POS app (counter and waiter)

The POS app builds two ways (see [docs/pos-devices](docs/pos-devices/01-counter-app-plan.md)):

- **Counter**, the restaurant's one Pine Labs device: bills to collect, payments, and it prints every KOT and bill. This is the
  default when nothing is set.
- **Waiter**, staff phones: tables and orders; KOTs print at the counter and there is no billing.

### In a browser

The counter, with a **simulated Pine Labs terminal and printer** (approve, decline or lose a payment's answer; make the printer
run out of paper; see printed paper under **KOT printer**):

```bash
cd apps/pos-app
```

```bash
EXPO_PUBLIC_APP_VARIANT=counter EXPO_PUBLIC_PINELABS_SIMULATOR=1 EXPO_PUBLIC_API_URL=http://127.0.0.1:8000 npx expo start --web --port 8093
```

A waiter's phone, in another browser tab (link it with its own setup code):

```bash
EXPO_PUBLIC_APP_VARIANT=waiter EXPO_PUBLIC_API_URL=http://127.0.0.1:8000 npx expo start --web --port 8094
```

Add `http://localhost:8093,http://localhost:8094` to `ALLOWED_ORIGINS` in the API's `.env`. Without the simulator, the browser
counter prints through the browser's print dialog and card/UPI are recorded by hand.

### On a tablet (Expo Go)

```bash
cd apps/pos-app
```

```bash
EXPO_PUBLIC_API_URL=http://<your-computer's-LAN-IP>:8000 npx expo start --lan
```

Scan the QR code with Expo Go. For this the API must listen on your network, so start it with `--host 0.0.0.0` instead of
`127.0.0.1`.

### Link the tablet to the branch (first time only)

A new tablet shows **Set up this device** and asks for a setup code.

1. Sign in to the **admin app** as `owner` (or `neha.manager`).
2. Go to **Staff** and tap **Link a device**. You get a code like `K7M2-9QXP`, valid for 15 minutes.
3. Type it on the tablet and tap **Link device**.

Without the admin app, you can create a code from the API docs page: sign in with `POST /api/v1/staff/auth/login`, then call
`POST /api/v1/staff/devices/setup-codes` with the token.

Then pick a person on the tablet and enter their PIN (for example **Sana Shaikh**, PIN `3871`). The tablet locks after two
minutes without a touch.

---

## 8. Run the admin app

```bash
cd apps/admin-app
```

```bash
EXPO_PUBLIC_API_URL=http://127.0.0.1:8000 npx expo start --web --port 8090
```

Open http://localhost:8090 and sign in as `owner` / `preview-owner-2026`. For a phone, use `npx expo start --lan` with the
API's LAN address, as for the POS.

---

## 9. Run the platform console

Create your operator account once, from `services/api-fastapi` (venv active):

```bash
python -m app.cli create-platform-admin --email you@example.com --full-name "Your Name"
```

It asks for a password (14+ characters, mixed) and prints an **authenticator key** and **recovery codes** once. Add the key to
an authenticator app (Google Authenticator, 1Password, and so on) and keep the recovery codes. Add more operators later from
the console under **Team → Add operator**.

After sign-in, money and security actions ask for your password and code again at most once an hour. Nothing waits for a second
person's approval unless you set `PLATFORM_FOUR_EYES=true` in `.env`.

Start the console from the repo root:

```bash
pnpm --filter @restaurant-erp/platform-console dev
```

Open http://localhost:5180 and sign in with the email, password and the 6-digit code from your authenticator. The console
forwards `/api` to `http://127.0.0.1:8000`. To use another API, set `VITE_API_TARGET`, for example
`VITE_API_TARGET=http://127.0.0.1:8010`.

Lost the authenticator? Reset it with:

```bash
python -m app.cli reset-platform-admin-2fa --email you@example.com
```

---

## 10. Try the two kitchen modes

The same product runs two ways, decided by the **Kitchen display** module (`kds`) on the restaurant's plan:

| | Screen plan (has `kds`) | Slip plan (no `kds`) |
|---|---|---|
| Waiter's button | **Send to kitchen**: one ticket per station, on the kitchen screen | **Print KOT**: one printed KOT for the whole table, lines grouped by station; the waiter hands it to the kitchen |
| Kitchen staff | sign in to the POS and see the kitchen screen | no kitchen screen; kitchen accounts are refused |
| Item status | kitchen: preparing, ready; floor: served | waiter marks the slip **served** |
| Cancelling a sent ticket | request goes to the kitchen to approve | manager PIN on the spot, and a VOID slip prints |
| Who can bill and take payment | owner, manager, cashier | owner, manager, cashier **and waiter** |

The demo restaurant starts on the screen plan. To try the slip plan:

1. In the platform console, open **Subscribers** → **Spice Route**.
2. In its **Modules** card, disable **Kitchen display** (give a reason when asked). Enable it again to go back to the screen plan.
   For a lasting setup, create a plan without `kds` under **Plans** and use **Change plan / term** on the subscriber instead.
3. The POS picks up the change within a minute; no restart needed.

Then, signed in as Sana (`3871`): open a table, add items and tap **Print KOT**. The KOT opens in the print dialog
(browser print on web, the system print sheet on a tablet). Answer **Yes, printed** when the paper is out. Tickets that never
printed show **Slip not printed** on the order and a red **not printed** chip on the floor.

Direct thermal printing (LAN/Bluetooth, ESC/POS) is not built yet; today any printer the operating system can print to works.

---

## 11. Run the tests

From `services/api-fastapi` (venv active). The tests need Postgres and Redis running. They create and migrate their own
database (`restaurant_erp_test`) and use Redis database 15, so they never touch your demo data.

```bash
python -m pytest -q
```

Run the suite again as an ordinary database role, so row-level security is enforced as it is in production:

```bash
RLS_TEST_ROLE=rls_app TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/restaurant_erp_test_rls TEST_REDIS_URL=redis://localhost:6379/14 python -m pytest -q
```

Both take about 20 minutes. Type-check the apps from the repo root:

```bash
pnpm typecheck
```

---

## 12. Troubleshooting

| Symptom | Fix |
|---|---|
| Tests or the API fail with `ConnectionRefusedError` | Redis or Postgres isn't running. Start Docker Desktop, then `docker compose up -d redis`. |
| `docker compose up postgres` fails: port 5432 in use | A local Postgres is already running. Use it (section 3) or stop it. |
| Web app shows "Cannot reach the server" | The API isn't on the address in `EXPO_PUBLIC_API_URL`, or that app's address is missing from `ALLOWED_ORIGINS`. |
| Platform console signs you out right away | Set `PLATFORM_COOKIE_SECURE=false` in `.env` and restart the API. |
| Tablet can't reach the API | Start the API with `--host 0.0.0.0` and use your computer's LAN IP in `EXPO_PUBLIC_API_URL`. Check the firewall allows port 8000. |
| Setup code rejected | Codes last 15 minutes and work once. Make a new one in the admin app. |
| Kitchen staff can't sign in | The restaurant is on the slip plan, which has no kitchen screen. Expected. |
| Changes to `.env` ignored | Restart the API; settings are read at start-up. |

More operational detail (secrets, backups, scheduled jobs, rotating keys) is in
[docs/platform-admin/01-runbooks.md](docs/platform-admin/01-runbooks.md).
