"""
Tenant isolation: an organization's owner must never see or change another organization's data,
even when guessing ids. Cross-tenant lookups answer 404 (never 403) so existence is not revealed.
"""

import pytest

from app.core.auth import resolve_token
from app.core.errors import Unauthorized
from app.core.security import create_access_token
from tests import factories
from tests.factories import auth_headers, idem
from tests.flows import API, create_order, line


@pytest.fixture
async def org_b(db):
    return await factories.make_org(db, slug="globex", name="Globex Diners")


@pytest.fixture
async def branch_b(db, org_b):
    return await factories.make_branch(db, code="DEL1", name="Connaught", org=org_b)


@pytest.fixture
async def owner_b(db, branch_b):
    return await factories.make_staff(db, branch_b, role="owner", pin="8000", username="owner_b")


async def test_token_carries_org_and_resolves(db, owner, branch):
    token = auth_headers(owner)["Authorization"].split()[1]
    user = await resolve_token(db, token)
    assert user.organization_id == owner.organization_id
    assert user.org_branch_ids == frozenset({branch.id})


async def test_token_with_wrong_org_claim_is_rejected(db, owner, org_b):
    token = create_access_token(owner.id, claims={"org": str(org_b.id), "role": "owner", "tv": 0, "amr": "pwd"})
    with pytest.raises(Unauthorized):
        await resolve_token(db, token)


async def test_owner_sees_only_own_org_branches(client, owner, branch, other_branch, owner_b, branch_b):
    r = await client.get(f"{API}/floor/branches", headers=auth_headers(owner))
    assert r.status_code == 200
    assert {b["id"] for b in r.json()} == {str(branch.id), str(other_branch.id)}
    r = await client.get(f"{API}/floor/branches", headers=auth_headers(owner_b))
    assert {b["id"] for b in r.json()} == {str(branch_b.id)}


async def test_owner_cannot_touch_other_orgs_branch(client, owner, branch_b):
    headers = auth_headers(owner)
    assert (await client.get(f"{API}/floor/branches/{branch_b.id}", headers=headers)).status_code == 404
    assert (await client.get(f"{API}/floor/branches/{branch_b.id}/floors", headers=headers)).status_code == 404
    r = await client.get(f"{API}/analytics/kpis/live", params={"branch_id": str(branch_b.id)}, headers=headers)
    assert r.status_code in (403, 404)


async def test_created_branch_belongs_to_callers_org(client, db, owner, owner_b, org_b):
    body = {"name": "Koramangala", "code": "BLR2"}
    r = await client.post(f"{API}/floor/branches", json=body, headers=auth_headers(owner))
    assert r.status_code == 201, r.text
    created = r.json()["id"]
    # Visible to its own org's owner immediately, invisible to the other org.
    mine = await client.get(f"{API}/floor/branches", headers=auth_headers(owner))
    assert created in {b["id"] for b in mine.json()}
    theirs = await client.get(f"{API}/floor/branches", headers=auth_headers(owner_b))
    assert created not in {b["id"] for b in theirs.json()}
    assert (await client.get(f"{API}/floor/branches/{created}", headers=auth_headers(owner_b))).status_code == 404


async def test_staff_isolated_between_orgs(client, owner, branch, owner_b, branch_b):
    listed = await client.get(f"{API}/staff", headers=auth_headers(owner))
    assert {u["id"] for u in listed.json()["items"]} == {str(owner.id)}

    # Other org's owner cannot be revoked or have its PIN reset by guessing the id.
    for path, body in (("revoke-sessions", None), ("reset-pin", {"pin": "5555"})):
        r = await client.post(f"{API}/staff/{owner_b.id}/{path}", json=body, headers=auth_headers(owner))
        assert r.status_code == 404, (path, r.text)

    # Cannot place a new staff member into another org's branch.
    payload = {"username": "intruder", "full_name": "In Truder", "role": "waiter", "pin": "4321",
               "branch_id": str(branch_b.id)}
    r = await client.post(f"{API}/staff", json=payload, headers=auth_headers(owner))
    assert r.status_code == 404, r.text


async def test_created_staff_inherits_org(client, db, owner, branch, org_b):
    payload = {"username": "newwaiter", "full_name": "New Waiter", "role": "waiter", "pin": "4321",
               "branch_id": str(branch.id)}
    r = await client.post(f"{API}/staff", json=payload, headers=auth_headers(owner))
    assert r.status_code == 201, r.text
    from app.models import StaffUser
    created = await db.get(StaffUser, r.json()["id"])
    assert created.organization_id == owner.organization_id


async def test_menu_is_per_org(client, db, owner, owner_b, org_b, waiter):
    mine = await factories.make_menu_item(db, name="Paneer Tikka")
    cat_b = await factories.make_category(db, name="Starters", org=org_b)
    theirs = await factories.make_menu_item(db, category=cat_b, name="Pad Thai")

    items_a = await client.get(f"{API}/catalog/items", headers=auth_headers(owner))
    assert {i["id"] for i in items_a.json()} == {str(mine.id)}
    items_b = await client.get(f"{API}/catalog/items", headers=auth_headers(owner_b))
    assert {i["id"] for i in items_b.json()} == {str(theirs.id)}

    cats_a = await client.get(f"{API}/catalog/categories", headers=auth_headers(owner))
    assert {c["name"] for c in cats_a.json()} == {"Mains"}

    menu_b = await client.get(f"{API}/catalog/menu", headers=auth_headers(owner_b))
    assert [c["name"] for c in menu_b.json()["categories"]] == ["Starters"]

    # Different tenants never share a menu fingerprint.
    etag_a = (await client.get(f"{API}/catalog/menu", headers=auth_headers(owner))).headers["ETag"]
    assert etag_a != menu_b.headers["ETag"]


async def test_cannot_read_or_edit_other_orgs_catalog(client, db, owner, org_b):
    cat_b = await factories.make_category(db, name="Starters", org=org_b)
    item_b = await factories.make_menu_item(db, category=cat_b, name="Pad Thai", variants=[("Half", "100.00")])
    group_b = await factories.make_modifier_group(db, name="Heat", org=org_b)
    headers = auth_headers(owner)

    assert (await client.get(f"{API}/catalog/items/{item_b.id}", headers=headers)).status_code == 404
    assert (await client.patch(f"{API}/catalog/items/{item_b.id}", json={"name": "Hacked"}, headers=headers)).status_code == 404
    assert (await client.patch(f"{API}/catalog/items/{item_b.id}/toggle-86", json={"is_available_86": False},
                               headers=headers)).status_code == 404
    assert (await client.patch(f"{API}/catalog/categories/{cat_b.id}", json={"name": "Hacked"},
                               headers=headers)).status_code == 404

    mine = await client.get(f"{API}/catalog/modifier-groups", headers=headers)
    assert str(group_b.id) not in {g["id"] for g in mine.json()}

    # Cannot create an item under, or attach modifiers from, another org.
    body = {"category_id": str(cat_b.id), "name": "Sneaky", "base_price": "10.00", "station": "kitchen"}
    assert (await client.post(f"{API}/catalog/items", json=body, headers=headers)).status_code == 404
    own_cat = await factories.make_category(db, name="Mine")
    body = {"category_id": str(own_cat.id), "name": "Sneaky", "base_price": "10.00", "station": "kitchen",
            "modifier_group_ids": [str(group_b.id)]}
    assert (await client.post(f"{API}/catalog/items", json=body, headers=headers)).status_code == 404


async def test_cannot_order_other_orgs_menu_item(client, db, waiter, org_b):
    cat_b = await factories.make_category(db, name="Starters", org=org_b)
    item_b = await factories.make_menu_item(db, category=cat_b, name="Pad Thai")
    r = await client.post(
        f"{API}/orders",
        json={"order_type": "takeaway", "items": [line(item_b)]},
        headers=idem(waiter),
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "item_unavailable"


async def test_86_event_only_reaches_own_org_branches(client, db, owner, branch, branch_b, org_b, events):
    item = await factories.make_menu_item(db)
    r = await client.patch(f"{API}/catalog/items/{item.id}/toggle-86", json={"is_available_86": False},
                           headers=auth_headers(owner))
    assert r.status_code == 200
    targets = {e["branch_id"] for e in events if e["type"] == "item.86_changed"}
    assert str(branch.id) in targets and str(branch_b.id) not in targets


async def test_same_org_flow_still_works(client, db, waiter, table):
    item = await factories.make_menu_item(db)
    order = await create_order(client, waiter, table, items=[line(item)])
    assert order["items"]
