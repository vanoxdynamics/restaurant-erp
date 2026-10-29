# 11 — Analytics, audit log, background work

Fixes: F05 (analytics stubs), F13 (worker/celery), F17 (coverage gate). Depends on: 09.

Files: `app/modules/analytics/{router,service}.py`, `app/worker/tasks.py`, tests, CI config.

## Analytics endpoints (owner, manager; manager limited to own branch; require `amr == "pwd"`)

### `GET /analytics/kpis/live?branch_id=`
All counts in **one round trip** where practical (a single `SELECT` with scalar subqueries, or `asyncio.gather`
is **not** allowed on one `AsyncSession` — sessions aren't concurrency-safe; run sequentially or as one SQL).

- `active_tables_count`: tables `status IN (occupied, billed)`; `vacant_tables_count`: `vacant`, both active tables only.
- `open_orders_count`: orders `status IN (active, billed)` for today's `business_date`.
- `pending_kots_count`: KOTs `status IN (queued, preparing)`.
- `total_sales_today`: `SUM(bills.grand_total)` where `status = paid` and `business_date = today`.
- `average_ticket_size`: `q(total_sales / paid_bill_count)` or `0` when none.
- `timestamp = utcnow()`.
- Cache 10 s in Redis (`kpi:{branch_id}`) — admin apps poll this.

### `GET /analytics/reports/z-report?branch_id=&report_date=`
- `report_date` defaults to today's branch-local business date; future date → 422.
- From **paid** bills on that `business_date`: `gross_sales = SUM(subtotal)`, `total_discounts = SUM(discount_amount)`,
  `net_sales = gross - discounts`, `total_tax_cgst`, `total_tax_sgst`, `total_bills_count`.
- From payments joined to those bills (`status = success`) grouped by `payment_mode` → cash/card/upi.
- `voided_orders_count`: orders `status = cancelled` + bills `status = voided` on that date (report both if
  the schema gets a second field — add `voided_bills_count`).
- Sanity invariant asserted in tests: `cash + card + upi == SUM(grand_total)` of paid bills.
- Once the date is closed (date < today), cache the result for 24 h; `generate_eod_daily_summary` (below)
  computes and persists it.

### `GET /analytics/audit-log?branch_id=&action_type=&from=&to=`
Paginated `AuditLogRead` from `audit_logs` joined to `staff_users` for `actor_name` (and approver name),
newest first. Replace the hard-coded `return []`.

## Background work (decision D4)

The current `app/worker/tasks.py` functions are no-op coroutines, and `celery` was a dependency with no
Celery app (removed in file 01).

- Keep the three functions, typed and documented, as **idempotent** coroutines that FastAPI `BackgroundTasks`
  can run after the response:
  - `deduct_bom_recipe_inventory(order_id)` — leave as a logged no-op with `TODO(inventory)`; there is no
    inventory/BOM model yet. Log at INFO with the order id. Don't fake a "deducted" status.
  - `send_manager_push_alert(branch_id, title, body, payload)` — logged no-op until a push provider is chosen;
    called from KOT void (file 08), high discount and bill void (file 09).
  - `generate_eod_daily_summary(branch_id, report_date)` — calls the Z-report service and stores the JSON in a new
    `daily_reports(branch_id, report_date, payload JSONB, generated_at)` table (unique on branch+date). Expose
    `POST /analytics/reports/z-report/close?branch_id=&report_date=` (manager+) that runs it synchronously.
- Background tasks open their **own** `AsyncSessionLocal()` — never reuse the request session after the response.
- Leave a note in `app/worker/__init__.py`: when a durable queue is needed, adopt ARQ (async, Redis already present).

## Final cleanup (this task closes the plan)

- Grep: `rg "NotImplementedError|return \[\]" app/modules` → no hits.
- Grep: `rg "utcnow\(" app` → only `app/core/clock.py`.
- Remove `get_redis_pool()` warmup call from lifespan if file 01 replaced it with a ping.
- Raise CI coverage gate to `--cov-fail-under=80`.
- Update `services/api-fastapi/README.md`: env vars, bootstrap CLI, auth flow, idempotency header contract,
  WebSocket handshake, how to run tests.
- Regenerate the TS client (`pnpm generate:api`) and list breaking API changes for app owners:
  auth required everywhere, `OrderCreate` without `branch_id`, `OrderUpdate` without `status`/`table_id`,
  add-items body shape, `KOTFireRequest`, `PINAuthRequest.branch_id` required, idempotency header required,
  new WebSocket URL + auth handshake, error envelope `{"error": {...}}`.

## Tests

- KPIs on a seeded day (2 tables occupied, 1 vacant, 3 paid bills, 1 pending KOT) match hand counts; empty day → zeros, no division error.
- Z-report: tender totals equal paid grand totals; bill paid at 00:30 IST counts toward the new day; voided bills excluded from sales.
- Audit log: filters and pagination; manager of another branch → 404.
- `close` endpoint persists one `daily_reports` row; second call is idempotent (upsert).
- Background task uses its own session (assert it still works after the request session is closed).

## Done when

All 34 original endpoints are implemented, coverage ≥80%, CI green, README and TS client updated.
