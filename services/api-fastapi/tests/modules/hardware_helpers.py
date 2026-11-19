"""Shared set-up for the hardware shop tests: a hardware organization with staff, and a few catalogue builders."""

from typing import Any, Dict, List, Optional

from tests.factories import auth_headers
from tests.flows import API
from tests.modules import apparel_helpers

H = f"{API}/hardware"
set_category = apparel_helpers.set_category


async def make_shop(db, redis_client, *, slug: str = "balaji", code: str = "SB1") -> Dict[str, Any]:
    """A hardware organization with a branch, owner, manager and cashier."""
    return await apparel_helpers.make_shop(db, redis_client, slug=slug, code=code, category="hardware")


async def category(client, user, name: str = "Cables") -> Dict[str, Any]:
    r = await client.post(f"{H}/categories", json={"name": name}, headers=auth_headers(user))
    assert r.status_code == 201, r.text
    return r.json()


async def product(client, user, category_id: str, *, name: str = "Copper Wire", price: str = "42.00",
                  units: Optional[List[Dict[str, Any]]] = None, **extra: Any) -> Dict[str, Any]:
    body = {"category_id": category_id, "name": name, "price": price, "units": units or [], **extra}
    r = await client.post(f"{H}/products", json=body, headers=auth_headers(user))
    assert r.status_code == 201, r.text
    return r.json()


async def cable(client, user, category_id: str, **extra: Any) -> Dict[str, Any]:
    """Copper wire 2.5 sq mm sold by the metre, by the 90 m coil (3,500.00) and the 10 m bundle."""
    return await product(client, user, category_id, name="Copper Wire", size_spec="2.5 sq mm", base_unit="m", price="42.00",
                         units=[{"unit": "coil", "factor": "90", "price": "3500.00", "barcode": "8901000000901"},
                                {"unit": "bundle", "factor": "10"}], **extra)


def opt(product: Dict[str, Any], unit: str) -> Dict[str, Any]:
    return next(o for o in product["options"] if o["unit"] == unit)
