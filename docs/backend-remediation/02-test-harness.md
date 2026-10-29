# 02 — Test harness

Fixes: F17 (tests). Depends on: 01.

Goal: every later task can write fast, isolated, async integration tests against **real Postgres and
Redis** (the code relies on Postgres-specific behavior — `ON CONFLICT`, `FOR UPDATE`, UUID — so
SQLite is not acceptable).

## Layout

```
services/api-fastapi/tests/
  conftest.py          # engine, session, redis, client, auth helpers
  factories.py         # async builders for branches, floors, tables, staff, menu, orders
  core/
  modules/<module>/
```

## `conftest.py`

1. **Test settings.** Set `ENVIRONMENT=test`, `DATABASE_URL` → a dedicated `restaurant_erp_test` DB,
   `REDIS_URL` → `redis://localhost:6379/15` *before* importing `app`. Read overrides from
   `TEST_DATABASE_URL` / `TEST_REDIS_URL` env vars for CI.
2. **Schema once per session.** Session-scoped fixture: create the test DB if missing, run
   `alembic upgrade head` programmatically (`alembic.command.upgrade(Config("alembic.ini"), "head")`).
   Using Alembic (not `metadata.create_all`) means migrations are tested too.
3. **Per-test isolation.** Function-scoped `db_session` fixture: open a connection, begin an outer
   transaction, bind an `AsyncSession` with `join_transaction_mode="create_savepoint"`, yield, roll back
   the outer transaction. Services may call `commit()` freely — it only releases a savepoint.
4. **Redis.** Function-scoped `redis_client` on DB 15; `FLUSHDB` after each test.
5. **App client.** `client` fixture: `httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")`,
   with `app.dependency_overrides[get_db]` and `[get_redis]` pointing at the fixtures above. Wrap with
   `asgi_lifespan.LifespanManager` only in tests that need lifespan (realtime).
6. **Auth helpers.** `auth_headers(user) -> dict` builds a Bearer token with `create_access_token`;
   fixtures `owner`, `manager`, `cashier`, `waiter`, `kitchen` (each a `StaffUser` in `branch`).
7. `pytest.ini_options`: `asyncio_mode = "auto"`, `asyncio_default_fixture_loop_scope = "session"`,
   `filterwarnings = ["error::DeprecationWarning:app.*"]` (catches `utcnow` regressions).

## `factories.py`

Plain async functions (no factory-boy dependency needed):

```python
async def make_branch(db, *, code="BLR1", name="Indiranagar") -> Branch: ...
async def make_floor(db, branch, *, name="Ground") -> Floor: ...
async def make_table(db, floor, *, number="T1", capacity=4) -> DiningTable: ...
async def make_staff(db, branch, *, role="waiter", pin="1234", password=None) -> StaffUser: ...
async def make_menu_item(db, *, price="250.00", tax_rate="5.00", station="kitchen", variants=()) -> MenuItem: ...
async def make_order(db, branch, *, table=None, waiter=None, items=()) -> Order: ...
```

Each `flush()`es and returns the object. Use sensible defaults so tests only state what matters.

## CI

Add `.github/workflows/api.yml` (or document the command if no CI exists yet): services `postgres:16-alpine`
and `redis:7-alpine`, then `pip install -e .[dev]`, `ruff check`, `mypy app`, `pytest --cov=app --cov-fail-under=80`.
Coverage threshold starts at 0 in this task and is raised to 80 in file 11.

## Smoke tests in this task

- `tests/test_health.py`: `/health` 200; `/health/ready` 200.
- `tests/test_migrations.py`: `alembic upgrade head` then `downgrade base` then `upgrade head` succeeds.
- Move `tests/test_security.py` from file 01 under `tests/core/`.

## Done when

`pytest -q` passes locally against docker-compose Postgres/Redis, and two tests writing the same
unique value in different test functions don't conflict (proves rollback isolation).
