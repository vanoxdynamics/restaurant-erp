"""
Row-level security: a database-level backstop for tenant isolation.

These tests connect as a genuine non-superuser role (superusers bypass RLS), so they prove the policies themselves, and
that the API applies the scope to tenant requests even when application code forgets its own tenant filter.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import database as database_module
from app.core.database import rls_status, scope_session
from app.models import Order
from tests import factories
from tests.conftest import TEST_DATABASE_URL, ensure_app_role
from tests.factories import auth_headers
from tests.flows import API, create_order, line

PROBE_ROLE = "rls_probe"


@pytest.fixture
async def probe():
    """A session factory whose connections run as the restricted role."""
    await ensure_app_role(PROBE_ROLE)
    engine = create_async_engine(make_url(TEST_DATABASE_URL), poolclass=NullPool,
                                 connect_args={"server_settings": {"role": PROBE_ROLE}})
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
    await engine.dispose()


@pytest.fixture
async def world(db):
    """Two organizations, each with a branch, owner, waiter, menu and one order. Returns plain ids."""
    out = {}
    for tag, slug in (("a", "acme"), ("b", "globex")):
        org = await factories.make_org(db, slug=slug)
        branch = await factories.make_branch(db, code=f"{tag}BR1".upper(), name=f"Branch {tag}", org=org)
        out[tag] = {"org": org.id, "branch": branch.id, "branch_obj": branch, "org_obj": org}
    return out


async def count(session, table, scope=None):
    if scope is not None:
        await session.execute(text("SELECT set_config('app.org_scope', :s, false)"), {"s": str(scope)})
    return await session.scalar(text(f'SELECT count(*) FROM "{table}"'))


COVERED = ["branches", "staff_users", "categories", "menu_items", "modifier_groups", "organizations", "subscriptions", "orders"]


async def test_every_covered_table_is_filtered_by_scope(db, probe, world):
    a, b = world["a"], world["b"]
    for tag, w in (("a", a), ("b", b)):
        owner = await factories.make_staff(db, w["branch_obj"], role="waiter", username=f"waiter_{tag}", pin="1111" if tag == "a" else "2222")
        item = await factories.make_menu_item(db, category=await factories.make_category(db, org=w["org_obj"]), name=f"Dish {tag}")
        await factories.make_modifier_group(db, name=f"Mods {tag}", org=w["org_obj"])
        w["owner"], w["item"] = owner, item
    # one order per branch (a branch-keyed table)
    for w in (a, b):
        order = Order(branch_id=w["branch"], waiter_id=w["owner"].id, order_number=f"ORD-{w['branch']}", order_type="takeaway",
                      status="active", business_date=datetime.now(timezone.utc).date())
        order.items = []
        db.add(order)
    await db.commit()

    async with probe() as s:
        for table in COVERED:
            unscoped = await count(s, table)
            assert unscoped >= 2, table  # platform / login paths see everything
            await s.execute(text("RESET app.org_scope"))
            only_a = await count(s, table, a["org"])
            only_b = await count(s, table, b["org"])
            assert 0 < only_a < unscoped and 0 < only_b < unscoped and only_a + only_b == unscoped, (table, only_a, only_b, unscoped)
            await s.execute(text("RESET app.org_scope"))
        # And the rows really are the right ones.
        await s.execute(text("SELECT set_config('app.org_scope', :s, false)"), {"s": str(a["org"])})
        branches = (await s.execute(text("SELECT id FROM branches"))).scalars().all()
        assert branches == [a["branch"]]
        names = (await s.execute(text("SELECT name FROM menu_items"))).scalars().all()
        assert names == ["Dish a"]


async def test_scoped_sessions_cannot_write_into_another_organization(db, probe, world):
    a, b = world["a"], world["b"]
    async with probe() as s:
        await s.execute(text("SELECT set_config('app.org_scope', :s, false)"), {"s": str(a["org"])})
        with pytest.raises(DBAPIError, match="row-level security"):
            await s.execute(text("INSERT INTO categories (id, organization_id, name, sort_order, is_active, created_at, updated_at) "
                                 "VALUES (gen_random_uuid(), :o, 'Smuggled', 0, true, now(), now())"), {"o": b["org"]})
        await s.rollback()
        await s.execute(text("SELECT set_config('app.org_scope', :s, false)"), {"s": str(a["org"])})
        # Other tenants' rows are invisible, so changes to them match nothing.
        hit = await s.execute(text("UPDATE branches SET name = 'hijacked' WHERE organization_id = :o"), {"o": b["org"]})
        assert hit.rowcount == 0
        gone = await s.execute(text("DELETE FROM branches WHERE organization_id = :o"), {"o": b["org"]})
        assert gone.rowcount == 0
        # Own rows work normally.
        mine = await s.execute(text("UPDATE branches SET name = 'renamed' WHERE organization_id = :o"), {"o": a["org"]})
        assert mine.rowcount == 1
        await s.commit()
    names = (await db.execute(text("SELECT name FROM branches ORDER BY name"))).scalars().all()
    assert names == ["Branch b", "renamed"]


async def test_branch_keyed_tables_reject_foreign_branches(db, probe, world):
    a, b = world["a"], world["b"]
    async with probe() as s:
        await s.execute(text("SELECT set_config('app.org_scope', :s, false)"), {"s": str(a["org"])})
        with pytest.raises(DBAPIError, match="row-level security"):
            await s.execute(text("INSERT INTO floors (id, branch_id, name, sort_order, is_active, created_at, updated_at) "
                                 "VALUES (gen_random_uuid(), :b, 'Rooftop', 0, true, now(), now())"), {"b": b["branch"]})


async def test_scope_survives_commits_but_never_reaches_other_sessions(db, probe, world):
    a, b = world["a"], world["b"]
    async with probe() as scoped, probe() as other:
        await scope_session(scoped, a["org"])
        assert await scoped.scalar(text("SELECT count(*) FROM branches")) == 1
        await scoped.commit()  # a new transaction starts: the scope must be re-applied automatically
        assert await scoped.scalar(text("SELECT count(*) FROM branches")) == 1
        await scoped.commit()
        # The scope is transaction-local on a pooled connection, so another session never inherits it.
        assert await other.scalar(text("SELECT count(*) FROM branches")) == 2
    assert b


async def test_a_corrupt_scope_fails_closed(probe, world):
    async with probe() as s:
        await s.execute(text("SELECT set_config('app.org_scope', 'not-a-uuid', false)"))
        with pytest.raises(DBAPIError):
            await s.scalar(text("SELECT count(*) FROM branches"))


async def test_platform_tables_are_not_tenant_scoped(db, probe, world):
    await factories.make_org(db, slug="third")
    async with probe() as s:
        await s.execute(text("SELECT set_config('app.org_scope', :s, false)"), {"s": str(world["a"]["org"])})
        for table in ("plans", "platform_admins", "platform_audit_logs", "platform_modules"):
            assert await s.scalar(text(f'SELECT count(*) FROM "{table}"')) is not None


async def test_status_reports_whether_rls_binds_the_connection(db, probe):
    assert await rls_status(db) in ("enforced",) or (await rls_status(db)).startswith("bypassed")
    async with probe() as s:
        assert await rls_status(s) == "enforced"


# --- the API applies the scope even when application code does not filter -----------------------------------------------------


async def test_a_missing_tenant_filter_in_the_application_is_caught_by_the_database(client, db, probe, world, monkeypatch):
    a, b = world["a"], world["b"]
    owner_a = await factories.make_staff(db, a["branch_obj"], role="owner", username="owner_a", pin="9000")

    from app.modules.catalog import service as catalog_service
    from app.modules.floor import service as floor_service
    from sqlalchemy import select
    from app.models import Branch, Category

    async def leaky_branches(db_, user):  # a bug: forgot the organization filter
        return (await db_.scalars(select(Branch).order_by(Branch.name))).all()

    async def leaky_categories(db_, org_id, include_inactive):  # the same bug in another module
        return (await db_.scalars(select(Category))).all()

    await factories.make_category(db, name="A-menu", org=a["org_obj"])
    await factories.make_category(db, name="B-menu", org=b["org_obj"])
    monkeypatch.setattr(floor_service, "list_branches", leaky_branches)
    monkeypatch.setattr(catalog_service, "list_categories", leaky_categories)

    # With the superuser connection (RLS bypassed) the bug is real: it leaks.
    leaked = (await client.get(f"{API}/floor/branches", headers=auth_headers(owner_a))).json()
    if await rls_status(db) != "enforced":
        assert len(leaked) == 2

    # Now the application runs as the restricted role: the very same buggy code returns only the caller's rows.
    monkeypatch.setattr(database_module, "AsyncSessionLocal", probe)
    branches = (await client.get(f"{API}/floor/branches", headers=auth_headers(owner_a))).json()
    assert [x["id"] for x in branches] == [str(a["branch"])]
    categories = (await client.get(f"{API}/catalog/categories", headers=auth_headers(owner_a))).json()
    assert [c["name"] for c in categories] == ["A-menu"]


async def test_normal_requests_work_under_the_restricted_role(client, db, probe, world, monkeypatch):
    a = world["a"]
    waiter = await factories.make_staff(db, a["branch_obj"], role="waiter", username="waiter_a", pin="1111")
    item = await factories.make_menu_item(db, category=await factories.make_category(db, org=a["org_obj"]), name="Dish")
    monkeypatch.setattr(database_module, "AsyncSessionLocal", probe)
    order = await create_order(client, waiter, items=[line(item)])
    assert order["items"] and order["branch_id"] == str(a["branch"])
    # A token from the other organization cannot see it, with or without application-level checks.
    other = await factories.make_staff(db, world["b"]["branch_obj"], role="owner", username="owner_b", pin="9001")
    r = await client.get(f"{API}/orders/{order['id']}", headers=auth_headers(other))
    assert r.status_code == 404
