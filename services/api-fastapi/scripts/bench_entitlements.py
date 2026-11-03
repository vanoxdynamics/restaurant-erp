"""
Benchmark the subscription/entitlement hot path.

Every authenticated tenant request now runs: token check -> user lookup -> (owners: branch list) -> RLS scope -> cached
entitlement lookup. This measures what that costs, in-process (no network), on a throw-away database.

    python scripts/bench_entitlements.py [--requests 3000] [--concurrency 32]

It creates and drops its own database named <your database>_bench on the server DATABASE_URL points at, and uses Redis
database 13. It never touches your real data.
"""

import argparse
import asyncio
import logging
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_DIR))


def _bench_url() -> str:
    from sqlalchemy.engine import make_url

    base = os.environ.get("DATABASE_URL")
    if not base:
        for line in (SERVICE_DIR / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("DATABASE_URL="):
                base = line.split("=", 1)[1].strip().strip('"')
    url = make_url(base)
    return url.set(database=f"{url.database}_bench").render_as_string(hide_password=False)


BENCH_URL = _bench_url()
os.environ.update({"DATABASE_URL": BENCH_URL, "REDIS_URL": "redis://localhost:6379/13", "ENVIRONMENT": "development", "DEBUG": "false"})
os.chdir(SERVICE_DIR)

import asyncpg  # noqa: E402
import httpx  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402


def pct(values, p):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * p))]


def report(name, samples_s, wall_s=None, n=None):
    ms = [s * 1000 for s in samples_s]
    rate = (n or len(ms)) / (wall_s or sum(samples_s))
    print(f"{name:<46} {rate:>9.0f}/s   p50 {statistics.median(ms):6.2f} ms   p95 {pct(ms, .95):6.2f} ms   p99 {pct(ms, .99):6.2f} ms")


async def setup() -> None:
    url = make_url(BENCH_URL)
    admin = await asyncpg.connect(host=url.host, port=url.port, user=url.username, password=url.password, database="postgres")
    await admin.execute(f'DROP DATABASE IF EXISTS "{url.database}"')
    await admin.execute(f'CREATE DATABASE "{url.database}"')
    await admin.close()
    await asyncio.to_thread(subprocess.run, [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=SERVICE_DIR, check=True, capture_output=True)


async def teardown() -> None:
    from app.core.database import dispose_engine
    from app.core.redis import close_redis_pool

    await close_redis_pool()
    await dispose_engine()
    url = make_url(BENCH_URL)
    admin = await asyncpg.connect(host=url.host, port=url.port, user=url.username, password=url.password, database="postgres")
    await admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1", url.database)
    await admin.execute(f'DROP DATABASE IF EXISTS "{url.database}"')
    await admin.close()


async def _timed_get(client: httpx.AsyncClient, path: str, sem: asyncio.Semaphore, latencies: list[float]) -> None:
    async with sem:
        t = time.perf_counter()
        r = await client.get(path)
        latencies.append(time.perf_counter() - t)
        assert r.status_code == 200


async def main(requests: int, concurrency: int) -> None:
    await setup()
    logging.disable(logging.CRITICAL)  # per-request log lines would dominate the measurement
    from app.core.auth import token_claims
    from app.core.database import AsyncSessionLocal, rls_status
    from app.core.entitlements import compute_entitlements, ensure_legacy_subscription, get_entitlements
    from app.core.redis import redis_client
    from app.core.security import compute_pin_lookup, create_access_token, get_password_hash, get_pin_hash
    from app.main import app
    from app.models import Branch, Organization, StaffUser

    async with AsyncSessionLocal() as db:
        org = Organization(slug="bench", name="Bench Org")
        db.add(org)
        await db.flush()
        await ensure_legacy_subscription(db, org)
        branches = []
        for i in range(3):
            b = Branch(organization_id=org.id, code=f"B{i}", name=f"Branch {i}")
            b.floors = []
            db.add(b)
            branches.append(b)
        await db.flush()
        owner = StaffUser(organization_id=org.id, branch_id=branches[0].id, username="bench_owner", full_name="Bench", role="owner",
                          hashed_password=get_password_hash("x" * 12), hashed_pin=get_pin_hash("1234"),
                          pin_lookup=compute_pin_lookup(branches[0].id, "1234"))
        db.add(owner)
        await db.commit()
        token = create_access_token(owner.id, claims=token_claims(owner, "pwd"))
        rls = await rls_status(db)
        org_id = org.id

    print(f"database role: row-level security {rls}\n")
    print(f"{'measurement':<46} {'throughput':>11}   latency")
    print("-" * 100)

    # 1. entitlement computation straight from the database (cache miss path)
    samples = []
    async with AsyncSessionLocal() as db:
        for _ in range(300):
            t = time.perf_counter()
            await compute_entitlements(db, org_id)
            samples.append(time.perf_counter() - t)
    report("entitlements from the database (cache miss)", samples)

    # 2. cached lookup
    rc = redis_client()
    samples = []
    async with AsyncSessionLocal() as db:
        await get_entitlements(db, org_id, rc)
        for _ in range(3000):
            t = time.perf_counter()
            await get_entitlements(db, org_id, rc)
            samples.append(time.perf_counter() - t)
    await rc.aclose()
    report("entitlements from Redis (cache hit)", samples)

    # 3. whole authenticated requests, concurrently, in-process
    headers = {"Authorization": f"Bearer {token}"}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://bench", headers=headers) as client:
        for path in ("/api/v1/me/entitlements", "/api/v1/floor/branches", "/api/v1/catalog/categories"):
            assert (await client.get(path)).status_code == 200, path
            latencies: list[float] = []
            sem = asyncio.Semaphore(concurrency)
            start = time.perf_counter()
            await asyncio.gather(*(_timed_get(client, path, sem, latencies) for _ in range(requests)))
            report(f"GET {path} x{concurrency} concurrent", latencies, wall_s=time.perf_counter() - start, n=requests)

    await teardown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--requests", type=int, default=3000)
    parser.add_argument("--concurrency", type=int, default=32)
    args = parser.parse_args()
    asyncio.run(main(args.requests, args.concurrency))
