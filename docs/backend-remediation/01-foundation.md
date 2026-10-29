# 01 — Foundation: dependencies, config, security libs, DB session, app wiring, Docker

Fixes: F01, F07, F11, F13, F14, F15, F16, F19 (config/env parts). Depends on: nothing.

Goal: the app boots safely, hashing and JWT work on current library versions, and the DB session
has a clear commit model. No endpoint behavior changes in this task.

## 1. Dependencies (`pyproject.toml`, `requirements.txt`)

- Replace `python-jose[cryptography]` with `pyjwt>=2.9,<3`.
- Replace `passlib[bcrypt]` with `pwdlib[argon2,bcrypt]>=0.2,<0.3` (argon2 for new hashes; the bcrypt
  verifier keeps any existing bcrypt hashes readable).
- Remove `celery` (decision D4; see file 11).
- Add upper bounds to every dependency at the next major (e.g. `fastapi>=0.115,<1`,
  `sqlalchemy[asyncio]>=2.0.30,<2.2`, `redis[hiredis]>=5,<6`, `pydantic>=2.7,<3`).
- Add to `dev`: `pytest-cov`, `asgi-lifespan`, `factory-boy` (or plain fixtures—see file 02), `freezegun` or `time-machine`.
- Delete `requirements.txt` **or** make it a generated lock file
  (`pip-compile pyproject.toml -o requirements.lock`) — pick one source of truth. Preferred: `uv lock`
  / `pip-tools` lock checked in, Dockerfile installs from the lock.
- Add `[tool.mypy]` (`strict = true` for `app/core`, `plugins = ["pydantic.mypy"]`) and extend
  `[tool.ruff.lint] select = ["E","F","I","B","UP","ASYNC","DTZ"]` (DTZ catches naive datetimes).

## 2. `app/core/security.py`

Rewrite with pwdlib + PyJWT. Keep the existing function names so imports in `app/core/__init__.py` stay valid.

```python
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from pwdlib.hashers.bcrypt import BcryptHasher

password_hash = PasswordHash((Argon2Hasher(), BcryptHasher()))
```

- `verify_password` / `verify_pin` → `password_hash.verify(...)`; return `False` (don't raise) on a
  malformed hash.
- `create_access_token(subject, claims=None, expires_delta=None)`:
  - `now = datetime.now(timezone.utc)`; payload includes `sub`, `iat`, `exp`, `jti` (uuid4 hex),
    `iss = settings.JWT_ISSUER`, plus `claims`.
  - `jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)`.
- `decode_access_token(token)`: `jwt.decode(..., algorithms=[settings.ALGORITHM], issuer=settings.JWT_ISSUER,
  options={"require": ["exp", "sub", "iat"]})`; return `None` on `jwt.PyJWTError`.
- Add `compute_pin_lookup(branch_id: uuid.UUID, pin: str) -> str` =
  `hmac.new(settings.PIN_PEPPER.encode(), f"{branch_id}:{pin}".encode(), sha256).hexdigest()`.
  Used by file 03/06 for O(1) PIN lookup (F12).

## 3. `app/core/config.py`

- Add: `JWT_ISSUER: str = "restaurant-erp"`, `PIN_PEPPER: str`, `DEFAULT_TIMEZONE: str = "Asia/Kolkata"`,
  `DB_ECHO: bool = False` (decouple SQL echo from `DEBUG`), `DISCOUNT_APPROVAL_THRESHOLD_PERCENT: Decimal = Decimal("10")`,
  `LOGIN_MAX_ATTEMPTS: int = 5`, `LOGIN_LOCKOUT_SECONDS: int = 300`.
- Change defaults: `DEBUG: bool = False`, `ENVIRONMENT: Literal["development","test","staging","production"] = "development"`.
- `SECRET_KEY` and `PIN_PEPPER`: keep a dev default **only** when `ENVIRONMENT in {"development","test"}`.
  Add a `@model_validator(mode="after")` that raises if, outside dev/test, either is the default or
  shorter than 32 chars, or `DEBUG` is true.
- `env_file`: resolve relative to the service dir, not CWD:
  `env_file=Path(__file__).resolve().parents[2] / ".env"`.
- Fix `ALLOWED_ORIGINS` typing: `List[str]` with `Annotated[..., NoDecode]` (pydantic-settings ≥2.7) so
  the comma-split validator is the only parser.
- Update `.env.example` (service + root) with every new key and a comment on generating secrets
  (`python -c "import secrets; print(secrets.token_urlsafe(48))"`).

## 4. `app/core/database.py` (F07)

- `echo=settings.DB_ECHO`. Drop `future=True` (no-op in 2.x).
- `get_db` must **not** commit. New contract: services commit explicitly; the dependency only rolls
  back on error.

```python
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
```

- Add `async def dispose_engine() -> None: await engine.dispose()`.

## 5. `app/main.py` (F16)

- Lifespan: on startup `SELECT 1` via engine and `PING` Redis; log and fail fast if either is down.
  On shutdown: stop the realtime subscriber (file 10 adds it), `close_redis_pool()`, `dispose_engine()`.
- Disable docs outside dev: `docs_url="/docs" if settings.ENVIRONMENT != "production" else None` (same for redoc/openapi).
- `/health` → liveness (no I/O). Add `/health/ready` → checks DB + Redis, returns 503 on failure.
- Install logging config (stdlib `logging.config.dictConfig`, JSON formatter optional) in `app/core/logging.py`.
- Add a request-id middleware (`X-Request-ID` in/out, put in log records).
- Exception handlers are added in file 05 (they depend on `app/core/errors.py`).

## 6. Dockerfile (F14)

```dockerfile
FROM python:3.11-slim AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
COPY requirements.lock ./
RUN pip install -r requirements.lock
COPY . .
RUN pip install --no-deps . && useradd --create-home appuser
USER appuser
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
```

- `gcc`/`libpq-dev` aren't needed (asyncpg ships wheels). `--reload` moves to `docker-compose.yml`
  as a `command:` override for local dev only.
- Add `.dockerignore`: `venv/`, `.env`, `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`.
- In `docker-compose.yml`: add `command: sh -c "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"`,
  pass `PIN_PEPPER`, add a `healthcheck` hitting `/health/ready`.

## 7. `alembic.ini`

Replace the hard-coded `sqlalchemy.url` with an empty value and a comment; `env.py` already sets it from settings.

## 8. Small cleanups

- Remove unused `Header` import in `app/core/idempotency.py` (file 05 rewrites it anyway).
- Replace every `datetime.utcnow()` with `datetime.now(timezone.utc)`.

## Tests (add with file 02's harness; for now a minimal `tests/test_security.py` runs without DB)

- hash → verify round-trip for password and PIN; wrong value fails; malformed hash returns `False`.
- token round-trip; expired token → `None`; token signed with another key → `None`; token missing `exp` → `None`.
- `compute_pin_lookup` is deterministic and differs across branches for the same PIN.
- Settings: `ENVIRONMENT=production` with default `SECRET_KEY` raises `ValidationError`.

## Done when

- `python -c "from app.core.security import get_pin_hash; get_pin_hash('1234')"` succeeds.
- `docker compose build api-fastapi` succeeds and the container runs as non-root.
- `/health/ready` returns 503 with Redis stopped, 200 with it up.
- `ruff check app` and `mypy app/core` clean.
