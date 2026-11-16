"""
Populate a local preview database with a demo dress shop, through the real API where the module has endpoints.

    python -m scripts.seed_apparel

Requires the API running at API_URL (default http://127.0.0.1:8010, the `api` entry in .claude/launch.json) against the same
database. Only for local preview/QA databases — never run against production.

Preview sign-in: dressowner / preview-dress-2026 (POS PIN 6142). The business is *Kavya Fashions* (apparel), branch KF1.
The catalogue, stock and sales are added by later stages of the dress shop module.

A second dress shop made through the platform console when stage F was checked (local preview database only): *Meera Boutique*,
branch MB1, meeraowner / preview-boutique-2026 (PIN 6390). Console operator: scripts/seed_platform_admin.py.
"""

import asyncio
import os

import httpx
import redis.asyncio as aioredis
from sqlalchemy import select

from app.cli import create_owner
from app.core.config import settings
from app.core.database import AsyncSessionLocal, dispose_engine
from app.core.entitlements import invalidate_entitlements
from app.models import Organization, StaffUser

API = os.environ.get("API_URL", "http://127.0.0.1:8010") + "/api/v1"
OWNER = {"username": "dressowner", "password": "preview-dress-2026", "pin": "6142"}
SLUG, NAME = "kavya-fashions", "Kavya Fashions"


def check(r: httpx.Response) -> dict:
    if r.status_code >= 400:
        raise SystemExit(f"{r.request.method} {r.request.url} -> {r.status_code}: {r.text}")
    return r.json() if r.content else {}


async def ensure_shop() -> None:
    async with AsyncSessionLocal() as db:
        exists = await db.scalar(select(StaffUser).where(StaffUser.username == OWNER["username"]))
    if not exists:
        await create_owner(OWNER["username"], "Kavya Rao", "KF1", "Jayanagar", OWNER["password"], OWNER["pin"], SLUG, NAME)
    async with AsyncSessionLocal() as db:
        org = await db.scalar(select(Organization).where(Organization.slug == SLUG))
        org.category, org_id = "apparel", org.id
        await db.commit()
    await dispose_engine()
    client = aioredis.from_url(settings.REDIS_URL)
    await invalidate_entitlements(client, org_id)  # the API sees the business type at once
    await client.aclose()


async def main() -> None:
    await ensure_shop()
    async with httpx.AsyncClient(timeout=60) as c:
        owner = check(await c.post(f"{API}/staff/auth/login", json={"username": OWNER["username"], "password": OWNER["password"]}))
        headers = {"Authorization": f"Bearer {owner['access_token']}"}
        ent = check(await c.get(f"{API}/me/entitlements", headers=headers))
        print(f"{NAME} ({ent['category']}) is ready.")
        print(f"Sign in: {OWNER['username']} / {OWNER['password']} (PIN {OWNER['pin']}).")


if __name__ == "__main__":
    asyncio.run(main())
