"""
Populate a local preview database with a demo grocery shop, through the real API (the catalogue goes in by CSV import).

    python -m scripts.seed_shop

Requires the API running at API_URL (default http://127.0.0.1:8000) against the same database.
Only for local preview/QA databases — never run against production.

Preview sign-in: shopowner / preview-shop-2026 (POS PIN 5831). The business is *Fresh Mart* (grocery), branch FM1.
Set SHOP_CATEGORY=cafe or retail to try another business type with the same catalogue.
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

API = os.environ.get("API_URL", "http://127.0.0.1:8000") + "/api/v1"
OWNER = {"username": "shopowner", "password": "preview-shop-2026", "pin": "5831"}
CATEGORY = os.environ.get("SHOP_CATEGORY", "grocery")

CATALOGUE = """name,category,price,gst_rate,hsn,unit,barcode,sku,sold_by_weight
Onion,Vegetables,40,0,0703,kg,,VEG-ONION,yes
Tomato,Vegetables,32,0,0702,kg,,VEG-TOMATO,yes
Potato,Vegetables,28,0,0701,kg,,VEG-POTATO,yes
Toor Dal,Staples,160,5,0713,kg,,STP-TOOR,yes
Basmati Rice 1kg,Staples,120,5,1006,pcs,8901234567890,STP-RICE1,no
Sunflower Oil 1L,Staples,145,5,1512,pcs,8901234567891,STP-OIL1,no
Atta 5kg,Staples,265,5,1101,pcs,8901234567892,STP-ATTA5,no
Toned Milk 500ml,Dairy,28,0,0401,pcs,8901234567893,DRY-MILK,no
Paneer 200g,Dairy,90,5,0406,pcs,8901234567894,DRY-PANEER,no
Curd 400g,Dairy,45,5,0403,pcs,8901234567895,DRY-CURD,no
Bath Soap,Personal care,35,18,3401,pcs,8901030000001,PC-SOAP,no
Toothpaste 150g,Personal care,99,18,3306,pcs,8901030000002,PC-PASTE,no
Shampoo 180ml,Personal care,185,18,3305,pcs,8901030000003,PC-SHAMPOO,no
Biscuits,Snacks,30,18,1905,pcs,8901725000001,SNK-BISC,no
Potato Chips,Snacks,20,12,2005,pcs,8901725000002,SNK-CHIPS,no
"""


def check(r: httpx.Response) -> dict:
    if r.status_code >= 400:
        raise SystemExit(f"{r.request.method} {r.request.url} -> {r.status_code}: {r.text}")
    return r.json() if r.content else {}


async def ensure_shop() -> None:
    async with AsyncSessionLocal() as db:
        exists = await db.scalar(select(StaffUser).where(StaffUser.username == OWNER["username"]))
    if not exists:
        await create_owner(OWNER["username"], "Meera Shah", "FM1", "Koramangala", OWNER["password"], OWNER["pin"],
                           "fresh-mart", "Fresh Mart")
    async with AsyncSessionLocal() as db:
        org = await db.scalar(select(Organization).where(Organization.slug == "fresh-mart"))
        org.category, org_id = CATEGORY, org.id
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
        result = check(await c.post(f"{API}/retail/items/import", json={"csv": CATALOGUE}, headers=headers))
        print(f"Fresh Mart ({CATEGORY}): {result['created']} items created, {result['updated']} updated.")
        print(f"Sign in: {OWNER['username']} / {OWNER['password']} (PIN {OWNER['pin']}).")


if __name__ == "__main__":
    asyncio.run(main())
