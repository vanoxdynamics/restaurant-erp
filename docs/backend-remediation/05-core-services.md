# 05 — Core services: auth & RBAC, errors, document numbering, idempotency, events, money/clock

Fixes: F02 (mechanism), F03, F04, F12 (rate limiting), F15. Depends on: 03, 04.

Goal: the shared building blocks every module needs, built and tested once.

## 1. Errors — `app/core/errors.py`

```python
class DomainError(Exception): status_code = 400; code = "domain_error"
class NotFound(DomainError): 404, "not_found"
class Conflict(DomainError): 409, "conflict"            # state conflicts, stale version
class InvalidTransition(Conflict): code = "invalid_transition"
class Forbidden(DomainError): 403, "forbidden"
class ApprovalRequired(Forbidden): code = "approval_required"
class Unauthorized(DomainError): 401, "unauthorized"
class TooManyAttempts(DomainError): 429, "too_many_attempts"
```

Each carries `message` and optional `details: dict`. In `app/main.py` register handlers:
- `DomainError` → `{"error": {"code", "message", "details"}}` with its status.
- `sqlalchemy.orm.exc.StaleDataError` → 409 `stale_version`.
- `sqlalchemy.exc.IntegrityError` → 409 `integrity_conflict` (log the constraint name; don't leak SQL).
- Unhandled `Exception` → 500 `internal_error` with request id; log the stack.

## 2. Auth dependencies — `app/core/auth.py` (F02)

```python
bearer = HTTPBearer(auto_error=False)

@dataclass(frozen=True)
class CurrentUser:
    id: UUID; branch_id: UUID | None; role: Role; username: str; full_name: str

async def get_current_user(creds=Depends(bearer), db=Depends(get_db)) -> CurrentUser
```
- Missing/invalid token → `Unauthorized`.
- Load `StaffUser` by `sub`; reject if not found, `is_active` false, or token's `tv` claim ≠ `token_version`
  (lets an admin revoke all tokens by bumping it). Cache the lookup in Redis for 60 s if profiling shows need — not required now.
- Tokens carry claims `role`, `branch_id`, `tv`.

```python
ROLE_RANK = {OWNER: 50, MANAGER: 40, CASHIER: 30, CAPTAIN: 20, WAITER: 10, KITCHEN: 10}

def require_roles(*roles: Role) -> Callable  # dependency: Forbidden unless user.role in roles
def require_branch_access(user: CurrentUser, branch_id: UUID) -> None
    # owners: any branch; everyone else: must equal user.branch_id → else Forbidden (use NotFound for reads to avoid leaking existence)
```

Permission matrix (put it in the module docstring and enforce via `require_roles`):

| Action | Roles |
|---|---|
| Catalog/floor/branch writes, staff CRUD | owner, manager |
| 86 toggle | owner, manager, captain, kitchen |
| Create order / add items / fire KOT | owner, manager, captain, waiter, cashier |
| KOT status update | kitchen, manager, owner |
| KOT void, order cancel | manager+ directly; others with `approver_pin` of a manager |
| Bill create / payments | cashier, manager, owner (captain optional — confirm) |
| Bill void | manager+ (or approver_pin) |
| Shift open/close | cashier, waiter, captain (own shift); manager can close any |
| Analytics, audit log | owner, manager |

### Manager approval helper

```python
async def resolve_approver(db, user: CurrentUser, branch_id: UUID, approver_pin: str | None) -> UUID
```
Returns `user.id` if `ROLE_RANK[user.role] >= MANAGER`; otherwise looks up an active manager/owner in
`branch_id` by `compute_pin_lookup(branch_id, approver_pin)` then `verify_pin`; raises `ApprovalRequired`
if absent or wrong. Rate-limited with the same limiter as login (key `approval:{branch_id}`).

### Login rate limiter — `app/core/rate_limit.py`

Redis fixed-window: `INCR` + `EXPIRE` in a `MULTI` pipeline on `login:{scope}:{key}`; if count >
`LOGIN_MAX_ATTEMPTS` → `TooManyAttempts` with `Retry-After`. Scopes: per `branch_id`+`device_id`/IP for PIN,
per `username` and per IP for password. Clear on success. Write an `AuditLog(LOGIN_LOCKOUT)` the first
time a lockout trips.

## 3. Clock & money — `app/core/clock.py`, `app/core/money.py`

```python
def utcnow() -> datetime: return datetime.now(timezone.utc)
def business_date(branch_tz: str, at: datetime | None = None) -> date
def financial_year(d: date) -> str   # Apr–Mar, e.g. 2026-10-28 → "2627"

CENT = Decimal("0.01")
def q(x: Decimal) -> Decimal: return x.quantize(CENT, rounding=ROUND_HALF_UP)
```
Tests should freeze time via an injectable clock or `time-machine`, not by patching `datetime`.

## 4. Document numbering — `app/core/numbering.py` (F03)

Replace `get_next_daily_sequence` (delete it from `app/core/redis.py` and `app/core/__init__.py`).

```python
async def next_document_number(db: AsyncSession, branch: Branch, doc_type: Literal["order","kot","bill"]) -> str
```
- Period: daily `YYYYMMDD` (branch-local `business_date`) for `order`/`kot`; FY (`financial_year`) for `bill` (decision D3).
- One statement, inside the caller's transaction:
  ```sql
  INSERT INTO document_counters (branch_id, doc_type, period, value) VALUES (:b, :t, :p, 1)
  ON CONFLICT (branch_id, doc_type, period) DO UPDATE SET value = document_counters.value + 1
  RETURNING value
  ```
  Use `sqlalchemy.dialects.postgresql.insert(...).on_conflict_do_update(...)`.
- Format: order `ORD-{CODE}-{YYYYMMDD}-{0001}`, kot `KOT-{CODE}-{YYYYMMDD}-{0001}`,
  bill `{CODE}/{FY}/{000001}` (≤16 chars when `CODE` ≤ 6 — enforce in `BranchCreate` or truncate; confirm D3).
- Because it's in the business transaction, a rollback also rolls back the number → no gaps on failure
  (bills need this for GST). The row lock serializes concurrent issuers per branch/type — fine at restaurant scale.

## 5. Idempotency — rewrite `app/core/idempotency.py` (F04)

Replace the two functions with a guard that stores the **response** and is bound to the caller and payload.

```python
class IdempotencyGuard:
    def __init__(self, redis, *, scope: str, key: str | None, user_id: UUID, payload: BaseModel, required: bool): ...
    async def begin(self) -> CachedResponse | None
    async def complete(self, status_code: int, body: dict) -> None
    async def release(self) -> None
```
- Redis key: `idem:{scope}:{user_id}:{key}`. Value JSON: `{"state": "processing"|"done", "fingerprint": sha256(payload.model_dump_json()), "status": int, "body": {...}}`.
- `begin()`:
  - `key is None` and `required` → `DomainError(400, "idempotency_key_required")`.
  - `SET NX EX 60` with state processing. If it existed:
    - fingerprint differs → 422 `idempotency_key_reused`.
    - `processing` → 409 `request_in_progress` (+ `Retry-After: 1`).
    - `done` → return the cached response; the route returns it with the original status code.
- `complete()` → overwrite with state done, TTL 24 h. Call it **after** `db.commit()` succeeds.
- `release()` → `DEL` the key so the client can retry. Call it in an `except` around the service call.
- Provide a dependency factory so routes stay small:

```python
def idempotent(scope: str, *, required: bool = True):
    async def dep(idempotency_key: str | None = Header(None, alias="X-Idempotency-Key", max_length=100),
                  redis=Depends(get_redis), user=Depends(get_current_user)) -> IdempotencyFactory: ...
```

Route pattern (document it in the module docstring):

```python
cached = await guard.begin()
if cached: return JSONResponse(cached.body, status_code=cached.status)
try:
    result = await service.do_thing(db, user, payload)   # service commits
except Exception:
    await guard.release(); raise
body = Schema.model_validate(result).model_dump(mode="json")
await guard.complete(201, body)
return JSONResponse(body, status_code=201)
```

Required on: `POST /orders`, `POST /kots/fire`, `POST /billing/bills`, `POST /billing/bills/{id}/payments`,
`POST /billing/bills/{id}/split-payments`. Optional elsewhere. As a DB-level backstop, payments also
store `idempotency_key` with a unique index `(bill_id, idempotency_key)` (file 03).

## 6. Domain events — `app/core/events.py`

```python
async def publish_after_commit(session: AsyncSession, branch_id: UUID, topic: Topic, event: dict) -> None
```
Appends to `session.info["pending_events"]`. Register a SQLAlchemy `after_commit` listener on
`AsyncSessionLocal`'s sync session class that schedules `realtime_manager.publish(...)` via
`asyncio.get_running_loop().create_task(...)` for each pending event, and an `after_rollback` listener
that clears them. Events are never published for rolled-back work. `Topic` is a `StrEnum`:
`orders`, `kots`, `tables`, `billing`, `catalog`. File 10 implements `publish`; until then it can be a no-op stub.

Event envelope: `{"type": "kot.fired", "branch_id": ..., "entity_id": ..., "data": {...}, "ts": iso8601}`.

## 7. Audit helper — `app/core/audit.py`

`def record_audit(db, *, branch_id, action, actor_id, approver_id=None, entity_type, entity_id, amount=None, details={})`
— adds an `AuditLog` row to the current session (no commit).

## Tests (`tests/core/`)

- **auth:** no token 401; expired 401; inactive user 401; bumped `token_version` 401; waiter on
  manager-only route 403; manager of branch A reading branch B → 404.
- **resolve_approver:** manager caller passes without PIN; waiter + correct manager PIN passes and returns
  the manager id; waiter + wrong PIN → 403 approval_required; waiter + another waiter's PIN → 403.
- **rate limit:** 6th bad attempt → 429; success resets.
- **numbering:** two branches same day get independent `0001`; 50 concurrent `asyncio.gather` calls in
  separate sessions produce 50 distinct consecutive numbers; rollback doesn't consume a number;
  23:59 vs 00:01 IST fall on different days (not UTC); bill counter resets on 1 April.
- **idempotency:** replay returns identical body + status and does not create a second row; same key,
  different payload → 422; failure releases the key so retry succeeds; same key from two users doesn't collide;
  missing key on required route → 400.
- **events:** event published after commit; nothing published after rollback.
- **money:** `q(Decimal("2.345")) == Decimal("2.35")`.

## Done when

All core tests pass; nothing in `app/` imports `get_next_daily_sequence`, `verify_idempotency_key` or
`mark_idempotency_completed`; every router in `app/modules/` has `dependencies=[Depends(get_current_user)]`
at the `APIRouter(...)` level except `staff` login routes (temporary until each module task adds role checks).
