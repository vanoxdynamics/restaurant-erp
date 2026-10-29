"""Reusable API flows for module tests."""

from decimal import Decimal
from typing import Any, Dict, List, Optional

from tests.factories import auth_headers, idem

API = "/api/v1"


async def create_order(client, user, table=None, items: Optional[List[Dict[str, Any]]] = None, **extra) -> dict:
    body = {"order_type": "dine_in" if table else "takeaway", "items": items or [], **extra}
    if table is not None:
        body["table_id"] = str(table.id)
    r = await client.post(f"{API}/orders", json=body, headers=idem(user))
    assert r.status_code == 201, r.text
    return r.json()


async def fire(client, user, order_id: str) -> list:
    r = await client.post(f"{API}/kots/fire", json={"order_id": order_id}, headers=idem(user))
    assert r.status_code == 201, r.text
    return r.json()


async def bill(client, user, order_id: str, **extra) -> dict:
    r = await client.post(f"{API}/billing/bills", json={"order_id": order_id, **extra}, headers=idem(user))
    assert r.status_code == 201, r.text
    return r.json()


async def open_shift(client, user, opening_cash: str = "1000.00") -> dict:
    r = await client.post(f"{API}/staff/shifts/open", json={"opening_cash": opening_cash}, headers=auth_headers(user))
    assert r.status_code == 201, r.text
    return r.json()


async def pay(client, user, bill_id: str, amount, mode: str = "cash", reference: Optional[str] = None, key=None):
    body = {"payment_mode": mode, "amount": str(Decimal(amount))}
    if reference:
        body["reference_id"] = reference
    return await client.post(f"{API}/billing/bills/{bill_id}/payments", json=body, headers=idem(user, key))


def line(item, quantity: int = 1, **extra) -> dict:
    return {"item_id": str(item.id), "quantity": quantity, **extra}
