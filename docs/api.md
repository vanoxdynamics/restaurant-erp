# API reference

FastAPI service in `services/api-fastapi`: REST under `/api/v1`, authenticated WebSockets, SQLAlchemy 2 (async), Pydantic v2,
Redis for idempotency, rate limits and realtime fan-out. Interactive docs at `http://localhost:8000/docs` (disabled when
`ENVIRONMENT=production`). Health: `/health` (liveness) and `/health/ready` (database and Redis; 503 when either is down).

How to run it: see the [README](../README.md).

## Configuration

See `services/api-fastapi/.env.example`. Outside `development`/`test`, `SECRET_KEY` and `PIN_PEPPER` must be unique
values of 32+ characters and `DEBUG` must be false, or the app refuses to start.

## Authentication

Every API route requires `Authorization: Bearer <token>` except the two login routes.

- `POST /api/v1/staff/auth/login` — username + password (owners, managers).
- `POST /api/v1/staff/auth/pin` — `{branch_id, pin, device_id?}` for POS/handhelds. PINs are unique
  per branch among active staff.

Both are rate limited (5 failures → 5-minute lockout, `429` with `Retry-After`). Manager screens
(staff management, analytics) require a password login, not a PIN session. Sensitive actions
(voids, cancellations, large discounts) by non-managers take a manager's `approver_pin`.

## Idempotency

`POST /orders`, `POST /kots/fire`, `POST /billing/bills`, `POST /billing/bills/{id}/payments` and
`/split-payments` require `X-Idempotency-Key` (8–100 chars, unique per logical request).

- Retrying with the same key and body returns the original status and body (`Idempotent-Replay: true`).
- Same key with a different body → `422 idempotency_key_reused`.
- A request still in progress → `409 request_in_progress`.
- A failed request releases its key, so the client can retry.

## Errors

All errors use one envelope: `{"error": {"code": "...", "message": "...", "details": {...}}}`.
Stale optimistic-lock versions return `409` with `details.current_version`.

## Document numbers

Allocated transactionally in Postgres (`document_counters`), per branch:
`ORD-BLR1-20261028-0001`, `KOT-BLR1-20261028-0001` (daily, branch-local date) and bills
`BLR1/2627/000001` (per Indian financial year, ≤16 characters for GST).

## WebSockets

`ws://host/ws/branches/{branch_id}/{topic}` with `topic` one of `orders`, `kots`, `tables`,
`billing`, `catalog`.

1. Within 5 s send `{"type": "auth", "token": "<JWT>"}`.
2. Server replies `{"type": "ready", ...}` or closes with `4401` (bad/missing token) or `4403`
   (no access to the branch or topic).
3. Events arrive as `{"type": "kot.fired", "branch_id", "entity_id", "data", "ts"}`.
   Send `{"type": "ping"}` to get `{"type": "pong"}`; idle sockets close after 60 s.

Delivery is best-effort: after reconnecting, refetch state over REST. Events fan out across
workers through Redis pub/sub and are only published after the database commit.

## Counter device and card terminals

- **Print station.** The counter device prints with its device token (`X-Device-Token`, issued by `POST /staff/devices/link`)
  instead of a person's session: `GET /kots/station/print-queue`, `GET /kots/station/{id}/slip`, `POST /kots/station/{id}/printed`
  (needs `X-Idempotency-Key`). Staff equivalents: `GET /kots/print-queue`, `POST /kots/{id}/slip/printed`.
- **KOT and bill printing.** `GET /kots/{id}/slip` and `GET /billing/bills/{id}/receipt` return `rows` (style, align, text) for
  58 or 80 mm paper (`width`), or `format=text|html` (slips also `escpos`).
- **Terminal payments (Pine Labs).** `POST /billing/bills/{id}/terminal-payments` saves a card/UPI payment as `pending` with a
  10-character `billing_ref_no`; `POST /billing/payments/{id}/complete` records the terminal's answer (approved or declined; safe
  to repeat); `POST /billing/payments/{id}/reverse` voids it the same day (manager approval; `check_only: true` validates first).
  While a payment is pending, other tenders and voiding the bill are refused (`409 terminal_payment_pending`).
  Details: [pos-devices/02-pinelabs-integration.md](pos-devices/02-pinelabs-integration.md).

## Business categories and shops

Each organization has a `category` (`restaurant`, `cafe`, `grocery`, `retail`) that presets six workflow settings:
`table_service`, `kitchen_tickets`, `quick_sale`, `barcode`, `weighed_items`, `tokens`. Only per-business overrides are
stored (`organizations.settings`); `GET /me/entitlements` returns `category` and the effective `settings`. Operators set both
with `POST /platform/organizations` and `PATCH /platform/organizations/{id}` (`category`, `settings`); `GET
/platform/business-categories` lists the presets. Presets live in `app/core/business.py`.

Restaurants and shops use **separate apps**: restaurants the counter and waiter builds of the POS app, cafés and shops the
`retail` build (`apps/pos-app/src/retail`). Each refuses a business of the other kind. On the server, shop-only endpoints live in
their own module (`app/modules/retail`, under `/retail`); catalog, orders, billing, payments and printing are shared.

- **Counter sales.** `order_type: "counter_sale"` is a sale with no table, created with its items. When `tokens` is on it gets a
  daily per-branch `token_number`, printed large on the bill and KOT. Billing a sale when `kitchen_tickets` is off does not
  require firing; when it is on, billing a counter sale fires its KOT in the same transaction. Dine-in and takeaway orders
  keep the old rule (`409 unfired_items`).
- **Quantities** are decimals with 3 places (`0.750`). Items not `sold_by_weight` must be whole (`422
  whole_quantity_required`). Responses send whole quantities as JSON integers (`2`), others as numbers (`0.75`).
- **Item fields.** `barcode` and `sku` (unique per business, `409 duplicate_barcode|duplicate_sku`), `hsn_code` (4–8
  digits), `unit` (`pcs`, `kg`, `g`, `l`, `ml`; price is per unit), `sold_by_weight`. Order lines snapshot `unit_snapshot` and
  `hsn_snapshot`; the bill prints units, the per-unit rate of loose items, and HSN codes.
- **Barcode lookup.** `GET /retail/items/lookup?code=` finds the active item by barcode, then SKU (`404 barcode_not_found`).
- **CSV import.** `POST /retail/items/import` (managers) with `{"csv": "...", "dry_run": false}`. Columns: `name`,
  `category`, `price` (required), `gst_rate`, `hsn`, `unit`, `barcode`, `sku`, `sold_by_weight`. Rows update the item with the
  same SKU, else barcode, else name in the same category; missing categories are created. Bad rows are skipped and reported
  as `errors: [{row, errors, data}]` with the file's line number; good rows are saved together. Up to 10,000 rows.

## Business name and logo on bills

The business sets how its bills look (owners and managers): `GET/PATCH /business/profile` (`display_name`; null goes back to
the registered name) and `PUT/DELETE /business/logo` (a PNG or JPEG under 500 KB, base64). Bills print the business name large
with the branch name under it; the HTML bill shows the logo at the top in black and white, and `GET
/billing/bills/{id}/receipt` returns `logo_data_url` for printers that can print images. The text rows for line printers (the
Pine Labs device) do not include the logo yet.

## Subscription billing

Operators record what subscribers owe and pay through the platform API (`/api/v1/platform`, see the console app).

- **Invoices and payments.** Activation and plan-change invoices describe a period that is already in effect; renewal
  invoices are issued `BILLING_INVOICE_LEAD_DAYS` (14) before a period ends, and **recording the payment in full is what
  extends the subscription** and applies any scheduled monthly/yearly change. Overpayments and downgrade leftovers become
  account credit that pays the next invoice. Payments are recorded by hand (bank transfer, UPI, cash, ...); there is no
  payment gateway yet, and the `InvoicePayment` model leaves room for a provider reference.
- **The billing worker** runs the renewal sweep: it issues renewal invoices, queues reminders (14/7/1 days before renewal,
  3/1 before a trial ends), moves unpaid subscriptions to *past due* (full access during `SUBSCRIPTION_GRACE_DAYS`), then to
  *expired* (read-only), and cancels subscriptions set to end with their period. It never suspends on its own.

  ```bash
  python -m app.cli run-billing            # one pass, e.g. from cron / a Kubernetes CronJob
  python -m app.cli run-billing --loop     # keep running every BILLING_SWEEP_INTERVAL_SECONDS (docker compose: billing-worker)
  ```

  Safe to run twice or on several machines at once: each subscription is locked with `SKIP LOCKED`, every action is
  idempotent, and a failure on one subscription does not stop the rest. Access rules are also evaluated lazily from the
  dates, so a stopped worker never grants or removes access incorrectly; it only delays invoices, reminders and the
  stored status.
- **Reminders** are written to the `billing_notifications` outbox. Delivery uses a `Mailer`; the default only logs, so
  plug in an SMTP/API mailer in `app/modules/platform/billing_service.py` before relying on emails.

## Migrations

```bash
alembic revision --autogenerate -m "describe change"   # then review: CHECKs and partial indexes need hand edits
alembic upgrade head
alembic check                                          # fails if models drift from migrations
```
