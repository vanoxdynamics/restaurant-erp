# Restaurant ERP Backend API (`services/api-fastapi`)

Asynchronous FastAPI backend: REST APIs, authenticated WebSockets, SQLAlchemy 2.x async ORM,
Pydantic v2 schemas, Redis-backed idempotency, rate limiting and realtime fan-out.

## Local development

```bash
# from the repo root: Postgres 16 + Redis 7
docker compose up -d postgres redis

cd services/api-fastapi
python -m venv venv && venv/Scripts/activate   # or source venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env                            # adjust DATABASE_URL / REDIS_URL
alembic upgrade head
python -m app.cli create-owner --username owner --full-name "Owner" --branch-code BLR1 --branch-name "Indiranagar"
uvicorn app.main:app --reload --port 8000
```

Docs: `http://localhost:8000/docs` (disabled when `ENVIRONMENT=production`).
Health: `/health` (liveness), `/health/ready` (DB + Redis; 503 when either is down).

## Configuration

See `.env.example`. Outside `development`/`test`, `SECRET_KEY` and `PIN_PEPPER` must be unique
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

## Tests

Tests use a real Postgres (`restaurant_erp_test`, recreated and migrated each run) and Redis DB 15.
Override with `TEST_DATABASE_URL` / `TEST_REDIS_URL`.

```bash
pytest -q --cov=app
ruff check app tests
mypy app
```

## Migrations

```bash
alembic revision --autogenerate -m "describe change"   # then review: CHECKs and partial indexes need hand edits
alembic upgrade head
alembic check                                          # fails if models drift from migrations
```
