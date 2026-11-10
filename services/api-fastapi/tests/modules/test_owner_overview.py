"""The owner's business overview: period totals against the period before, and the breakdowns behind them."""

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import update

from app.core.clock import business_date
from app.models import Bill
from tests import factories
from tests.factories import auth_headers
from tests.flows import API, bill, create_order, fire, line, pay

URL = f"{API}/analytics/overview"


async def _sale(client, waiter, cashier, item, quantity=1, table=None, mode="cash"):
    order = await create_order(client, waiter, table, items=[line(item, quantity)])
    await fire(client, waiter, order["id"])
    b = await bill(client, cashier, order["id"])
    r = await pay(client, cashier, b["id"], b["grand_total"], mode=mode, reference="REF1" if mode != "cash" else None)
    assert r.status_code == 200, r.text
    return b


async def test_overview_compares_with_the_period_before(client, db, branch, owner, waiter, cashier, table):
    mains = await factories.make_category(db, name="Mains")
    drinks = await factories.make_category(db, name="Drinks")
    dal = await factories.make_menu_item(db, category=mains, name="Dal", price="200.00", tax_rate="0.00")
    lassi = await factories.make_menu_item(db, category=drinks, name="Lassi", price="50.00", tax_rate="0.00")

    await _sale(client, waiter, cashier, dal, 2, table=table)  # 400, dine-in
    await _sale(client, waiter, cashier, lassi, 1, mode="upi")  # 50, takeaway
    old = await _sale(client, waiter, cashier, dal, 1)  # 200, moved to 8 days ago: the previous week
    today = business_date(branch.timezone)
    await db.execute(update(Bill).where(Bill.id == old["id"]).values(business_date=today - timedelta(days=8)))
    await db.commit()

    r = await client.get(URL, params={"period": "7d"}, headers=auth_headers(owner))
    assert r.status_code == 200, r.text
    o = r.json()
    assert (o["start"], o["end"]) == ((today - timedelta(days=6)).isoformat(), today.isoformat())
    assert Decimal(o["totals"]["net_sales"]) == Decimal("450.00") and o["totals"]["bills"] == 2
    assert Decimal(o["totals"]["average_bill"]) == Decimal("225.00")
    assert Decimal(o["previous"]["net_sales"]) == Decimal("200.00")
    assert o["changes"]["net_sales"] == 125.0  # 450 against 200
    assert len(o["trend"]) == 7 and Decimal(o["trend"][-1]["sales"]) == Decimal("450.00")
    assert len(o["by_weekday"]) == 7
    assert [(c["name"], Decimal(c["amount"])) for c in o["categories"]] == [("Mains", Decimal("400.00")), ("Drinks", Decimal("50.00"))]
    assert o["top_items"][0]["name"] == "Dal" and o["top_items"][0]["quantity"] == 2
    assert {t["name"]: Decimal(t["amount"]) for t in o["order_types"]} == {"Dine-in": Decimal("400.00"), "Takeaway": Decimal("50.00")}
    assert o["staff"][0]["name"] == waiter.full_name and o["staff"][0]["count"] == 2
    assert Decimal(o["payments"]["cash"]) == Decimal("400.00") and Decimal(o["payments"]["upi"]) == Decimal("50.00")

    today_view = (await client.get(URL, params={"period": "today"}, headers=auth_headers(owner))).json()
    assert today_view["by_weekday"] == [] and all(":" in p["label"] for p in today_view["trend"])
    assert today_view["changes"]["net_sales"] is None  # nothing yesterday to compare with


async def test_overview_access(client, db, branch, other_branch, owner, manager, cashier):
    assert (await client.get(URL, headers=auth_headers(cashier))).status_code == 403
    assert (await client.get(URL, headers=auth_headers(manager))).status_code == 200
    assert (await client.get(URL, params={"all_branches": True}, headers=auth_headers(manager))).status_code == 403
    every = await client.get(URL, params={"all_branches": True}, headers=auth_headers(owner))
    assert every.status_code == 200 and every.json()["branches"] == 2
    assert (await client.get(URL, params={"period": "1y"}, headers=auth_headers(owner))).status_code == 422
