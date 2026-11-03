import asyncio
import re

import pytest

from app.core.auth import CurrentUser, resolve_approver
from app.core.database import AsyncSessionLocal
from app.core.enums import Role
from app.core.errors import ApprovalRequired, TooManyAttempts
from app.core.numbering import next_document_number
from app.models import Branch, Order
from tests import factories
from tests.factories import auth_headers, idem
from tests.flows import API, create_order, line


def as_current(user) -> CurrentUser:
    return CurrentUser(id=user.id, branch_id=user.branch_id, role=Role(user.role), username=user.username,
                       full_name=user.full_name, auth_method="pwd", organization_id=user.organization_id)


# --- Auth ------------------------------------------------------------------------


async def test_token_auth_rules(client, db, branch, waiter, manager):
    r = await client.get(f"{API}/orders", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401

    assert (await client.get(f"{API}/orders", headers=auth_headers(waiter))).status_code == 200
    # Waiter on a manager-only route.
    assert (await client.get(f"{API}/staff", headers=auth_headers(waiter))).status_code == 403

    # Bumping token_version revokes outstanding tokens.
    headers = auth_headers(waiter)
    r = await client.post(f"{API}/staff/{waiter.id}/revoke-sessions", headers=auth_headers(manager))
    assert r.status_code == 200, r.text
    assert (await client.get(f"{API}/orders", headers=headers)).status_code == 401


async def test_inactive_user_token_rejected(client, db, branch):
    user = await factories.make_staff(db, branch, is_active=False)
    assert (await client.get(f"{API}/orders", headers=auth_headers(user))).status_code == 401


async def test_manager_routes_require_password_session(client, manager):
    r = await client.get(f"{API}/staff", headers=auth_headers(manager, auth_method="pin"))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "password_login_required"


async def test_cross_branch_reads_are_hidden(client, db, branch, other_branch, waiter):
    other_manager = await factories.make_staff(db, other_branch, role="manager", pin="5555")
    order = await create_order(client, waiter)
    r = await client.get(f"{API}/orders/{order['id']}", headers=auth_headers(other_manager))
    assert r.status_code == 404


async def test_resolve_approver(db, redis_client, branch, waiter, manager, captain):
    assert await resolve_approver(db, as_current(manager), branch.id, None) == manager.id
    assert await resolve_approver(db, as_current(waiter), branch.id, "9001", redis_client) == manager.id
    with pytest.raises(ApprovalRequired):
        await resolve_approver(db, as_current(waiter), branch.id, None)
    with pytest.raises(ApprovalRequired):
        await resolve_approver(db, as_current(waiter), branch.id, "0000", redis_client)
    with pytest.raises(ApprovalRequired):  # captain's PIN is not a manager's
        await resolve_approver(db, as_current(waiter), branch.id, "9003", redis_client)


async def test_approval_pin_is_rate_limited(db, redis_client, branch, waiter, manager):
    for _ in range(5):
        with pytest.raises(ApprovalRequired):
            await resolve_approver(db, as_current(waiter), branch.id, "0000", redis_client)
    with pytest.raises(TooManyAttempts):
        await resolve_approver(db, as_current(waiter), branch.id, "9001", redis_client)


# --- Numbering ----------------------------------------------------------------------


async def test_numbers_are_per_branch(db, branch, other_branch):
    a = await next_document_number(db, branch, "kot")
    b = await next_document_number(db, other_branch, "kot")
    a2 = await next_document_number(db, branch, "kot")
    await db.commit()
    assert re.fullmatch(r"KOT-BLR1-\d{8}-0001", a)
    assert re.fullmatch(r"KOT-MUM1-\d{8}-0001", b)
    assert a2.endswith("-0002")
    bill = await next_document_number(db, branch, "bill")
    assert re.fullmatch(r"BLR1/\d{4}/000001", bill) and len(bill) <= 16


async def test_rollback_releases_number(db, branch):
    branch_id = branch.id
    await next_document_number(db, branch, "order")
    await db.rollback()
    branch = await db.get(Branch, branch_id)
    assert (await next_document_number(db, branch, "order")).endswith("-0001")
    await db.rollback()


async def test_concurrent_numbering_is_gapless_and_unique(branch):
    async def allocate() -> str:
        async with AsyncSessionLocal() as session:
            b = await session.get(Branch, branch.id)
            number = await next_document_number(session, b, "bill")
            await session.commit()
            return number

    numbers = await asyncio.gather(*(allocate() for _ in range(25)))
    seqs = sorted(int(n.rsplit("/", 1)[1]) for n in numbers)
    assert seqs == list(range(1, 26))


# --- Idempotency -------------------------------------------------------------------


async def test_idempotency_key_required(client, waiter):
    r = await client.post(f"{API}/orders", json={"order_type": "takeaway"}, headers=auth_headers(waiter))
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "idempotency_key_required"


async def test_replay_returns_same_response_without_duplicate(client, db, waiter):
    item = await factories.make_menu_item(db)
    body = {"order_type": "takeaway", "items": [line(item)]}
    headers = idem(waiter, "key-replay-0001")
    first = await client.post(f"{API}/orders", json=body, headers=headers)
    second = await client.post(f"{API}/orders", json=body, headers=headers)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert second.headers.get("idempotent-replay") == "true"
    from sqlalchemy import func, select
    assert await db.scalar(select(func.count(Order.id))) == 1


async def test_key_reuse_with_different_body_rejected(client, waiter):
    headers = idem(waiter, "key-reuse-0001")
    assert (await client.post(f"{API}/orders", json={"order_type": "takeaway"}, headers=headers)).status_code == 201
    r = await client.post(f"{API}/orders", json={"order_type": "delivery"}, headers=headers)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "idempotency_key_reused"


async def test_failure_releases_key_for_retry(client, db, waiter, table):
    item = await factories.make_menu_item(db, available=False)
    headers = idem(waiter, "key-retry-00001")
    body = {"order_type": "dine_in", "table_id": str(table.id), "items": [line(item)]}
    assert (await client.post(f"{API}/orders", json=body, headers=headers)).status_code == 422
    # Retrying with the same key isn't locked out (old guard returned 409 for 120s).
    r = await client.post(f"{API}/orders", json=body, headers=headers)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "item_unavailable"


async def test_same_key_from_two_users_does_not_collide(client, waiter, captain):
    body = {"order_type": "takeaway"}
    a = await client.post(f"{API}/orders", json=body, headers=idem(waiter, "shared-key-0001"))
    b = await client.post(f"{API}/orders", json=body, headers=idem(captain, "shared-key-0001"))
    assert a.status_code == b.status_code == 201
    assert a.json()["id"] != b.json()["id"]
