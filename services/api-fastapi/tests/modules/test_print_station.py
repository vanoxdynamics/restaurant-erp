"""
The counter device as the branch's print station: it prints waiters' KOTs with its own device token, so printing carries on
while no one is signed in on it. It can only see and confirm its own branch's KOTs, and nothing it does is put on a person.
"""

import pytest
from sqlalchemy import select

from app.models import KOT, AuditLog
from tests import factories
from tests.factories import auth_headers, idem
from tests.flows import API
from tests.modules.test_kitchen_module import SLIP, fired_ticket, set_modules
from tests.modules.test_staff import _link_device

STATION = f"{API}/kots/station"


@pytest.fixture
async def go_slip(db, redis_client, branch):
    """Put the organization on a plan without the kitchen display."""
    await set_modules(db, redis_client, branch.organization_id, SLIP)


@pytest.fixture
async def device(client, manager, branch):
    return {"X-Device-Token": await _link_device(client, manager, branch, device_id="counter-1")}


async def test_the_counter_prints_waiters_kots_without_anyone_signed_in(client, db, go_slip, device, waiter, manager):
    _, kot = await fired_ticket(client, db, waiter, name="Paneer Tikka")
    r = await client.get(f"{STATION}/print-queue", headers=device)
    assert r.status_code == 200, r.text
    assert [k["kot_number"] for k in r.json()] == [kot["kot_number"]]

    slip = await client.get(f"{STATION}/{kot['id']}/slip", params={"width": 58}, headers=device)
    assert slip.status_code == 200 and slip.json()["paper_mm"] == 58 and slip.json()["rows"]

    key = {**device, "X-Idempotency-Key": "station-print-0001"}
    first = await client.post(f"{STATION}/{kot['id']}/printed", headers=key)
    again = await client.post(f"{STATION}/{kot['id']}/printed", headers=key)  # the confirmation was retried
    assert first.status_code == again.status_code == 200 and again.json()["print_count"] == 1
    assert (await client.get(f"{STATION}/print-queue", headers=device)).json() == []

    row = await db.scalar(select(KOT).where(KOT.id == kot["id"]).execution_options(populate_existing=True))
    assert row.print_count == 1 and row.last_printed_by_id is None  # a device, not a person

    # A reprint someone asks for goes through their own session and is audited to them.
    await client.post(f"{API}/kots/{kot['id']}/slip/printed", headers=idem(manager))
    [audit] = (await db.scalars(select(AuditLog).where(AuditLog.action_type == "kot_reprint"))).all()
    assert audit.actor_id == manager.id


async def test_only_a_linked_device_and_only_its_own_branch(client, db, go_slip, device, waiter, manager, branch):
    _, kot = await fired_ticket(client, db, waiter)
    assert (await client.get(f"{STATION}/print-queue")).status_code == 401
    assert (await client.get(f"{STATION}/print-queue", headers=auth_headers(manager))).status_code == 401  # a person is not a device
    assert (await client.post(f"{STATION}/{kot['id']}/printed", headers={**device})).status_code in (400, 422)  # key required

    other_org = await factories.make_org(db, slug="globex", name="Globex")
    other_branch = await factories.make_branch(db, code="DEL1", name="Delhi", org=other_org)
    other_manager = await factories.make_staff(db, other_branch, role="manager", pin="8001", username="mgr_b")
    stranger = {"X-Device-Token": await _link_device(client, other_manager, other_branch, device_id="counter-b")}
    assert (await client.get(f"{STATION}/print-queue", headers=stranger)).json() == []
    assert (await client.get(f"{STATION}/{kot['id']}/slip", headers=stranger)).status_code == 404
    assert (await client.post(f"{STATION}/{kot['id']}/printed", headers={**stranger, "X-Idempotency-Key": "station-x-0001"})).status_code == 404
    assert branch.id


async def test_void_notices_print_at_the_station_too(client, db, go_slip, device, waiter, manager):
    _, kot = await fired_ticket(client, db, waiter)
    await client.post(f"{STATION}/{kot['id']}/printed", headers={**device, "X-Idempotency-Key": "station-print-0002"})
    r = await client.post(f"{API}/kots/{kot['id']}/void", json={"void_reason": "guest left", "approver_pin": "9001"},
                          headers=auth_headers(waiter))
    assert r.status_code == 200, r.text
    queue = (await client.get(f"{STATION}/print-queue", headers=device)).json()
    assert [(k["kot_number"], k["status"]) for k in queue] == [(kot["kot_number"], "voided")]
    assert (await client.get(f"{STATION}/{kot['id']}/slip", headers=device)).json()["marker"] == "VOID"
    await client.post(f"{STATION}/{kot['id']}/printed", headers={**device, "X-Idempotency-Key": "station-print-0003"})
    assert (await client.get(f"{STATION}/print-queue", headers=device)).json() == []
    assert manager.id
