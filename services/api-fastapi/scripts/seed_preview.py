"""
Populate a local preview database through the real API, the way staff would use it.

    DATABASE_URL=...restaurant_erp_preview python -m scripts.seed_preview

Requires the API running at API_URL (default http://127.0.0.1:8000) against the same database.
Only for local preview/QA databases — never run against production.

Preview sign-in: owner / preview-owner-2026 (POS PIN 4821).
"""

import asyncio
import os
import uuid

import httpx
from sqlalchemy import select

from app.cli import create_owner
from app.core.database import AsyncSessionLocal, dispose_engine
from app.core.modules import ModuleKey
from app.models import Organization, OrganizationModule, StaffUser

API = os.environ.get("API_URL", "http://127.0.0.1:8000") + "/api/v1"
OWNER = {"username": "owner", "password": "preview-owner-2026", "pin": "4821"}


def idem() -> dict:
    return {"X-Idempotency-Key": uuid.uuid4().hex}


def check(r: httpx.Response) -> dict:
    if r.status_code >= 400:
        raise SystemExit(f"{r.request.method} {r.request.url} -> {r.status_code}: {r.text}")
    return r.json() if r.content else {}


async def ensure_owner() -> None:
    async with AsyncSessionLocal() as db:
        if await db.scalar(select(StaffUser).where(StaffUser.username == OWNER["username"])):
            return
    await create_owner(OWNER["username"], "Arjun Mehta", "BLR1", "Indiranagar", OWNER["password"], OWNER["pin"],
                       "spice-route", "Spice Route")
    # New businesses start without the kitchen display; this demo shows it (it has a kitchen-screen account), so it opts in.
    async with AsyncSessionLocal() as db:
        org = await db.scalar(select(Organization).where(Organization.slug == "spice-route"))
        override = await db.get(OrganizationModule, (org.id, ModuleKey.KDS.value))
        override.enabled, override.reason = True, "Demo restaurant uses the kitchen screen"
        await db.commit()
    await dispose_engine()


async def main() -> None:
    await ensure_owner()
    async with httpx.AsyncClient(timeout=30) as c:
        owner = check(await c.post(f"{API}/staff/auth/login", json={"username": "owner", "password": OWNER["password"]}))
        H = {"Authorization": f"Bearer {owner['access_token']}"}
        branch_id = owner["branch_id"]

        if check(await c.get(f"{API}/floor/branches/{branch_id}/floors", headers=H)):
            print("Preview data already present; nothing to do.")
            return

        # Floors and tables
        tables = {}
        for floor_name, numbers, seats in (("Main hall", ["T1", "T2", "T3", "T4", "T5", "T6"], 4), ("Terrace", ["P1", "P2", "P3"], 6)):
            floor = check(await c.post(f"{API}/floor/branches/{branch_id}/floors", json={"name": floor_name}, headers=H))
            for n in numbers:
                t = check(await c.post(f"{API}/floor/floors/{floor['id']}/tables", json={"table_number": n, "capacity": seats}, headers=H))
                tables[n] = t["id"]

        # Menu
        menu = {}
        for cat_name, items in {
            "Starters": [("Paneer Tikka", "280.00", "grill"), ("Chicken 65", "320.00", "kitchen"), ("Hara Bhara Kebab", "240.00", "grill")],
            "Mains": [("Butter Chicken", "420.00", "kitchen"), ("Dal Makhani", "310.00", "kitchen"), ("Paneer Butter Masala", "360.00", "kitchen")],
            "Breads": [("Butter Naan", "70.00", "grill"), ("Garlic Naan", "90.00", "grill")],
            "Drinks": [("Fresh Lime Soda", "120.00", "kitchen"), ("Masala Chaas", "90.00", "kitchen"), ("Cold Coffee", "160.00", "kitchen")],
            "Desserts": [("Gulab Jamun", "140.00", "dessert"), ("Kulfi", "150.00", "dessert")],
        }.items():
            cat = check(await c.post(f"{API}/catalog/categories", json={"name": cat_name}, headers=H))
            for name, price, station in items:
                item = check(await c.post(f"{API}/catalog/items", headers=H, json={
                    "category_id": cat["id"], "name": name, "base_price": price, "station": station,
                }))
                menu[name] = item["id"]
        check(await c.patch(f"{API}/catalog/items/{menu['Kulfi']}/toggle-86", json={"is_available_86": False}, headers=H))

        # Staff
        staff = [
            {"username": "neha.manager", "full_name": "Neha Kapoor", "role": "manager", "pin": "5521", "password": "preview-manager-2026"},
            {"username": "ravi.captain", "full_name": "Ravi Kumar", "role": "captain", "pin": "3104"},
            {"username": "sana.waiter", "full_name": "Sana Shaikh", "role": "waiter", "pin": "3871"},
            {"username": "imran.cashier", "full_name": "Imran Ali", "role": "cashier", "pin": "6630"},
            {"username": "deepak.kitchen", "full_name": "Deepak Rao", "role": "kitchen", "pin": "7409"},
        ]
        for s in staff:
            check(await c.post(f"{API}/staff", json=s, headers=H))

        async def pin_login(pin: str) -> dict:
            r = check(await c.post(f"{API}/staff/auth/pin", json={"pin": pin, "branch_id": branch_id}))
            return {"Authorization": f"Bearer {r['access_token']}"}

        captain = await pin_login("3104")
        cashier = await pin_login("6630")

        async def order(table: str, lines: list[tuple[str, int]]) -> dict:
            o = check(await c.post(f"{API}/orders", headers={**captain, **idem()}, json={
                "order_type": "dine_in", "table_id": tables[table], "guest_count": 2,
                "items": [{"item_id": menu[n], "quantity": q} for n, q in lines],
            }))
            kots = check(await c.post(f"{API}/kots/fire", headers={**captain, **idem()}, json={"order_id": o["id"]}))
            return {**o, "kots": kots}

        # Settled tables
        check(await c.post(f"{API}/staff/shifts/open", json={"opening_cash": "2000.00"}, headers=cashier))
        for table, lines, mode, ref in (
            ("T1", [("Butter Chicken", 1), ("Butter Naan", 3), ("Fresh Lime Soda", 2)], "upi", "UTR4471"),
            ("T2", [("Paneer Tikka", 1), ("Dal Makhani", 1), ("Garlic Naan", 2)], "cash", None),
            ("P1", [("Chicken 65", 2), ("Paneer Butter Masala", 1), ("Butter Naan", 4), ("Cold Coffee", 3)], "card", "AUTH8820"),
        ):
            o = await order(table, lines)
            bill = check(await c.post(f"{API}/billing/bills", headers={**cashier, **idem()}, json={"order_id": o["id"]}))
            body = {"payment_mode": mode, "amount": bill["grand_total"]}
            if ref:
                body["reference_id"] = ref
            check(await c.post(f"{API}/billing/bills/{bill['id']}/payments", headers={**cashier, **idem()}, json=body))

        # A large discount approved by a manager PIN
        o = await order("T3", [("Butter Chicken", 2), ("Garlic Naan", 4), ("Gulab Jamun", 2)])
        bill = check(await c.post(f"{API}/billing/bills", headers={**cashier, **idem()}, json={
            "order_id": o["id"], "discount_amount": "250.00", "discount_reason": "Regular guest", "approver_pin": "5521",
        }))
        check(await c.post(f"{API}/billing/bills/{bill['id']}/payments", headers={**cashier, **idem()},
                           json={"payment_mode": "cash", "amount": bill["grand_total"]}))

        # Close the cashier's shift ₹50 short so shift history has a variance
        paid = check(await c.get(f"{API}/billing/bills", params={"status": "paid"}, headers=cashier))
        cash_total = sum(float(p["amount"]) for b in paid["items"] for p in b["payments"] if p["payment_mode"] == "cash")
        check(await c.post(f"{API}/staff/shifts/close", headers=cashier,
                           json={"blind_drop_cash": f"{2000 + cash_total - 50:.2f}"}))

        # Live tables: one billed and waiting to pay, two still eating
        o = await order("T4", [("Hara Bhara Kebab", 1), ("Masala Chaas", 2)])
        check(await c.post(f"{API}/billing/bills", headers={**cashier, **idem()}, json={"order_id": o["id"]}))
        busy = await order("T5", [("Paneer Butter Masala", 1), ("Butter Naan", 2), ("Fresh Lime Soda", 1)])
        terrace = await order("P2", [("Chicken 65", 1), ("Cold Coffee", 2)])

        # Void requests waiting for a manager
        breads = next(k for k in busy["kots"] if k["station"] == "grill")
        check(await c.post(f"{API}/kots/{breads['id']}/void-requests", json={"reason": "Customer Changed Mind"}, headers=captain))
        kitchen = next(k for k in terrace["kots"] if k["station"] == "kitchen")
        check(await c.post(f"{API}/kots/{kitchen['id']}/void-requests", json={"reason": "Wrong Order Punch"}, headers=captain))

    print("Preview data created. Sign in as 'owner' (see script docstring for the password).")


if __name__ == "__main__":
    asyncio.run(main())
