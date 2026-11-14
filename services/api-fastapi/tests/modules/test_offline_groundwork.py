"""
Offline billing, phase 1 (docs/pos-devices/06-offline-phase-1-spec.md): the groundwork only. A sale id that makes duplicates
impossible, devices as records that can be revoked, the business's own switch and limits, and the offline lease.
"""

import asyncio
from datetime import datetime, timedelta

from sqlalchemy import func, select

from app.core.entitlements import invalidate_entitlements
from app.core.security import create_access_token, decode_offline_lease
from app.models import Device, Order, Organization, PlatformModule, Subscription
from tests import factories
from tests.factories import auth_headers, idem
from tests.flows import API, line
from tests.modules.test_business_categories import make_item, set_category
from tests.modules.test_kitchen_module import set_modules
from tests.modules.test_staff import _link_device, _make_code

ORDERS = f"{API}/orders"


async def counter(client, user, items, sale_id=None, key=None, **extra):
    body = {"order_type": "counter_sale", "items": items, **extra}
    if sale_id is not None:
        body["client_sale_id"] = sale_id
    return await client.post(ORDERS, json=body, headers=idem(user, key))


async def order_count(db) -> int:
    return await db.scalar(select(func.count()).select_from(Order))


async def other_business(db, *, code="OTH1"):
    org = await factories.make_org(db, slug="other", name="Other Traders")
    branch = await factories.make_branch(db, code=code, name="Elsewhere", org=org)
    return org, branch


# --- step 2: the sale id ---------------------------------------------------------------------------------------------------


async def test_the_same_sale_id_gives_one_order(client, db, branch, cashier):
    soap = await make_item(db, name="Soap", price="35.00")
    first = await counter(client, cashier, [line(soap)], sale_id="dev1-sale-0001")
    again = await counter(client, cashier, [line(soap)], sale_id="dev1-sale-0001")
    assert first.status_code == again.status_code == 201, again.text
    assert again.json()["id"] == first.json()["id"] and again.json()["order_number"] == first.json()["order_number"]
    assert first.json()["client_sale_id"] == "dev1-sale-0001"
    assert await order_count(db) == 1


async def test_a_different_cashier_sending_the_sale_gets_the_same_order(client, db, branch, cashier, manager):
    soap = await make_item(db, name="Soap", price="35.00")
    first = await counter(client, cashier, [line(soap)], sale_id="dev1-sale-0002")
    again = await counter(client, manager, [line(soap)], sale_id="dev1-sale-0002")
    assert again.status_code == 201 and again.json()["id"] == first.json()["id"]
    assert await order_count(db) == 1


async def test_the_first_order_wins_even_with_different_items(client, db, branch, cashier):
    soap = await make_item(db, name="Soap", price="35.00")
    rice = await make_item(db, name="Rice", price="60.00")
    first = (await counter(client, cashier, [line(soap)], sale_id="dev1-sale-0003")).json()
    again = await counter(client, cashier, [line(rice, 3)], sale_id="dev1-sale-0003")
    assert again.status_code == 201
    body = again.json()
    assert body["id"] == first["id"] and [i["name_snapshot"] for i in body["items"]] == ["Soap"]
    assert body["subtotal"] == first["subtotal"] == "35.00"


async def test_the_rule_is_per_branch(client, db, branch, other_branch, cashier, owner):
    first = await counter(client, cashier, [], sale_id="shared-sale-01")
    in_other_branch = await counter(client, owner, [], sale_id="shared-sale-01", branch_id=str(other_branch.id))
    _, their_branch = await other_business(db)
    their_cashier = await factories.make_staff(db, their_branch, role="cashier", pin="7002", username="their_cashier")
    in_other_business = await counter(client, their_cashier, [], sale_id="shared-sale-01")
    ids = {first.json()["id"], in_other_branch.json()["id"], in_other_business.json()["id"]}
    assert len(ids) == 3 and await order_count(db) == 3


async def test_two_requests_racing_with_one_sale_id_make_one_order(client, db, branch, cashier):
    soap = await make_item(db, name="Soap", price="35.00")
    a, b = await asyncio.gather(
        counter(client, cashier, [line(soap)], sale_id="dev1-race-0001"),
        counter(client, cashier, [line(soap)], sale_id="dev1-race-0001"),
    )
    assert a.status_code == b.status_code == 201, (a.text, b.text)
    assert a.json()["id"] == b.json()["id"]
    assert await order_count(db) == 1


async def test_bad_sale_ids_are_refused(client, db, branch, cashier):
    for bad in ("short", "has a space", "x" * 65, "semi;colon1"):
        r = await counter(client, cashier, [], sale_id=bad)
        assert r.status_code == 422, bad
    assert await order_count(db) == 0


async def test_a_duplicate_does_not_use_up_a_token(client, db, redis_client, branch, cashier):
    await set_category(db, redis_client, branch.organization_id, "cafe")
    latte = await make_item(db, name="Latte", price="180.00")
    first = (await counter(client, cashier, [line(latte)], sale_id="cafe-sale-0001")).json()
    again = (await counter(client, cashier, [line(latte)], sale_id="cafe-sale-0001")).json()
    assert again["token_number"] == first["token_number"]
    following = (await counter(client, cashier, [line(latte)])).json()
    assert following["token_number"] == first["token_number"] + 1


async def test_orders_without_a_sale_id_are_unchanged(client, db, branch, cashier):
    a = (await counter(client, cashier, [])).json()
    b = (await counter(client, cashier, [])).json()
    assert a["id"] != b["id"] and a["client_sale_id"] is None


# --- step 3: devices as records ---------------------------------------------------------------------------------------------

DEVICES = f"{API}/staff/devices"
ROSTER = f"{API}/staff/auth/roster"
STATION_QUEUE = f"{API}/kots/station/print-queue"
LEASE = f"{API}/staff/devices/lease"
OFFLINE = f"{API}/business/offline"


async def link(client, manager, branch, device_id="counter-1") -> dict:
    token = await _link_device(client, manager, branch, device_id=device_id)
    return {"X-Device-Token": token}


async def record(db, device_id) -> Device:
    return await db.scalar(select(Device).where(Device.device_id == device_id).execution_options(populate_existing=True))


async def listed(client, user, **params) -> list:
    r = await client.get(DEVICES, params=params, headers=auth_headers(user))
    assert r.status_code == 200, r.text
    return r.json()


async def patch(client, user, device_pk, **body):
    return await client.patch(f"{DEVICES}/{device_pk}", json=body, headers=auth_headers(user))


async def revoke(client, user, device_pk):
    return await client.post(f"{DEVICES}/{device_pk}/revoke", headers=auth_headers(user))


async def test_linking_makes_a_record_that_managers_can_list(client, db, branch, manager, cashier):
    await link(client, manager, branch, "counter-1")
    row = await record(db, "counter-1")
    assert row.branch_id == branch.id and row.offline_enabled is False and row.series_letter is None
    [shown] = await listed(client, manager)
    assert shown["device_id"] == "counter-1" and shown["id"] == str(row.id) and shown["revoked_at"] is None
    assert (await client.get(DEVICES, headers=auth_headers(cashier))).status_code == 403
    assert (await client.get(DEVICES, headers=auth_headers(manager, auth_method="pin"))).status_code == 403

    named = await patch(client, manager, row.id, name="Front counter")
    assert named.status_code == 200 and named.json()["name"] == "Front counter" and named.json()["offline_enabled"] is False


async def test_a_device_linked_before_records_gets_one_on_first_use(client, db, branch, manager):
    token = create_access_token("old-tablet-1", claims={"typ": "device", "branch_id": str(branch.id)},
                                expires_delta=timedelta(days=365))
    assert await record(db, "old-tablet-1") is None
    r = await client.get(ROSTER, headers={"X-Device-Token": token})
    assert r.status_code == 200 and str(manager.id) in {e["id"] for e in r.json()}
    row = await record(db, "old-tablet-1")
    assert row is not None and row.branch_id == branch.id and row.last_seen_at is not None


async def test_last_seen_is_not_rewritten_on_every_request(client, db, branch, manager):
    device = await link(client, manager, branch)
    assert (await record(db, "counter-1")).last_seen_at is None  # linking is not contact yet
    await client.get(ROSTER, headers=device)
    first = (await record(db, "counter-1")).last_seen_at
    await client.get(ROSTER, headers=device)
    assert first is not None and (await record(db, "counter-1")).last_seen_at == first


async def test_a_revoked_device_is_refused_everywhere(client, db, branch, manager):
    device = await link(client, manager, branch)
    assert (await client.get(ROSTER, headers=device)).status_code == 200
    assert (await client.get(STATION_QUEUE, headers=device)).status_code != 401
    row = await record(db, "counter-1")

    r = await revoke(client, manager, row.id)
    assert r.status_code == 200 and r.json()["revoked_at"] is not None and r.json()["revoked_by_id"] == str(manager.id)
    again = await revoke(client, manager, row.id)  # revoking twice changes nothing
    assert again.status_code == 200 and again.json()["revoked_at"] == r.json()["revoked_at"]

    roster = await client.get(ROSTER, headers=device)
    assert roster.status_code == 401 and roster.json()["error"]["code"] == "device_revoked"
    assert (await client.get(STATION_QUEUE, headers=device)).status_code == 401
    lease = await client.post(LEASE, headers=device)
    assert lease.status_code == 401 and lease.json()["error"]["code"] == "device_revoked"

    assert await listed(client, manager) == []
    assert [d["device_id"] for d in await listed(client, manager, include_revoked="true")] == ["counter-1"]
    enable = await patch(client, manager, row.id, offline_enabled=True)
    assert enable.status_code == 409 and enable.json()["error"]["code"] == "device_revoked"


async def test_enabling_offline_gives_series_letters(client, db, redis_client, branch, manager):
    await link(client, manager, branch, "counter-1")
    await link(client, manager, branch, "counter-2")
    one, two = await record(db, "counter-1"), await record(db, "counter-2")

    await set_modules(db, redis_client, branch.organization_id, ("pos",))
    r = await patch(client, manager, one.id, offline_enabled=True)
    assert r.status_code == 403 and r.json()["error"]["code"] == "module_disabled"
    await set_modules(db, redis_client, branch.organization_id, ("pos", "offline_billing"))

    first = await patch(client, manager, one.id, offline_enabled=True)
    assert first.status_code == 200 and first.json()["series_letter"] == "A" and first.json()["offline_enabled"] is True
    second = await patch(client, manager, two.id, offline_enabled=True)
    assert second.json()["series_letter"] == "B"

    off = await patch(client, manager, one.id, offline_enabled=False)  # switching off keeps the letter
    assert off.json()["offline_enabled"] is False and off.json()["series_letter"] == "A"
    on = await patch(client, manager, one.id, offline_enabled=True)
    assert on.json()["series_letter"] == "A"


async def test_a_long_branch_code_cannot_bill_offline(client, db, branch, owner):
    long_branch = await factories.make_branch(db, code="LONG12", name="Long code")
    await link(client, owner, long_branch, "counter-long")
    row = await record(db, "counter-long")
    r = await client.patch(f"{DEVICES}/{row.id}", json={"offline_enabled": True}, headers=auth_headers(owner))
    assert r.status_code == 422 and r.json()["error"]["code"] == "branch_code_too_long"
    assert (await record(db, "counter-long")).series_letter is None


async def test_a_letter_is_never_given_twice(client, db, branch, manager):
    for n in (1, 2, 3):
        await link(client, manager, branch, f"counter-{n}")
    one, two, three = [await record(db, f"counter-{n}") for n in (1, 2, 3)]
    assert (await patch(client, manager, one.id, offline_enabled=True)).json()["series_letter"] == "A"
    await revoke(client, manager, one.id)
    assert (await record(db, "counter-1")).series_letter == "A"  # kept by the revoked device
    assert (await patch(client, manager, two.id, offline_enabled=True)).json()["series_letter"] == "B"
    assert (await patch(client, manager, three.id, offline_enabled=True)).json()["series_letter"] == "C"


async def test_no_letter_left(client, db, branch, manager):
    db.add_all(Device(branch_id=branch.id, device_id=f"used-{letter}", series_letter=letter)
               for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    await db.commit()
    await link(client, manager, branch, "counter-27")
    r = await patch(client, manager, (await record(db, "counter-27")).id, offline_enabled=True)
    assert r.status_code == 409 and r.json()["error"]["code"] == "no_series_left"


async def test_another_business_cannot_see_or_change_the_device(client, db, branch, manager):
    await link(client, manager, branch, "counter-1")
    row = await record(db, "counter-1")
    _, their_branch = await other_business(db)
    their_manager = await factories.make_staff(db, their_branch, role="manager", pin="7001", username="their_manager")
    assert (await patch(client, their_manager, row.id, name="Mine now")).status_code == 404
    assert (await revoke(client, their_manager, row.id)).status_code == 404
    assert await listed(client, their_manager) == [] and await listed(client, their_manager, include_revoked="true") == []
    theirs = await client.get(DEVICES, params={"branch_id": str(branch.id)}, headers=auth_headers(their_manager))
    assert theirs.status_code == 403
    after = await record(db, "counter-1")
    assert after.name is None and after.revoked_at is None


async def test_a_revoked_device_id_cannot_be_linked_again(client, db, branch, manager):
    await link(client, manager, branch, "counter-1")
    await revoke(client, manager, (await record(db, "counter-1")).id)
    code = await _make_code(client, manager, branch)
    taken = await client.post(f"{API}/staff/devices/link", json={"code": code, "device_id": "counter-1"})
    assert taken.status_code == 409 and taken.json()["error"]["code"] == "device_id_taken"
    fresh = await client.post(f"{API}/staff/devices/link", json={"code": code, "device_id": "counter-1b"})
    assert fresh.status_code == 200  # the code was not used up by the refusal
    assert (await record(db, "counter-1b")).branch_id == branch.id


async def test_a_device_id_of_another_branch_cannot_be_linked_here(client, db, branch, other_branch, owner, manager):
    await link(client, owner, other_branch, "counter-1")
    code = await _make_code(client, manager, branch)
    r = await client.post(f"{API}/staff/devices/link", json={"code": code, "device_id": "counter-1"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "device_id_taken"
    again = await client.post(f"{API}/staff/devices/link",
                              json={"code": await _make_code(client, owner, other_branch), "device_id": "counter-1"})
    assert again.status_code == 200  # same branch, not revoked: the record is reused
    assert await db.scalar(select(func.count()).select_from(Device)) == 1


# --- step 4: the business's own switch and limits ---------------------------------------------------------------------------


async def test_offline_settings_defaults_and_who_may_change_them(client, db, branch, owner, manager, cashier):
    r = await client.get(OFFLINE, headers=auth_headers(manager))
    assert r.status_code == 200
    assert r.json() == {"offline_allowed": False, "offline_max_hours": 24, "offline_max_sales": 300,
                        "offline_max_cash": "50000.00", "module_enabled": True}
    assert (await client.get(OFFLINE, headers=auth_headers(cashier))).status_code == 403
    assert (await client.patch(OFFLINE, json={"offline_allowed": True}, headers=auth_headers(manager))).status_code == 403

    r = await client.patch(OFFLINE, json={"offline_allowed": True, "offline_max_hours": 12}, headers=auth_headers(owner))
    assert r.status_code == 200
    body = r.json()
    assert body["offline_allowed"] is True and body["offline_max_hours"] == 12 and body["offline_max_sales"] == 300
    r = await client.patch(OFFLINE, json={"offline_max_cash": "20000"}, headers=auth_headers(owner))
    assert r.json()["offline_max_cash"] == "20000.00" and r.json()["offline_max_hours"] == 12 and r.json()["offline_allowed"]


async def test_offline_limits_are_checked(client, db, branch, owner):
    for bad in ({"offline_max_hours": 0}, {"offline_max_hours": 73}, {"offline_max_sales": 0},
                {"offline_max_sales": 2001}, {"offline_max_cash": "0"}, {"offline_max_cash": "500000.01"},
                {"offline_allowed": None}):
        assert (await client.patch(OFFLINE, json=bad, headers=auth_headers(owner))).status_code == 422, bad
    ok = await client.patch(OFFLINE, json={"offline_max_hours": 72, "offline_max_sales": 2000, "offline_max_cash": "500000"},
                            headers=auth_headers(owner))
    assert ok.status_code == 200


async def test_offline_needs_the_module_to_be_switched_on(client, db, redis_client, branch, owner):
    await client.patch(OFFLINE, json={"offline_allowed": True}, headers=auth_headers(owner))
    await set_modules(db, redis_client, branch.organization_id, ("pos",))
    assert (await client.get(OFFLINE, headers=auth_headers(owner))).json()["module_enabled"] is False
    off = await client.patch(OFFLINE, json={"offline_allowed": False, "offline_max_sales": 50}, headers=auth_headers(owner))
    assert off.status_code == 200 and off.json()["offline_allowed"] is False  # turning off is always allowed
    on = await client.patch(OFFLINE, json={"offline_allowed": True}, headers=auth_headers(owner))
    assert on.status_code == 403 and on.json()["error"]["code"] == "module_disabled"


async def test_another_business_offline_settings_are_separate(client, db, branch, owner):
    their_org, their_branch = await other_business(db)
    their_owner = await factories.make_staff(db, their_branch, role="owner", pin="7000", username="their_owner")
    r = await client.patch(OFFLINE, json={"offline_allowed": True, "offline_max_hours": 5}, headers=auth_headers(their_owner))
    assert r.status_code == 200
    mine = (await client.get(OFFLINE, headers=auth_headers(owner))).json()
    assert mine["offline_allowed"] is False and mine["offline_max_hours"] == 24
    theirs = await db.scalar(select(Organization).where(Organization.id == their_org.id)
                             .execution_options(populate_existing=True))
    assert theirs.offline_allowed is True and theirs.offline_max_hours == 5


# --- step 5: the lease ------------------------------------------------------------------------------------------------------


async def ready(client, db, owner, manager, branch, device_id="counter-1") -> dict:
    """A device with all three switches on."""
    device = await link(client, manager, branch, device_id)
    r = await client.patch(OFFLINE, json={"offline_allowed": True}, headers=auth_headers(owner))
    assert r.status_code == 200, r.text
    r = await patch(client, manager, (await record(db, device_id)).id, offline_enabled=True)
    assert r.status_code == 200, r.text
    return device


def refused(r, reason: str) -> bool:
    error = r.json().get("error", {})
    return r.status_code == 403 and error.get("code") == "offline_not_allowed" and error["details"]["reason"] == reason


async def _set_org_status(db, redis_client, org_id, status):
    org = await db.scalar(select(Organization).where(Organization.id == org_id).execution_options(populate_existing=True))
    org.status = status
    await db.commit()
    await invalidate_entitlements(redis_client, org_id)


async def _set_subscription(db, redis_client, org_id, status):
    sub = await db.scalar(select(Subscription).where(Subscription.organization_id == org_id)
                          .execution_options(populate_existing=True))
    sub.status = status
    await db.commit()
    await invalidate_entitlements(redis_client, org_id)


async def test_a_lease_for_a_device_with_every_switch_on(client, db, branch, owner, manager):
    await client.patch(OFFLINE, json={"offline_max_hours": 10, "offline_max_sales": 120, "offline_max_cash": "15000"},
                       headers=auth_headers(owner))
    device = await ready(client, db, owner, manager, branch)
    r = await client.post(LEASE, headers=device)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["device_id"] == "counter-1" and body["branch_id"] == str(branch.id) and body["series_letter"] == "A"
    assert body["max_sales"] == 120 and body["max_cash"] == "15000.00"
    issued, expires = datetime.fromisoformat(body["issued_at"]), datetime.fromisoformat(body["expires_at"])
    assert expires - issued == timedelta(hours=10)

    claims = decode_offline_lease(body["lease"])
    assert claims["sub"] == "counter-1" and claims["typ"] == "offline_lease" and claims["series"] == "A"
    assert claims["branch_id"] == str(branch.id) and claims["org"] == str(branch.organization_id)  # only its own business
    assert claims["max_sales"] == 120 and claims["max_cash"] == "15000.00"
    assert claims["exp"] - claims["iat"] == 10 * 3600


async def test_each_switch_refuses_the_lease(client, db, redis_client, branch, owner, manager):
    device = await ready(client, db, owner, manager, branch)
    assert (await client.post(LEASE, headers=device)).status_code == 200

    await _set_subscription(db, redis_client, branch.organization_id, "expired")
    assert refused(await client.post(LEASE, headers=device), "subscription_inactive")
    await _set_subscription(db, redis_client, branch.organization_id, "active")

    await set_modules(db, redis_client, branch.organization_id, ("pos",))
    assert refused(await client.post(LEASE, headers=device), "module_disabled")
    await set_modules(db, redis_client, branch.organization_id, ("pos", "offline_billing"))
    assert (await client.post(LEASE, headers=device)).status_code == 200

    await client.patch(OFFLINE, json={"offline_allowed": False}, headers=auth_headers(owner))
    assert refused(await client.post(LEASE, headers=device), "business_disabled")
    await client.patch(OFFLINE, json={"offline_allowed": True}, headers=auth_headers(owner))

    await patch(client, manager, (await record(db, "counter-1")).id, offline_enabled=False)
    assert refused(await client.post(LEASE, headers=device), "device_disabled")
    await patch(client, manager, (await record(db, "counter-1")).id, offline_enabled=True)
    assert (await client.post(LEASE, headers=device)).status_code == 200


async def test_the_platform_kill_switch_refuses_the_lease(client, db, redis_client, branch, owner, manager):
    device = await ready(client, db, owner, manager, branch)
    db.add(PlatformModule(module_key="offline_billing", enabled=False, reason="incident"))
    await db.commit()
    await invalidate_entitlements(redis_client, branch.organization_id)
    assert refused(await client.post(LEASE, headers=device), "module_disabled")


async def test_a_suspended_or_lapsed_business_gets_no_lease(client, db, redis_client, branch, owner, manager):
    device = await ready(client, db, owner, manager, branch)
    await _set_org_status(db, redis_client, branch.organization_id, "suspended")
    assert refused(await client.post(LEASE, headers=device), "subscription_inactive")
    await _set_org_status(db, redis_client, branch.organization_id, "active")
    assert (await client.post(LEASE, headers=device)).status_code == 200
    await _set_subscription(db, redis_client, branch.organization_id, "cancelled")
    assert refused(await client.post(LEASE, headers=device), "subscription_inactive")


async def test_a_lease_is_never_a_login_or_a_device_token(client, db, branch, owner, manager):
    device = await ready(client, db, owner, manager, branch)
    lease = (await client.post(LEASE, headers=device)).json()["lease"]
    assert (await client.get(f"{API}/staff/me", headers={"Authorization": f"Bearer {lease}"})).status_code == 401
    assert (await client.get(ROSTER, headers={"X-Device-Token": lease})).status_code == 401
    assert (await client.post(LEASE, headers={"X-Device-Token": lease})).status_code == 401
    assert (await client.post(LEASE, headers=auth_headers(manager))).status_code == 401  # a staff session is not a device
    assert decode_offline_lease(device["X-Device-Token"]) is None
    assert decode_offline_lease(auth_headers(manager)["Authorization"].split()[1]) is None
    assert decode_offline_lease("garbage") is None
