"""
The `kds` (kitchen display) module: plans with it run the kitchen screen; plans without it work from printed slips that
staff carry, with floor staff marking them served and waiters allowed to bill and close.
"""

import itertools
import uuid

import pytest
from sqlalchemy import select

from app.core.entitlements import invalidate_entitlements
from app.models import KOT, Organization, OrganizationModule, Payment, Plan, PlanModule, Subscription
from tests import factories
from tests.factories import auth_headers, idem
from tests.flows import API, bill, create_order, fire, line, pay

_codes = itertools.count(1)


async def set_modules(db, redis_client, org_id, modules):
    """Move the organization onto a fresh plan with exactly these modules; the change is live immediately."""
    plan = Plan(code=f"test_plan_{next(_codes)}", name="Test plan")
    db.add(plan)
    await db.flush()
    db.add_all(PlanModule(plan_id=plan.id, module_key=m) for m in modules)
    sub = await db.scalar(select(Subscription).where(Subscription.organization_id == org_id))
    sub.plan_id = plan.id
    await db.commit()
    await invalidate_entitlements(redis_client, org_id)


SCREEN = ("pos", "kds")
SLIP = ("pos",)


@pytest.fixture
def go(db, redis_client, branch):
    async def switch(modules):
        await set_modules(db, redis_client, branch.organization_id, modules)
    return switch


async def fired_ticket(client, db, waiter, table=None, name="Dish"):
    item = await factories.make_menu_item(db, name=name)
    order = await create_order(client, waiter, table, items=[line(item)])
    [kot] = await fire(client, waiter, order["id"])
    return order, kot


async def set_status(client, user, kot_id, status):
    return await client.patch(f"{API}/kots/{kot_id}/status", json={"status": status}, headers=auth_headers(user))


# --- firing -------------------------------------------------------------------------------------------------------------------


async def test_the_default_plan_still_has_the_kitchen_screen(client, db, owner, waiter):
    r = await client.get(f"{API}/me/entitlements", headers=auth_headers(owner))
    assert "kds" in r.json()["modules"]
    _, kot = await fired_ticket(client, db, waiter)
    assert kot["fulfilment"] == "screen"


async def test_firing_records_how_the_ticket_reaches_the_kitchen(client, db, go, waiter):
    _, on_screen = await fired_ticket(client, db, waiter, name="Screen dish")
    await go(SLIP)
    _, on_paper = await fired_ticket(client, db, waiter, name="Paper dish")
    assert (on_screen["fulfilment"], on_paper["fulfilment"]) == ("screen", "slip")
    assert on_paper["status"] == "queued" and on_paper["items"][0]["item_name"] == "Paper dish"
    assert {k.fulfilment for k in (await db.scalars(select(KOT))).all()} == {"screen", "slip"}


# --- ticket status rules ---------------------------------------------------------------------------------------------------


async def test_on_a_slip_plan_floor_staff_mark_a_ticket_served_straight_from_queued(client, db, go, waiter, table):
    await go(SLIP)
    order, kot = await fired_ticket(client, db, waiter, table)
    r = await set_status(client, waiter, kot["id"], "served")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "served" and all(i["status"] == "served" for i in r.json()["items"])
    lines = (await client.get(f"{API}/orders/{order['id']}", headers=auth_headers(waiter))).json()["items"]
    assert {i["status"] for i in lines} == {"served"}
    assert (await set_status(client, waiter, kot["id"], "served")).status_code == 409  # already served


async def test_kitchen_stages_are_refused_without_the_module(client, db, go, waiter, manager, owner):
    await go(SLIP)
    _, kot = await fired_ticket(client, db, waiter)
    for user in (manager, owner):  # even roles that run the kitchen on a screen plan
        for stage in ("preparing", "ready"):
            r = await set_status(client, user, kot["id"], stage)
            assert r.status_code == 403 and r.json()["error"]["code"] == "module_disabled", (user.role, stage)
            assert r.json()["error"]["details"] == {"module": "kds"}


async def test_screen_plan_workflow_is_unchanged(client, db, waiter, manager):
    _, kot = await fired_ticket(client, db, waiter)
    assert (await set_status(client, waiter, kot["id"], "served")).status_code == 409  # cannot skip the kitchen stages
    assert (await set_status(client, waiter, kot["id"], "preparing")).status_code == 403
    for stage in ("preparing", "ready"):
        assert (await set_status(client, manager, kot["id"], stage)).status_code == 200
    assert (await set_status(client, waiter, kot["id"], "served")).json()["status"] == "served"


async def test_a_paper_slip_stays_a_slip_if_the_module_is_added_later(client, db, go, waiter, manager):
    await go(SLIP)
    _, kot = await fired_ticket(client, db, waiter)
    await go(SCREEN)
    r = await set_status(client, manager, kot["id"], "preparing")
    assert r.status_code == 409 and r.json()["error"]["code"] == "slip_ticket"  # the kitchen already has it on paper
    assert (await set_status(client, waiter, kot["id"], "served")).status_code == 200


async def test_a_screen_ticket_can_still_be_closed_if_the_module_is_removed(client, db, go, waiter, manager):
    _, kot = await fired_ticket(client, db, waiter)  # fired while the plan had the screen
    await go(SLIP)
    assert (await set_status(client, manager, kot["id"], "preparing")).status_code == 403
    r = await set_status(client, waiter, kot["id"], "served")  # not stranded: floor staff can close it
    assert r.status_code == 200 and r.json()["status"] == "served"


# --- the kitchen screen ----------------------------------------------------------------------------------------------------------


async def test_kitchen_screen_never_lists_paper_slips(client, db, go, waiter, kitchen):
    await go(SLIP)
    _, slip = await fired_ticket(client, db, waiter, name="Slip dish")
    await go(SCREEN)
    _, screen = await fired_ticket(client, db, waiter, name="Screen dish")
    seen_by_kitchen = (await client.get(f"{API}/kots", headers=auth_headers(kitchen))).json()
    assert [k["id"] for k in seen_by_kitchen] == [screen["id"]]
    seen_by_floor = (await client.get(f"{API}/kots", headers=auth_headers(waiter))).json()
    assert {k["id"] for k in seen_by_floor} == {slip["id"], screen["id"]}  # the floor sees every ticket to mark served


async def test_kitchen_role_has_no_screen_without_the_module(client, db, go, kitchen, waiter):
    assert (await client.get(f"{API}/kots", headers=auth_headers(kitchen))).status_code == 200
    await go(SLIP)
    r = await client.get(f"{API}/kots", headers=auth_headers(kitchen))
    assert r.status_code == 403 and r.json()["error"]["code"] == "module_disabled"
    assert (await client.get(f"{API}/kots", headers=auth_headers(waiter))).status_code == 200  # floor staff still need the list


async def test_kitchen_accounts_need_the_module(client, db, go, owner, branch, waiter):
    body = {"username": "cook01", "full_name": "Chef Cook", "role": "kitchen", "pin": "7777", "branch_id": str(branch.id)}
    await go(SLIP)
    r = await client.post(f"{API}/staff", json=body, headers=auth_headers(owner))
    assert r.status_code == 403 and r.json()["error"]["code"] == "module_disabled"
    r = await client.patch(f"{API}/staff/{waiter.id}", json={"role": "kitchen"}, headers=auth_headers(owner))
    assert r.status_code == 403 and r.json()["error"]["code"] == "module_disabled"
    await go(SCREEN)
    assert (await client.post(f"{API}/staff", json=body, headers=auth_headers(owner))).status_code == 201
    assert (await client.patch(f"{API}/staff/{waiter.id}", json={"role": "kitchen"}, headers=auth_headers(owner))).status_code == 200


async def test_other_roles_are_not_affected_by_the_kitchen_rule(client, db, go, owner, branch):
    await go(SLIP)
    body = {"username": "wait02", "full_name": "Wait Er", "role": "waiter", "pin": "6666", "branch_id": str(branch.id)}
    assert (await client.post(f"{API}/staff", json=body, headers=auth_headers(owner))).status_code == 201


# --- waiters and the bill -------------------------------------------------------------------------------------------------------


async def test_waiter_closes_the_bill_on_a_slip_plan(client, db, go, waiter, table):
    await go(SLIP)
    order, kot = await fired_ticket(client, db, waiter, table)
    b = await bill(client, waiter, order["id"])  # bill without a cashier
    assert b["status"] == "unpaid"
    listed = await client.get(f"{API}/billing/bills", params={"order_id": order["id"]}, headers=auth_headers(waiter))
    assert listed.status_code == 200 and listed.json()["total"] == 1
    r = await pay(client, waiter, b["id"], b["grand_total"])
    assert r.status_code == 200, r.text
    assert r.json()["bill"]["status"] == "paid" and r.json()["change_due"] == "0.00"
    assert (await db.scalar(select(Payment).where(Payment.bill_id == b["id"]))) is not None
    assert kot["fulfilment"] == "slip"


async def test_waiter_can_also_use_split_payment_and_upi_qr_on_a_slip_plan(client, db, go, waiter, branch):
    await go(SLIP)
    order, _ = await fired_ticket(client, db, waiter)
    b = await bill(client, waiter, order["id"])
    r = await client.get(f"{API}/billing/bills/{b['id']}/upi-qr", headers=auth_headers(waiter))
    assert r.status_code in (200, 409, 422)  # allowed through the role check; 409/422 only if the branch has no UPI id configured
    assert r.status_code != 403
    half = str(round(float(b["grand_total"]) / 2, 2))
    split = {"payments": [{"payment_mode": "cash", "amount": half}, {"payment_mode": "card", "amount": str(round(float(b["grand_total"]) - float(half), 2)),
                                                                  "reference_id": "CARD-1"}]}
    r = await client.post(f"{API}/billing/bills/{b['id']}/split-payments", json=split, headers=idem(waiter))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "paid"


async def test_waiters_cannot_bill_on_a_screen_plan(client, db, go, waiter):
    order, _ = await fired_ticket(client, db, waiter)  # default plan has the kitchen display
    assert (await client.post(f"{API}/billing/bills", json={"order_id": order["id"]}, headers=idem(waiter))).status_code == 403
    assert (await client.get(f"{API}/billing/bills", headers=auth_headers(waiter))).status_code == 403
    await go(SLIP)
    assert (await client.post(f"{API}/billing/bills", json={"order_id": order["id"]}, headers=idem(waiter))).status_code == 201
    await go(SCREEN)  # and the permission goes away again as soon as the plan does
    assert (await client.get(f"{API}/billing/bills", headers=auth_headers(waiter))).status_code == 403


async def test_waiters_keep_the_controls_cashiers_have_on_a_slip_plan(client, db, go, waiter, manager, cashier):
    await go(SLIP)
    order, _ = await fired_ticket(client, db, waiter)
    body = {"order_id": order["id"], "discount_amount": "99.00", "discount_reason": "regular"}
    denied = await client.post(f"{API}/billing/bills", json=body, headers=idem(waiter))
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "approval_required"  # big discounts still need a manager
    ok = await client.post(f"{API}/billing/bills", json={**body, "approver_pin": "9001"}, headers=idem(waiter))
    assert ok.status_code == 201, ok.text

    b = ok.json()
    void = f"{API}/billing/bills/{b['id']}/void"
    r = await client.post(void, json={"reason": "wrong table", "approver_pin": "9001"}, headers=auth_headers(waiter))
    assert r.status_code == 403  # voiding a bill stays with managers and cashiers whatever the plan
    r = await client.post(void, json={"reason": "wrong table", "approver_pin": "9001"}, headers=auth_headers(cashier))
    assert r.status_code == 200
    assert manager.id


async def test_other_roles_keep_their_access_on_both_plans(client, db, go, waiter, cashier, owner):
    order, _ = await fired_ticket(client, db, waiter)
    for modules in (SCREEN, SLIP):
        await go(modules)
        for user in (cashier, owner):
            assert (await client.get(f"{API}/billing/bills", headers=auth_headers(user))).status_code == 200


async def test_staff_not_in_the_billing_roles_are_still_refused(client, db, go, waiter, kitchen):
    await go(SLIP)
    order, _ = await fired_ticket(client, db, waiter)
    assert (await client.get(f"{API}/billing/bills", headers=auth_headers(kitchen))).status_code == 403
    assert (await client.post(f"{API}/billing/bills", json={"order_id": order["id"]}, headers=idem(kitchen))).status_code == 403


# --- operators decide it from the console --------------------------------------------------------------------------------------------


async def test_operators_pick_screen_or_slip_through_plans_and_overrides(client, db):
    from tests.platform_helpers import P, create_org, make_plan, operator, org_body, tenant_login

    ops, _ = await operator(client, db)
    modules = {m["key"]: m for m in (await client.get(f"{P}/modules", headers=ops)).json()}
    assert modules["kds"]["depends_on"] == ["pos"] and modules["kds"]["is_core"] is False

    slip_plan = await make_plan(db, "standard", modules=("pos",), monthly="500.00", yearly="5000.00")
    org = await create_org(client, ops, slip_plan, start="active", key="kds-key-0001")
    assert {m["key"]: m["enabled"] for m in org["modules"]}["kds"] is False
    tenant = await tenant_login(client, org_body(slip_plan))

    org_row = await db.get(Organization, uuid.UUID(org["id"]))
    soup = await factories.make_menu_item(db, name="Soup", category=await factories.make_category(db, org=org_row))

    async def new_ticket():
        r = await client.post(f"{API}/orders", json={"order_type": "takeaway", "items": [line(soup)]},
                              headers={**tenant, "X-Idempotency-Key": f"order-key-{next(_codes)}"})
        assert r.status_code == 201, r.text
        k = await client.post(f"{API}/kots/fire", json={"order_id": r.json()["id"]}, headers={**tenant, "X-Idempotency-Key": f"fire-key-{next(_codes)}"})
        assert k.status_code == 201, k.text
        return k.json()[0]

    assert (await new_ticket())["fulfilment"] == "slip"
    r = await client.put(f"{P}/organizations/{org['id']}/modules/kds", json={"enabled": True, "reason": "pilot of the kitchen screen"}, headers=ops)
    assert r.status_code == 200 and {m["key"]: m["enabled"] for m in r.json()["modules"]}["kds"] is True
    assert (await new_ticket())["fulfilment"] == "screen"  # live, no re-login
    r = await client.delete(f"{P}/organizations/{org['id']}/modules/kds", params={"reason": "pilot ended"}, headers=ops)
    assert (await new_ticket())["fulfilment"] == "slip"


async def test_new_business_from_the_command_line_starts_without_the_kitchen_display(db):
    """The kitchen display is an add-on switched on from the platform console; a new business starts with printed KOTs."""
    from app.cli import create_owner
    from app.core.entitlements import compute_entitlements

    await create_owner("cli_owner", "Cli Owner", "CLI1", "Main", "cli-owner-password", "1234", "cli-biz", "Cli Biz")
    org_id = (await db.scalar(select(Organization).where(Organization.slug == "cli-biz"))).id
    ent = await compute_entitlements(db, org_id)
    assert "pos" in ent.modules and "kds" not in ent.modules

    # A second owner for the same business does not touch what the console set since.
    override = await db.get(OrganizationModule, (org_id, "kds"))
    override.enabled = True
    await db.commit()
    await create_owner("cli_owner2", "Cli Owner Two", "CLI1", "Main", "cli-owner-password", "5678", "cli-biz", "Cli Biz")
    db.expire_all()
    assert "kds" in (await compute_entitlements(db, org_id)).modules


async def test_paying_closes_the_orders_printed_paper_kots(client, db, redis_client, branch, waiter, cashier, table):
    """Nobody marks a paper KOT served, so paying the bill closes the printed ones; an unprinted one stays in the print queue."""
    await set_modules(db, redis_client, branch.organization_id, SLIP)
    item = await factories.make_menu_item(db, name="Dal", price="200.00")
    order = await create_order(client, waiter, table, items=[line(item)])
    printed = (await fire(client, waiter, order["id"]))[0]
    r = await client.post(f"{API}/kots/{printed['id']}/slip/printed", headers=idem(cashier))
    assert r.status_code == 200, r.text

    b = await bill(client, cashier, order["id"])
    assert (await pay(client, cashier, b["id"], b["grand_total"])).status_code == 200
    kot = await db.get(KOT, uuid.UUID(printed["id"]), populate_existing=True)
    assert kot.status == "served"
    open_kots = (await client.get(f"{API}/kots", headers=auth_headers(cashier))).json()
    assert printed["id"] not in {k["id"] for k in open_kots}


async def test_kitchen_tickets_name_the_waiter_who_took_the_order(client, db, branch, waiter, kitchen, table):
    """The waiter's phone uses this to announce only their own food as ready."""
    item = await factories.make_menu_item(db, name="Dal", price="200.00")
    order = await create_order(client, waiter, table, items=[line(item)])
    await fire(client, waiter, order["id"])
    board = (await client.get(f"{API}/kots", headers=auth_headers(kitchen))).json()
    assert [t["waiter_id"] for t in board if t["order_id"] == order["id"]] == [str(waiter.id)]
