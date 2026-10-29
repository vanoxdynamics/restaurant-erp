from decimal import Decimal

from sqlalchemy import select

from app.core.security import decode_access_token
from app.models import AuditLog, StaffUser
from tests import factories
from tests.factories import auth_headers
from tests.flows import API, bill, create_order, fire, line, open_shift, pay


async def test_pin_login_success_and_claims(client, branch, waiter):
    r = await client.post(f"{API}/staff/auth/pin", json={"pin": "9004", "branch_id": str(branch.id)})
    assert r.status_code == 200, r.text
    body = r.json()
    claims = decode_access_token(body["access_token"])
    assert body["role"] == "waiter" and claims["amr"] == "pin" and claims["branch_id"] == str(branch.id)


async def test_pin_login_failures(client, db, branch, other_branch, waiter):
    bad = await client.post(f"{API}/staff/auth/pin", json={"pin": "0000", "branch_id": str(branch.id)})
    assert bad.status_code == 401
    wrong_branch = await client.post(f"{API}/staff/auth/pin", json={"pin": "9004", "branch_id": str(other_branch.id)})
    assert wrong_branch.status_code == 401
    inactive = await factories.make_staff(db, branch, pin="7777", is_active=False)
    assert inactive
    r = await client.post(f"{API}/staff/auth/pin", json={"pin": "7777", "branch_id": str(branch.id)})
    assert r.status_code == 401


async def test_pin_login_lockout_and_audit(client, db, branch, waiter):
    body = {"pin": "0000", "branch_id": str(branch.id), "device_id": "tablet-1"}
    for _ in range(5):
        assert (await client.post(f"{API}/staff/auth/pin", json=body)).status_code == 401
    r = await client.post(f"{API}/staff/auth/pin", json={**body, "pin": "9004"})
    assert r.status_code == 429
    assert "retry-after" in r.headers
    assert await db.scalar(select(AuditLog).where(AuditLog.action_type == "login_lockout"))


async def test_password_login(client, manager):
    ok = await client.post(f"{API}/staff/auth/login", json={"username": "MANAGER1", "password": "correct-horse-battery"})
    assert ok.status_code == 200, ok.text
    assert decode_access_token(ok.json()["access_token"])["amr"] == "pwd"
    bad = await client.post(f"{API}/staff/auth/login", json={"username": "manager1", "password": "nope"})
    assert bad.status_code == 401
    unknown = await client.post(f"{API}/staff/auth/login", json={"username": "ghost", "password": "nope"})
    assert unknown.status_code == 401


async def test_create_staff_rules(client, db, branch, other_branch, manager, waiter):
    payload = {"username": "newwaiter", "full_name": "New Waiter", "role": "waiter", "pin": "4455"}
    r = await client.post(f"{API}/staff", json=payload, headers=auth_headers(manager))
    assert r.status_code == 201, r.text
    assert r.json()["branch_id"] == str(branch.id)

    # The original review finding: anyone could create an owner.
    owner_body = {"username": "evil", "full_name": "E", "role": "owner", "pin": "1111", "password": "x" * 12}
    assert (await client.post(f"{API}/staff", json=owner_body)).status_code == 401
    assert (await client.post(f"{API}/staff", json=owner_body, headers=auth_headers(waiter))).status_code == 403
    assert (await client.post(f"{API}/staff", json=owner_body, headers=auth_headers(manager))).status_code == 403
    peer = {**owner_body, "username": "peer", "role": "manager"}
    assert (await client.post(f"{API}/staff", json=peer, headers=auth_headers(manager))).status_code == 403
    elsewhere = {**payload, "username": "elsewhere", "pin": "6677", "branch_id": str(other_branch.id)}
    assert (await client.post(f"{API}/staff", json=elsewhere, headers=auth_headers(manager))).status_code == 403

    dup_user = await client.post(f"{API}/staff", json={**payload, "pin": "8899"}, headers=auth_headers(manager))
    assert dup_user.status_code == 409 and dup_user.json()["error"]["code"] == "username_taken"
    dup_pin = await client.post(f"{API}/staff", json={**payload, "username": "other"}, headers=auth_headers(manager))
    assert dup_pin.status_code == 409 and dup_pin.json()["error"]["code"] == "pin_taken"


async def test_owner_can_create_manager_and_deactivation_revokes(client, db, owner, branch):
    body = {"username": "mgr2", "full_name": "M2", "role": "manager", "pin": "3434", "password": "managerpass1"}
    r = await client.post(f"{API}/staff", json=body, headers=auth_headers(owner))
    assert r.status_code == 201, r.text
    created = await db.get(StaffUser, __import__("uuid").UUID(r.json()["id"]))
    headers = auth_headers(created)
    assert (await client.get(f"{API}/staff/me", headers=headers)).status_code == 200
    r = await client.patch(f"{API}/staff/{created.id}", json={"is_active": False}, headers=auth_headers(owner))
    assert r.status_code == 200
    assert (await client.get(f"{API}/staff/me", headers=headers)).status_code == 401


async def test_list_staff_is_branch_scoped(client, db, branch, other_branch, manager, waiter):
    await factories.make_staff(db, other_branch, pin="1212")
    r = await client.get(f"{API}/staff", headers=auth_headers(manager))
    assert r.status_code == 200
    assert {u["branch_id"] for u in r.json()["items"]} == {str(branch.id)}


async def test_shift_blind_drop(client, db, cashier, manager, waiter):
    shift = await open_shift(client, cashier, "500.00")
    again = await client.post(f"{API}/staff/shifts/open", json={"opening_cash": "0"}, headers=auth_headers(cashier))
    assert again.status_code == 409

    item = await factories.make_menu_item(db, price="100.00")
    order = await create_order(client, waiter, items=[line(item)])
    await fire(client, waiter, order["id"])
    b = await bill(client, cashier, order["id"])
    assert (await pay(client, cashier, b["id"], "50.00", "card", reference="AUTH1")).status_code == 200
    assert (await pay(client, cashier, b["id"], b["balance_due"] and "55.00")).status_code == 200

    r = await client.post(f"{API}/staff/shifts/close", json={"blind_drop_cash": "550.00"}, headers=auth_headers(cashier))
    assert r.status_code == 200, r.text
    closed = r.json()
    assert closed["status"] == "closed" and "expected_cash" not in closed  # drop stays blind

    from app.models import Shift
    record = await db.get(Shift, __import__("uuid").UUID(shift["id"]))
    await db.refresh(record)
    # Only cash counts: 500 opening + 55 cash = 555 expected; 550 dropped.
    assert record.expected_cash == Decimal("555.00")
    assert record.cash_variance == Decimal("-5.00")


async def test_manager_sees_reconciliation(client, manager):
    await open_shift(client, manager, "100.00")
    r = await client.post(f"{API}/staff/shifts/close", json={"blind_drop_cash": "100.00"}, headers=auth_headers(manager))
    assert r.status_code == 200
    assert r.json()["expected_cash"] == "100.00" and r.json()["cash_variance"] == "0.00"
