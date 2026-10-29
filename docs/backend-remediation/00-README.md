# Backend Remediation Plan — `services/api-fastapi`

Review date: 2026-10-28. Scope: everything under `services/api-fastapi/` (app, alembic, Dockerfile, deps).

## TL;DR

The service is a **scaffold**, not a working backend. Of 34 HTTP endpoints, **25 raise
`NotImplementedError` (HTTP 500)** and **9 list endpoints return a hard-coded `[]`**. The
foundations under those stubs also have defects that would make a straight "fill in the stubs"
effort ship bugs: password/PIN hashing crashes at runtime, there is no authentication at all,
document numbers collide across branches, and the idempotency guard locks out legitimate retries.

Fix the foundations first (files 01–05), then implement modules on top (06–11).

## Verified on the installed venv

| Check | Result |
|---|---|
| `get_pin_hash("1234")` | **ValueError: password cannot be longer than 72 bytes** (bcrypt 5.0.0 + passlib 1.7.4) |
| `GET /api/v1/orders/{id}` | **500** (NotImplementedError) |
| `GET /api/v1/catalog/items` | 200 `[]` (fake) |
| `tests/` directory | does not exist |

## Findings

Severity: **C** = broken / security hole, **H** = will cause bugs in production, **M** = should fix, **L** = cleanup.

| ID | Sev | Finding | Where | Fixed in |
|---|---|---|---|---|
| F01 | C | Password/PIN hashing crashes: passlib 1.7.4 is incompatible with bcrypt ≥4.1/5.0 (installed: 5.0.0). No staff can be created or log in. | `app/core/security.py:7` | 01, 05 |
| F02 | C | No authentication or authorization on any HTTP or WebSocket route. Roles exist in the model but are never checked. `POST /staff` is open, so anyone can create an `owner`. | all routers | 05, 06 |
| F03 | C | `get_next_daily_sequence` puts `branch_id` in the Redis key but **not in the returned number** (`KOT-20261028-0001`). `order_number`, `kot_number`, `bill_number` are globally unique, so the second branch to fire a KOT each day hits a unique violation. Day boundary is UTC (05:30 IST); Redis flush/restart resets counters → duplicates; `INCR`+`EXPIRE` not atomic. | `app/core/redis.py:38-49` | 03, 05 |
| F04 | C | Idempotency guard: key is set to `processing` and nothing ever completes or releases it, so any failure locks retries out with 409 for 120 s. A completed replay returns **400** instead of the original response. Key is optional on money endpoints and not bound to caller or payload. | `app/core/idempotency.py` | 05 |
| F05 | C | 25 endpoints raise `NotImplementedError` → 500; 9 list endpoints return `[]` regardless of data. | all `modules/*/router.py` | 06–11 |
| F06 | H | WebSocket: unauthenticated, any client can join any `channel_id`, server echoes client input, connections leak on non-`WebSocketDisconnect` errors, dead sockets never pruned, sends are sequential (one slow tablet stalls the channel), `publish_redis` exists but nothing subscribes, so multi-worker fan-out does not work. | `app/realtime/*` | 10 |
| F07 | H | `get_db` commits implicitly in dependency teardown, after the handler has returned. Commit errors surface outside the handler and can't be paired with idempotency/event publishing. | `app/core/database.py:23-33` | 01 |
| F08 | H | Response models nest relationships (`BranchRead.floors[].tables`, `CategoryRead.items[].variants`, `OrderRead.items`, `BillRead.payments`, `KOTRead.items`). Serializing async ORM objects without eager loading raises `MissingGreenlet`. Latent today because nothing is queried yet. | `app/schemas/*` | 04, 07–09 |
| F09 | H | Schemas don't validate: status/station/payment_mode/role/order_type are free strings; prices, tax rates, discounts can be negative; `Decimal` precision unbounded vs `Numeric(10,2)` (DB error → 500); empty item lists accepted; KOT `item_name` is client-supplied (tamperable); PIN not digits-only; `OrderUpdate.status` lets a client mark an order `completed` without billing. | `app/schemas/*` | 04 |
| F10 | H | Data model gaps: no FK on `orders.waiter_id`, `order_items.variant_id`, `dining_tables.merged_into_table_id`; modifiers are not linked to menu items or order lines (unusable); no `station` on menu items (KOT station-splitting impossible); no audit-log table behind `FraudAuditLogRead`; no CHECK constraints on statuses; no "one open shift per user" / "one active bill per order" / "table number unique per floor"; `Category → MenuItem` cascade `delete-orphan` destroys rows referenced by order history. | `app/models/*` | 03 |
| F11 | H | Insecure defaults: hard-coded `SECRET_KEY` default, `DEBUG=True` default turns on SQL echo (logs hashes/PII), `/docs` public, no production guard. `python-jose` is unmaintained with known CVEs (CVE-2024-33663, CVE-2024-33664); `passlib` is unmaintained. | `app/core/config.py`, `security.py`, `pyproject.toml` | 01 |
| F12 | H | PIN login can't work as designed: bcrypt hashes are salted, so a user can't be looked up by PIN — the server would have to hash-verify every staff member per attempt (~100 ms each). No brute-force protection on a 10⁴–10⁶ keyspace. | `staff/router.py:21`, `models/staff.py:19` | 03, 05, 06 |
| F13 | M | Dependencies have lower bounds only (this is how bcrypt 5 slipped in); `requirements.txt` duplicates `pyproject.toml`; `celery` declared but there's no Celery app — worker tasks are plain no-op coroutines. | `pyproject.toml`, `requirements.txt`, `app/worker/` | 01, 11 |
| F14 | M | Dockerfile runs `pip install .` before copying `README.md`/`app/` → hatchling build fails; image runs `--reload` as root; no migration step. | `Dockerfile` | 01 |
| F15 | M | Naive `datetime.utcnow()` (deprecated since 3.12). Business date should be branch-local (Asia/Kolkata), not UTC. | `security.py:32`, `redis.py:44` | 01, 05 |
| F16 | M | Lifespan doesn't check DB/Redis or dispose the engine; `/health` always says healthy. | `app/main.py` | 01 |
| F17 | M | No tests, no structured logging, no exception handlers, no pagination on list endpoints. | — | 02, 07–11 |
| F18 | M | Optimistic lock is a manual `version` int; nothing increments or checks it. Use SQLAlchemy `version_id_col`. | `models/orders.py:22` | 03, 08 |
| F19 | L | `category_id: uuid.UUID = None` (should be `Optional`); unused imports/schemas (`Header` in idempotency, `mark_idempotency_completed` in orders, `BlindDropRequest`, `MenuItemUpdate`, `ModifierGroupCreate` have no routes); `env_file=".env"` resolves relative to CWD (root vs service `.env`); `alembic.ini` hard-codes a DB URL. | various | 01, 04, 07 |

## Plan files — execution order

| File | Title | Depends on | Can run in parallel with |
|---|---|---|---|
| [01-foundation.md](01-foundation.md) | Dependencies, config, security libs, DB session, app wiring, Docker | — | — |
| [02-test-harness.md](02-test-harness.md) | pytest + real Postgres/Redis fixtures + factories | 01 | — |
| [03-data-model-migration.md](03-data-model-migration.md) | Enums, constraints, missing FKs/tables, new Alembic revision | 02 | 04 |
| [04-schemas-validation.md](04-schemas-validation.md) | Pydantic validation, enums, response models | 03 (enums) | — |
| [05-core-services.md](05-core-services.md) | Auth deps & RBAC, document numbering, idempotency, event publisher, errors | 03, 04 | — |
| [06-staff-auth-shifts.md](06-staff-auth-shifts.md) | Login (password + PIN), staff CRUD, shifts | 05 | 07, 10 |
| [07-catalog-floor.md](07-catalog-floor.md) | Catalog + floor endpoints | 05 | 06, 10 |
| [08-orders-kots.md](08-orders-kots.md) | Order lifecycle + KOT firing | 05, 07 | 10 |
| [09-billing.md](09-billing.md) | Tax engine, bills, payments, UPI QR | 08 | 10 |
| [10-realtime.md](10-realtime.md) | Authenticated WebSockets + Redis pub/sub fan-out | 05 | 06–09 |
| [11-analytics-worker.md](11-analytics-worker.md) | KPIs, Z-report, audit log, worker cleanup | 09 | — |

Run each file as one agent task / one PR. Don't start a file until its dependencies are merged.

## Global rules for the coding agent

1. **Layering.** Routers stay thin: parse input, call a service function, return. Business logic lives
   in `app/modules/<module>/service.py`. Services take `AsyncSession` plus the current user, and
   **commit explicitly**.
2. **Async SQLAlchemy.** Never touch a relationship that wasn't eager-loaded. Use `selectinload(...)`
   for every relationship a response model serializes. `lazy="raise"` is set on relationships
   (file 03) so mistakes fail loudly in tests.
3. **Money.** Always `Decimal`, never `float`. Quantize to `Decimal("0.01")` with `ROUND_HALF_UP` only
   at defined points (line total, tax per bill, grand total). Helpers live in `app/core/money.py` (file 05).
4. **Time.** Always timezone-aware: `datetime.now(timezone.utc)`. Business date comes from
   `app/core/clock.py::business_date(branch)` (file 05).
5. **Errors.** Raise domain exceptions from `app/core/errors.py` (file 05); never `HTTPException` inside
   services, never bare `NotImplementedError` in a route.
6. **Tests are part of done.** Every file lists tests; a task isn't done until `pytest` passes and
   `ruff check` + `mypy app` are clean.
7. **Migrations.** Any model change ships with an Alembic revision. Never edit the initial revision
   `e54e65c309dc`.
8. **Match the code around you.** Same import style, type hints on everything, docstrings like the
   existing ones.

Commands (from `services/api-fastapi/`):

```bash
docker compose -f ../../docker-compose.yml up -d postgres redis
pytest -q
ruff check app tests
mypy app
alembic upgrade head
```

## Decisions to confirm before starting

These change the design. The plan uses the **default** shown; change it here before handing off.

| # | Question | Default used in this plan |
|---|---|---|
| D1 | Is the menu global or per-branch? `categories`/`menu_items` have no `branch_id`. | Global menu, with per-branch availability deferred. |
| D2 | PIN login UX: tablet sends PIN only, or staff picks their name tile then enters PIN? | PIN only, scoped by `branch_id`; PIN unique per branch (file 03/06). |
| D3 | Bill number format. GST rules require invoice numbers to be unique per financial year and at most 16 characters; confirm with the accountant. | `{BRANCHCODE}{FY}{seq}` e.g. `BLR1/2627/000123` for bills (≤16 chars, per-FY counter); daily counters for orders/KOTs. |
| D4 | Background jobs: Celery, ARQ, or none yet? | Remove Celery; use FastAPI `BackgroundTasks` for the stubs until a real worker is needed (file 11). |
| D5 | Discount approval threshold. | Discount > 10% of subtotal needs manager approval (configurable). |
