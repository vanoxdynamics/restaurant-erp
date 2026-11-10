import re
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


async def test_delete_staff_rules(client, db, owner, branch, other_branch, manager, waiter, cashier):
    # A waiter cannot delete anyone; a manager cannot delete themselves or a peer's branch.
    assert (await client.delete(f"{API}/staff/{cashier.id}", headers=auth_headers(waiter))).status_code == 403
    assert (await client.delete(f"{API}/staff/{manager.id}", headers=auth_headers(manager))).status_code == 403
    elsewhere = await factories.make_staff(db, other_branch, pin="1313")
    assert (await client.delete(f"{API}/staff/{elsewhere.id}", headers=auth_headers(manager))).status_code == 404
    assert (await client.delete(f"{API}/staff/{owner.id}", headers=auth_headers(owner))).status_code == 403

    # A manager deletes a brand-new staff member in their branch.
    fresh = await factories.make_staff(db, branch, pin="2468")
    r = await client.delete(f"{API}/staff/{fresh.id}", headers=auth_headers(manager))
    assert r.status_code == 204, r.text
    assert await db.scalar(select(StaffUser).where(StaffUser.id == fresh.id)) is None

    # The owner can delete across branches.
    r = await client.delete(f"{API}/staff/{elsewhere.id}", headers=auth_headers(owner))
    assert r.status_code == 204, r.text


async def test_delete_staff_with_history_is_refused(client, db, branch, manager, cashier):
    await open_shift(client, cashier, "100.00")
    r = await client.delete(f"{API}/staff/{cashier.id}", headers=auth_headers(manager))
    assert r.status_code == 409 and r.json()["error"]["code"] == "staff_has_history"
    assert await db.scalar(select(StaffUser).where(StaffUser.id == cashier.id)) is not None


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


# --- Who's-working sign-in (device token, staff roster, per-person PIN) ----------------


async def _make_code(client, manager, branch) -> str:
    r = await client.post(f"{API}/staff/devices/setup-codes", json={"branch_id": str(branch.id)},
                          headers=auth_headers(manager))
    assert r.status_code == 201, r.text
    return r.json()["code"]


async def _link_device(client, manager, branch, device_id="pos-test-1"):
    code = await _make_code(client, manager, branch)
    r = await client.post(f"{API}/staff/devices/link", json={"code": code, "device_id": device_id})
    assert r.status_code == 200, r.text
    return r.json()["device_token"]


async def test_only_a_manager_password_session_can_make_a_setup_code(client, branch, manager, waiter):
    body = {"branch_id": str(branch.id)}
    url = f"{API}/staff/devices/setup-codes"
    assert (await client.post(url, json=body, headers=auth_headers(waiter))).status_code == 403
    assert (await client.post(url, json=body, headers=auth_headers(manager, auth_method="pin"))).status_code == 403
    assert (await client.post(url, json=body)).status_code == 401
    ok = await client.post(url, json=body, headers=auth_headers(manager))
    assert ok.status_code == 201
    assert re.fullmatch(r"[A-Z2-9]{4}-[A-Z2-9]{4}", ok.json()["code"]) and ok.json()["expires_in"] == 900


async def test_setup_code_links_one_device_once(client, branch, manager):
    code = await _make_code(client, manager, branch)
    # No sign-in needed, and the code is read case- and dash-insensitively.
    first = await client.post(f"{API}/staff/devices/link",
                              json={"code": code.lower().replace("-", ""), "device_id": "pos-a"})
    assert first.status_code == 200
    assert first.json()["branch_id"] == str(branch.id) and first.json()["branch_name"] == branch.name
    claims = decode_access_token(first.json()["device_token"])
    assert claims["typ"] == "device" and claims["branch_id"] == str(branch.id)

    reuse = await client.post(f"{API}/staff/devices/link", json={"code": code, "device_id": "pos-b"})
    assert reuse.status_code == 401 and reuse.json()["error"]["code"] == "invalid_setup_code"


async def test_wrong_setup_codes_are_rate_limited(client, branch, manager):
    for _ in range(5):
        r = await client.post(f"{API}/staff/devices/link", json={"code": "AAAA-BBBB", "device_id": "pos-a"})
        assert r.status_code == 401
    code = await _make_code(client, manager, branch)
    locked = await client.post(f"{API}/staff/devices/link", json={"code": code, "device_id": "pos-a"})
    assert locked.status_code == 429  # even a real code is refused while this address is locked out


async def test_roster_needs_a_device_token_and_shows_only_this_branch(
    client, db, branch, other_branch, manager, waiter, kitchen
):
    outsider = await factories.make_staff(db, other_branch, role="waiter", pin="4444")
    inactive = await factories.make_staff(db, branch, role="waiter", pin="5555", is_active=False)
    token = await _link_device(client, manager, branch)

    r = await client.get(f"{API}/staff/auth/roster", headers={"X-Device-Token": token})
    assert r.status_code == 200
    ids = {e["id"] for e in r.json()}
    assert {str(waiter.id), str(kitchen.id), str(manager.id)} <= ids
    assert str(outsider.id) not in ids and str(inactive.id) not in ids
    assert set(r.json()[0]) == {"id", "full_name", "role", "pin_length"}  # no PIN, username or other details

    assert (await client.get(f"{API}/staff/auth/roster")).status_code == 401
    assert (await client.get(f"{API}/staff/auth/roster", headers={"X-Device-Token": "garbage"})).status_code == 401
    # A user's own session token is not a device token.
    user_token = auth_headers(manager)["Authorization"].split()[1]
    assert (await client.get(f"{API}/staff/auth/roster", headers={"X-Device-Token": user_token})).status_code == 401


async def test_device_token_is_not_a_user_session(client, branch, manager):
    token = await _link_device(client, manager, branch)
    r = await client.get(f"{API}/staff/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


async def test_pin_login_for_a_chosen_person(client, db, branch, other_branch, waiter, kitchen):
    ok = await client.post(f"{API}/staff/auth/pin",
                           json={"pin": "9004", "branch_id": str(branch.id), "staff_id": str(waiter.id)})
    assert ok.status_code == 200 and ok.json()["user_id"] == str(waiter.id)

    # Someone else's PIN does not work for this person, even though that PIN is valid in the branch.
    wrong = await client.post(f"{API}/staff/auth/pin",
                              json={"pin": "9005", "branch_id": str(branch.id), "staff_id": str(waiter.id)})
    assert wrong.status_code == 401

    # A person from another branch, or an inactive one, cannot sign in here.
    elsewhere = await factories.make_staff(db, other_branch, role="waiter", pin="4444")
    r = await client.post(f"{API}/staff/auth/pin",
                          json={"pin": "4444", "branch_id": str(branch.id), "staff_id": str(elsewhere.id)})
    assert r.status_code == 401


async def test_lockout_follows_the_person_not_the_device(client, db, branch, waiter, kitchen):
    for attempt in range(5):  # a fresh device id each time does not reset the count
        r = await client.post(
            f"{API}/staff/auth/pin",
            json={"pin": "0000", "branch_id": str(branch.id), "staff_id": str(waiter.id), "device_id": f"d{attempt}"},
        )
        assert r.status_code == 401
    locked = await client.post(
        f"{API}/staff/auth/pin",
        json={"pin": "9004", "branch_id": str(branch.id), "staff_id": str(waiter.id), "device_id": "brand-new"},
    )
    assert locked.status_code == 429  # even the right PIN is refused while locked
    assert await db.scalar(select(AuditLog).where(AuditLog.action_type == "login_lockout"))

    # Everyone else is unaffected.
    other = await client.post(f"{API}/staff/auth/pin",
                              json={"pin": "9005", "branch_id": str(branch.id), "staff_id": str(kitchen.id),
                                    "device_id": "brand-new"})
    assert other.status_code == 200


async def test_roster_tells_the_pad_how_many_digits_each_pin_has(client, branch, manager):
    token = await _link_device(client, manager, branch)
    body = {"username": "sixdigit", "full_name": "Six Digit", "role": "waiter", "pin": "246813"}
    created = await client.post(f"{API}/staff", json=body, headers=auth_headers(manager))
    assert created.status_code == 201, created.text
    lengths = lambda r: {e["id"]: e["pin_length"] for e in r.json()}  # noqa: E731

    roster = await client.get(f"{API}/staff/auth/roster", headers={"X-Device-Token": token})
    assert lengths(roster)[created.json()["id"]] == 6
    assert lengths(roster)[str(manager.id)] is None  # set before lengths were kept: the pad grows as before

    r = await client.post(f"{API}/staff/{created.json()['id']}/reset-pin", json={"pin": "1357"}, headers=auth_headers(manager))
    assert r.status_code == 200, r.text
    roster = await client.get(f"{API}/staff/auth/roster", headers={"X-Device-Token": token})
    assert lengths(roster)[created.json()["id"]] == 4


# --- Cash day (one per branch, opened and closed at the counter) ----------------


async def test_cash_day_is_shared_by_the_branch_and_counts_everyones_cash(client, db, branch, cashier, manager, waiter):
    url = f"{API}/staff/cash-day"
    assert (await client.get(url, headers=auth_headers(cashier))).json()["status"] == "not_open"
    assert (await client.post(f"{url}/close", json={"counted_cash": "0"}, headers=auth_headers(cashier))).json()["error"]["code"] == "day_not_open"

    opened = await client.post(f"{url}/open", json={"opening_cash": "500.00"}, headers=auth_headers(cashier))
    assert opened.status_code == 201, opened.text
    assert opened.json()["status"] == "open" and opened.json()["opened_by"] == cashier.full_name and not opened.json()["stale"]
    # One day per branch: the manager sees the same day and cannot open a second one.
    again = await client.post(f"{url}/open", json={"opening_cash": "0"}, headers=auth_headers(manager))
    assert again.status_code == 409 and again.json()["error"]["code"] == "day_already_open"
    assert (await client.get(url, headers=auth_headers(manager))).json()["id"] == opened.json()["id"]

    # Cash taken by two different people both count towards the day.
    item = await factories.make_menu_item(db, price="100.00", tax_rate="0.00")
    for taker in (cashier, manager):
        order = await create_order(client, waiter, items=[line(item)])
        await fire(client, waiter, order["id"])
        b = await bill(client, taker, order["id"])
        assert (await pay(client, taker, b["id"], b["grand_total"])).status_code == 200

    # The count is blind for the cashier; the manager sees expected cash and the difference.
    closed = await client.post(f"{url}/close", json={"counted_cash": "690.00"}, headers=auth_headers(cashier))
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "closed" and "expected_cash" not in {k for k, v in closed.json().items() if v is not None}
    assert (await client.get(url, headers=auth_headers(manager))).json()["status"] == "not_open"
    history = (await client.get(f"{API}/staff/shifts", headers=auth_headers(manager))).json()["items"]
    day = next(h for h in history if h["id"] == opened.json()["id"])
    assert Decimal(day["expected_cash"]) == Decimal("700.00") and Decimal(day["cash_variance"]) == Decimal("-10.00")


async def test_cash_day_left_open_from_yesterday_is_flagged(client, db, branch, cashier):
    import uuid
    from datetime import timedelta

    from app.models import Shift
    r = await client.post(f"{API}/staff/cash-day/open", json={"opening_cash": "0"}, headers=auth_headers(cashier))
    shift = await db.get(Shift, uuid.UUID(r.json()["id"]))
    shift.start_time = shift.start_time - timedelta(days=1)
    await db.commit()
    assert (await client.get(f"{API}/staff/cash-day", headers=auth_headers(cashier))).json()["stale"] is True


async def test_waiters_handle_the_cash_day_only_without_a_kitchen_screen(client, db, redis_client, branch, waiter):
    from tests.modules.test_kitchen_module import SLIP, set_modules
    url = f"{API}/staff/cash-day"
    assert (await client.get(url, headers=auth_headers(waiter))).status_code == 403
    await set_modules(db, redis_client, branch.organization_id, SLIP)
    assert (await client.get(url, headers=auth_headers(waiter))).status_code == 200
