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

## Inventory

A paid module (`inventory`, switched on per plan or per subscriber in the platform console). Every endpoint under `/inventory`
answers `403 module_disabled` without it. Code: `app/modules/inventory`.

- **Stock is per branch and per item.** A branch tracks an item once it has a stock level for it: the first delivery,
  adjustment or count creates one. Untracked items sell as before. An item's variants share its stock. Quantities are in the
  item's unit with 3 decimals; items not `sold_by_weight` are counted in whole numbers (`422 whole_quantity_required`).
- **Sales.** When a bill is fully paid, its order's tracked items leave stock in the same transaction (cancelled lines do
  not). A sale is never refused for lack of stock: the quantity goes negative and the item shows as out. An order is
  deducted once, also when a card payment is reversed and collected again. Voiding an unpaid bill changes nothing, because
  nothing left stock yet.
- **`GET /inventory/stock`** (any signed-in staff; `branch_id`, `low_only`, `include_untracked`, `search`): `quantity`,
  `low_stock_level`, `is_low`, `is_out`, and for owners and managers `cost_price` and `stock_value`.
- **`PUT /inventory/stock/{item_id}`** (managers): `is_tracked`, `low_stock_level` (null clears it), `cost_price`.
- **`POST /inventory/purchases`** (managers, needs `X-Idempotency-Key`): a delivery with `supplier_name`, `invoice_number`,
  `received_on` and `lines: [{item_id, quantity, unit_cost}]`. Each line adds to stock; `unit_cost` becomes the item's cost
  price. Purchases are not edited: correct a mistake with an adjustment. `GET /inventory/purchases`, `GET
  /inventory/purchases/{id}`.
- **`POST /inventory/adjustments`** (managers, needs `X-Idempotency-Key`): `item_id`, `reason`, and exactly one of `delta`
  (add or remove) or `counted` (a physical count; the quantity on record becomes this).
- **`GET /inventory/movements`** (managers; `item_id`, `kind`): the ledger, newest first. Every change is one row with
  `kind` (`purchase`, `sale`, `adjustment`, `count`), a signed `quantity` and `balance_after`. Rows are never edited.
- **Events** on the `catalog` topic: `stock.changed` (`item_ids`) after any change, and `stock.low` once when an item
  reaches its low-stock level.

Not built yet: suppliers as records, purchase orders, stock transfers between branches, returns to stock (comes with
returns and credit notes), batches and expiry, recipes for restaurant ingredients.

## Customers

Part of the core product (no module needed). A customer belongs to the business, not a branch, so every branch finds the same
person. Code: `app/modules/customers`.

- **Fields:** `name` (required), `phone`, `email`, `gstin`, `address`, `notes`, `is_active`. Phone numbers are stored as digits:
  `+91 98765 43210` and `098765-43210` both become `9876543210`; other countries keep their code. **One phone number, one
  customer per business**: a second one is refused with `409 customer_phone_taken`, whose `details` (`customer_id`, `name`,
  `is_active`) let the app offer the existing customer instead.
- **`GET /customers`** (owner, manager, captain, waiter, cashier; `search`, `include_inactive`, `limit`, `offset`): by name.
  `search` matches part of a name or an exact GSTIN, or part of the phone number when it is digits. Each row carries `visits`
  (paid bills), `total_spent` and `last_visit_at`.
- **`POST /customers`**, **`GET /customers/{id}`**, **`PATCH /customers/{id}`** (same roles). A patch changes only the fields
  sent; null clears one. Customers are never deleted: `is_active: false` hides one (owners and managers only).
- **`GET /customers/{id}/bills`** (owners and managers): the customer's bills, newest first, across branches.
- **On an order:** send `customer_id` with `POST /orders`, or set it later with **`PUT /orders/{id}/customer`**
  (`{"customer_id": "..."}`; null takes the customer off). It works after billing too, because people ask for their name or
  GSTIN once they see the bill. `OrderRead.customer_id` returns it. A hidden customer, or one from another business, is
  refused with `422 customer_not_found`.
- **On the bill:** the receipt prints `Customer:`, `Ph:` and the customer's `GSTIN:` under the cashier line. The bill reads the
  customer from its order, so a reprint shows a later correction.

Not built yet: customer credit (khata), loyalty points, sending the bill by SMS or WhatsApp, merging duplicate customers, and a
snapshot of the buyer's GSTIN on the bill for GST returns (comes with the accountant export).

## Business name and logo on bills

The business sets how its bills look (owners and managers): `GET/PATCH /business/profile` (`display_name`; null goes back to
the registered name) and `PUT/DELETE /business/logo` (a PNG or JPEG under 500 KB, base64). Bills print the business name large
with the branch name under it; the HTML bill shows the logo at the top in black and white, and `GET
/billing/bills/{id}/receipt` returns `logo_data_url` for printers that can print images. The text rows for line printers (the
Pine Labs device) do not include the logo yet.

## Devices and offline groundwork

Phase 1 of offline billing ([pos-devices/05-offline-billing-plan.md](pos-devices/05-offline-billing-plan.md), build spec
[06](pos-devices/06-offline-phase-1-spec.md)): the groundwork only. **Nothing bills offline yet**, and with no switch turned on
every existing endpoint answers as before (the only new field on an existing response is `OrderRead.client_sale_id`).

- **Module `offline_billing`** (depends on `pos`; the internal all-modules plan has it, other plans do not). It is switch 1 of
  three: the business's own switch and the device's switch must also be on before a device gets a lease.
- **Sale ids on orders.** `POST /orders` takes an optional `client_sale_id` (`^[A-Za-z0-9_-]{8,64}$`). The database keeps it
  unique per branch: the same id sent again, by any cashier, at any time, with any items, returns the first order with `201`
  (no new number, token or events). Two requests racing with one id also get one order. `X-Idempotency-Key` is still required
  and works as before; without `client_sale_id` nothing changes.
- **Devices are records.** Linking (`POST /staff/devices/link`) creates the device's record; a device linked before records
  existed gets one on its first request. A `device_id` already used by a revoked device or by another branch is refused with
  `409 device_id_taken`, and the setup code stays usable.
  - `GET /staff/devices?branch_id=&include_revoked=false`: the branch's devices, newest first (owners and managers with a
    password session; a branch the caller cannot act on is `403`).
  - `PATCH /staff/devices/{id}` (`name`, `offline_enabled`; only the fields sent change). Switching offline on needs, in this
    order: a device that is not revoked (`409 device_revoked`), the module (`403 module_disabled`), and a branch code of at most
    4 characters (`422 branch_code_too_long`), because a device's bill numbers are `{CODE}{LETTER}/{FY}/{00001}`
    (`BLR1A/2627/00001`) and GST allows 16 characters. The first time, the device is given a **series letter** `A`–`Z`: the
    first one no device of the branch has ever had, revoked ones included (`409 no_series_left` after 26). Switching off keeps
    the letter; it is never changed or reused.
  - `POST /staff/devices/{id}/revoke`: cuts the device off for good (revoking again returns the record). Its token is then
    refused with `401 device_revoked` by the sign-in roster, the print-station endpoints and the lease. To use the device again,
    link it with a new setup code and a new `device_id`.
  - Another business's device is `404` on patch and revoke and never listed.
  - Requests from a device update its `last_seen_at` at most every 5 minutes.
- **The business's switch and limits.** `GET /business/offline` (owners and managers) returns `offline_allowed` (default
  `false`), `offline_max_hours` (24, 1–72), `offline_max_sales` (300, 1–2000), `offline_max_cash` (`"50000.00"`, above 0 and at
  most 500000) and `module_enabled`. `PATCH /business/offline` is **owners only** and changes only the fields sent; turning
  `offline_allowed` on without the module is `403 module_disabled`, while changing limits or turning it off is always allowed.
  These are not part of `/me/entitlements`.
- **The lease.** `POST /staff/devices/lease`, authenticated by `X-Device-Token` only. Refused with `403 offline_not_allowed` and
  `details.reason`, checked in this order: `subscription_inactive` (read-only or suspended), `module_disabled` (also the
  platform-wide kill switch), `business_disabled`, `device_disabled`. Otherwise it returns `lease` (a signed token: `typ:
  "offline_lease"`, the device as subject, `branch_id`, `org`, `series`, `max_sales`, `max_cash`; valid for `offline_max_hours`),
  `device_id`, `branch_id`, `series_letter`, `issued_at`, `expires_at`, `max_sales`, `max_cash` and `server_time`. Each call gives
  a fresh lease and nothing is stored. A lease is never accepted as a login or as a device token.
- **Known gap, unchanged in this phase:** `POST /staff/auth/pin` does not look at the device record. A revoked device cannot
  fetch the staff list, so the app cannot reach its PIN pad, but the endpoint itself still answers anyone who sends a branch, a
  staff id and a PIN, as before.

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
