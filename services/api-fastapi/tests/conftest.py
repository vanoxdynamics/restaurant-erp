"""
Test harness: real Postgres (dedicated test database, migrated with Alembic) and real Redis (DB 15).

Tests commit for real, like production; every table is truncated and Redis flushed after each test.
Override targets with TEST_DATABASE_URL / TEST_REDIS_URL.
"""

import os
import subprocess
import sys
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parents[1]


def _dotenv_value(key: str) -> str | None:
    env_file = SERVICE_DIR / ".env"
    if not env_file.exists():
        return None
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"')
    return None


_base_db_url = os.environ.get("DATABASE_URL") or _dotenv_value("DATABASE_URL") or \
    "postgresql+asyncpg://postgres:postgres@localhost:5432/restaurant_erp"
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or _base_db_url.rsplit("/", 1)[0] + "/restaurant_erp_test"
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")

os.environ.update({
    "ENVIRONMENT": "test",
    "DATABASE_URL": TEST_DATABASE_URL,
    "REDIS_URL": TEST_REDIS_URL,
    "DB_NULL_POOL": "true",
    "DB_ECHO": "false",
    "DEBUG": "false",
})

import asyncpg  # noqa: E402
import httpx  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app.core import redis as redis_module  # noqa: E402
from app.core.database import AsyncSessionLocal  # noqa: E402
from app.core.events import set_publisher  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402
from tests import factories  # noqa: E402


async def _recreate_database() -> None:
    url = make_url(TEST_DATABASE_URL)
    conn = await asyncpg.connect(host=url.host, port=url.port, user=url.username, password=url.password,
                                 database="postgres")
    try:
        await conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1 AND pid <> pg_backend_pid()",
            url.database,
        )
        await conn.execute(f'DROP DATABASE IF EXISTS "{url.database}"')
        await conn.execute(f'CREATE DATABASE "{url.database}"')
    finally:
        await conn.close()


def _alembic(*args: str) -> None:
    subprocess.run([sys.executable, "-m", "alembic", *args], cwd=SERVICE_DIR, check=True,
                   env=os.environ.copy(), capture_output=True)


@pytest.fixture(scope="session", autouse=True)
async def migrated_database():
    await _recreate_database()
    _alembic("upgrade", "head")
    yield


TABLES = ", ".join(f'"{t.name}"' for t in reversed(Base.metadata.sorted_tables))


@pytest.fixture(autouse=True)
async def clean_state(migrated_database):
    yield
    async with AsyncSessionLocal() as session:
        await session.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
        await session.commit()
    client = redis_module.redis_client()
    try:
        await client.flushdb()
    finally:
        await client.aclose()
    await redis_module.close_redis_pool()


@pytest.fixture
async def db():
    async with AsyncSessionLocal() as session:
        yield session


@pytest.fixture
async def redis_client():
    client = redis_module.redis_client()
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def events():
    """Capture events published after commit."""
    captured: list = []

    async def publisher(branch_id, topic, envelope):
        captured.append({"branch_id": branch_id, "topic": topic, **envelope})

    set_publisher(publisher)
    yield captured
    set_publisher(None)


# --- Domain fixtures -----------------------------------------------------------


@pytest.fixture
async def branch(db):
    return await factories.make_branch(db)


@pytest.fixture
async def other_branch(db):
    return await factories.make_branch(db, code="MUM1", name="Bandra")


@pytest.fixture
async def floor(db, branch):
    return await factories.make_floor(db, branch)


@pytest.fixture
async def table(db, floor):
    return await factories.make_table(db, floor, number="T1")


@pytest.fixture
async def table2(db, floor):
    return await factories.make_table(db, floor, number="T2")


def _staff_fixture(role: str, pin: str):
    @pytest.fixture
    async def fixture(db, branch):
        return await factories.make_staff(db, branch, role=role, pin=pin, username=f"{role}1")
    return fixture


owner = _staff_fixture("owner", "9000")
manager = _staff_fixture("manager", "9001")
cashier = _staff_fixture("cashier", "9002")
captain = _staff_fixture("captain", "9003")
waiter = _staff_fixture("waiter", "9004")
kitchen = _staff_fixture("kitchen", "9005")


@pytest.fixture
def auth():
    return factories.auth_headers
