"""
Populate a local preview database with a demo hardware and electrical shop, through the real API where the module has endpoints.

    python -m scripts.seed_hardware

Requires the API running at API_URL (default http://127.0.0.1:8010, the `api` entry in .claude/launch.json) against the same
database. Only for local preview/QA databases — never run against production.

Preview sign-in: hwowner / preview-hardware-2026 (POS PIN 7253). The business is *Sri Balaji Hardware* (hardware), branch SB1.
Stage H1 loads about 60 products by CSV import, with alternate units (coils, boxes, packets, bags); every row has a SKU, so
re-running updates them. Stock, accounts and sales are added by later stages of the hardware module.
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
OWNER = {"username": "hwowner", "password": "preview-hardware-2026", "pin": "7253"}
SLUG, NAME = "sri-balaji-hardware", "Sri Balaji Hardware"


def check(r: httpx.Response) -> dict:
    if r.status_code >= 400:
        raise SystemExit(f"{r.request.method} {r.request.url} -> {r.status_code}: {r.text}")
    return r.json() if r.content else {}


async def ensure_shop() -> None:
    async with AsyncSessionLocal() as db:
        exists = await db.scalar(select(StaffUser).where(StaffUser.username == OWNER["username"]))
    if not exists:
        await create_owner(OWNER["username"], "Ravi Kumar", "SB1", "Peenya", OWNER["password"], OWNER["pin"], SLUG, NAME)
    async with AsyncSessionLocal() as db:
        org = await db.scalar(select(Organization).where(Organization.slug == SLUG))
        org.category, org_id = "hardware", org.id
        await db.commit()
    await dispose_engine()
    client = aioredis.from_url(settings.REDIS_URL)
    await invalidate_entitlements(client, org_id)  # the API sees the business type at once
    await client.aclose()

COLUMNS = "name,category,brand,size,price,wholesale_price,min_price,mrp,gst_rate,hsn,unit,sku,alt_unit,alt_factor,alt_price,alt_barcode"


def _rows() -> list:
    rows = []
    # Cables: by the metre and by the 90 m coil, which costs less than 90 single metres.
    for i, (size, per_m, coil) in enumerate((("0.75 sq mm", "14.50", "1180.00"), ("1.0 sq mm", "18.00", "1450.00"),
                                             ("1.5 sq mm", "27.00", "2250.00"), ("2.5 sq mm", "42.00", "3500.00"),
                                             ("4.0 sq mm", "66.00", "5600.00"), ("6.0 sq mm", "98.00", "8350.00"))):
        rows.append(f"FR Copper Wire,Cables,Finolex,{size},{per_m},,,,18,8544,m,CW-{i},coil,90,{coil},89010101{i:05d}")
    for i, (size, per_m) in enumerate((("3 core 1.5 sq mm", "78.00"), ("3 core 2.5 sq mm", "118.00"))):
        rows.append(f"Flexible Cable,Cables,Polycab,{size},{per_m},,,,18,8544,m,FC-{i},roll,100,,")
    rows.append("TV Coaxial Cable,Cables,Havells,RG6,16.00,,,,18,8544,m,COAX-6,coil,90,1290.00,")
    rows.append("LAN Cable,Cables,D-Link,Cat 6,22.00,,,,18,8544,m,LAN-6,box,305,5900.00,")
    # Pipe fittings: pieces, sold loose or by the box.
    for kind, base in (("PVC Elbow", 8), ("PVC Tee", 11), ("PVC Coupler", 6), ("PVC End Cap", 5)):
        for j, (size, mult) in enumerate((("1/2 inch", 1.0), ("3/4 inch", 1.5), ("1 inch", 2.2))):
            sku = f"{kind.split()[1][:3].upper()}-{j}"
            rows.append(f"{kind},Pipe Fittings,Supreme,{size},{base * mult:.2f},{base * mult * 0.85:.2f},,,18,3917,pcs,{sku},box,25,,")
    for j, (size, per_ft) in enumerate((("1/2 inch", "9.50"), ("3/4 inch", "13.00"), ("1 inch", "19.50"))):
        rows.append(f"CPVC Pipe,Pipe Fittings,Astral,{size},{per_ft},,,,18,3917,ft,PIPE-{j},bundle,10,,")
    # Fasteners: loose pieces at small prices (4 decimals), boxes and packets.
    for j, (size, each) in enumerate((("6 x 1/2 inch", "0.4500"), ("8 x 1 inch", "0.8000"), ("8 x 1.5 inch", "1.1000"),
                                      ("10 x 2 inch", "1.6500"))):
        rows.append(f"Wood Screw,Fasteners,GKW,{size},{each},,,,18,7318,pcs,WS-{j},box,100,,89020202{j:05d}")
    for j, (size, each) in enumerate((("6 mm", "1.2500"), ("8 mm", "1.7500"), ("10 mm", "2.5000"))):
        rows.append(f"Wall Plug,Fasteners,Fischer,{size},{each},,,,18,3926,pcs,WP-{j},pkt,50,,")
    for j, (size, per_kg) in enumerate((("1 inch", "92.00"), ("2 inch", "88.00"), ("3 inch", "86.00"))):
        rows.append(f"Wire Nails,Fasteners,,{size},{per_kg},,,,18,7317,kg,NAIL-{j},,,,")
    rows.append("Hex Bolt with Nut,Fasteners,,M8 x 50,6.5000,,,,18,7318,pcs,HB-850,dozen,12,72.00,")
    # Paint: by the litre and by the 4 l tin (entered as a box of 4 l).
    for j, (name, per_l) in enumerate((("Interior Emulsion", "310.00"), ("Exterior Emulsion", "420.00"), ("Enamel Paint", "380.00"),
                                       ("Wall Primer", "190.00"), ("Distemper", "95.00"))):
        rows.append(f"{name},Paint,Asian Paints,White,{per_l},,,,18,3209,l,PNT-{j},box,4,,")
    rows.append("Wall Putty,Paint,Birla White,,26.0000,,,,18,3214,kg,PUTTY,bag,40,980.00,")
    rows.append("Paint Brush,Paint,Asian Paints,2 inch,85.00,,,110,18,9603,pcs,BR-2,,,,")
    rows.append("Paint Roller,Paint,,9 inch,180.00,,,240,18,9603,pcs,ROL-9,,,,")
    # Switches and accessories.
    for j, (name, size, price, mrp) in enumerate((("Modular Switch", "6A", "48.00", "62"), ("Modular Switch", "16A", "92.00", "118"),
                                                  ("Modular Socket", "6A", "86.00", "110"), ("Modular Socket", "16A", "138.00", "176"),
                                                  ("Switch Plate", "4 module", "95.00", "128"), ("Switch Plate", "8 module", "165.00", "220"),
                                                  ("MCB Single Pole", "16A", "210.00", "290"), ("MCB Double Pole", "32A", "520.00", "720"),
                                                  ("LED Bulb", "9W", "85.00", "120"), ("LED Batten", "20W", "260.00", "390"))):
        rows.append(f"{name},Switches,Anchor,{size},{price},,,{mrp},18,8536,pcs,SW-{j},box,10,,")
    rows.append("Insulation Tape,Switches,Steelgrip,18 mm,12.00,,,15,18,3919,pcs,TAPE-18,pkt,10,110.00,")
    rows.append("Cement,Building,UltraTech,OPC 53,8.2000,,,,28,2523,kg,CEM-53,bag,50,405.00,")
    return rows


async def load_catalogue(c: httpx.AsyncClient, headers: dict) -> None:
    csv = "\n".join([COLUMNS, *_rows()])
    result = check(await c.post(f"{API}/hardware/import", json={"csv": csv}, headers=headers))
    if result["errors"]:
        raise SystemExit(f"Import errors: {result['errors']}")
    print(f"Catalogue: {result['products_created']} products created, {result['products_updated']} updated, "
          f"{result['units_created'] + result['units_updated']} alternate units.")


async def main() -> None:
    await ensure_shop()
    async with httpx.AsyncClient(timeout=60) as c:
        owner = check(await c.post(f"{API}/staff/auth/login", json={"username": OWNER["username"], "password": OWNER["password"]}))
        headers = {"Authorization": f"Bearer {owner['access_token']}"}
        ent = check(await c.get(f"{API}/me/entitlements", headers=headers))
        await load_catalogue(c, headers)
        print(f"{NAME} ({ent['category']}) is ready.")
        print(f"Sign in: {OWNER['username']} / {OWNER['password']} (PIN {OWNER['pin']}).")


if __name__ == "__main__":
    asyncio.run(main())
