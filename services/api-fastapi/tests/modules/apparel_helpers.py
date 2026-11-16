"""Shared set-up for the dress shop tests: an apparel organization with staff, and a few catalogue builders."""

from typing import Any, Dict, List, Optional

from app.core.entitlements import invalidate_entitlements
from app.models import Organization
from tests import factories
from tests.factories import auth_headers
from tests.flows import API

A = f"{API}/apparel"


async def make_shop(db, redis_client, *, slug: str = "kavya", code: str = "KF1", category: str = "apparel") -> Dict[str, Any]:
    """An organization of `category` with a branch, owner, manager and cashier. Returns the objects by role."""
    org = await factories.make_org(db, slug=slug, name=f"Shop {slug}")
    org.category = category
    await db.commit()
    await invalidate_entitlements(redis_client, org.id)
    branch = await factories.make_branch(db, code=code, name=f"{slug} main", org=org)
    staff = {role: await factories.make_staff(db, branch, role=role, pin=pin, username=f"{role}_{slug}")
             for role, pin in (("owner", "7000"), ("manager", "7001"), ("cashier", "7002"))}
    return {"org": org, "branch": branch, **staff}


async def set_category(db, redis_client, org_id, category: str) -> None:
    org = await db.get(Organization, org_id)
    org.category = category
    await db.commit()
    await invalidate_entitlements(redis_client, org_id)


async def category(client, user, name: str = "Shirts") -> Dict[str, Any]:
    r = await client.post(f"{A}/categories", json={"name": name}, headers=auth_headers(user))
    assert r.status_code == 201, r.text
    return r.json()


async def product(client, user, category_id: str, *, name: str = "Oxford Shirt", sizes: Optional[List[str]] = None,
                  colours: Optional[List[str]] = None, price: str = "999.00", mrp: Optional[str] = "1299.00",
                  **extra: Any) -> Dict[str, Any]:
    body = {"category_id": category_id, "name": name, "sizes": sizes or ["S", "M", "L"], "colours": colours or ["Blue"],
            "price": price, "mrp": mrp, **extra}
    r = await client.post(f"{A}/products", json=body, headers=auth_headers(user))
    assert r.status_code == 201, r.text
    return r.json()
