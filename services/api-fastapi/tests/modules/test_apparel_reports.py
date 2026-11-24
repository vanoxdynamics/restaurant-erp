"""Dress shops, stage A6: barcode labels, the day report, sales by product and size, a customer's purchases."""

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.modules.retailkit import barcode as bc
from tests.factories import auth_headers, idem
from tests.flows import API
from tests.modules.apparel_helpers import A, make_shop, set_category
from tests.modules.test_apparel_returns import give_back, paid_sale
from tests.modules.test_apparel_sales import sell, style

D = Decimal


@pytest.fixture
async def shop(db, redis_client):
    return await make_shop(db, redis_client)


@pytest.fixture
async def other(db, redis_client):
    return await make_shop(db, redis_client, slug="globex", code="GX1")


async def day(client, user, **params):
    r = await client.get(f"{A}/reports/day", params=params, headers=auth_headers(user))
    assert r.status_code == 200, r.text
    return r.json()


# --- the day report ----------------------------------------------------------------------------------------------------------


async def test_the_day_report_matches_the_days_sales_returns_and_payments_at_mixed_rates(client, shop):
    shirt = await style(client, shop, sizes=["M", "L"], price="999.00", mrp=None, name="Shirt")
    kurta = await style(client, shop, sizes=["M"], price="2999.00", mrp=None, name="Kurta", category_name="Kurtas")
    s, m_l = shirt["variants"][0]["id"], shirt["variants"][1]["id"]
    k = kurta["variants"][0]["id"]

    one = await paid_sale(client, shop, [(s, 2), (k, 1)])  # cash
    two_sale = (await sell(client, shop["cashier"], [(m_l, 3)], bill_discount="100.00", discount_reason="Festival")).json()
    split = {"payments": [{"payment_mode": "cash", "amount": "1000.00"},
                          {"payment_mode": "upi", "amount": str(D(two_sale["grand_total"]) - D("1000.00")), "reference_id": "U1"}]}
    r = await client.post(f"{A}/sales/{two_sale['id']}/payments/split", json=split, headers=idem(shop["cashier"]))
    assert r.status_code == 200, r.text
    two = r.json()
    await sell(client, shop["cashier"], [(s, 1)])  # unpaid: not in the report
    await give_back(client, shop["manager"], one, [(0, 1)])  # a shirt back, cash: 999.00
    await give_back(client, shop["manager"], one, [(1, 1)], refund_mode="store_credit")  # the kurta, as credit: 2,999.00

    report = await day(client, shop["manager"])
    sales = report["sales"]
    paid = [one, two]
    assert sales["count"] == 2 and sales["units"] == 2 + 1 + 3
    for key, field in (("subtotal", "subtotal"), ("discount_amount", "discount_amount"), ("taxable_value", "taxable_value"),
                       ("tax_cgst", "tax_cgst"), ("tax_sgst", "tax_sgst"), ("round_off", "round_off"), ("grand_total", "grand_total")):
        assert D(sales[key]) == sum(D(x[field]) for x in paid), key

    by_rate = {t["rate"]: t for t in report["tax_by_rate"]}
    assert set(by_rate) == {"5.00", "18.00"}
    lines = [line for x in paid for line in x["lines"]]
    for rate, entry in by_rate.items():
        mine = [line for line in lines if line["tax_rate"] == rate]
        assert D(entry["taxable_value"]) == sum(D(line["taxable_value"]) for line in mine)
        assert D(entry["tax"]) == sum(D(line["cgst"]) + D(line["sgst"]) + D(line["igst"]) for line in mine)
    assert sum(D(t["taxable_value"]) for t in report["tax_by_rate"]) == D(sales["taxable_value"])

    modes = {p["mode"]: p for p in report["payments"]}
    cash_taken = D(one["grand_total"]) + D("1000.00")
    assert D(modes["cash"]["amount"]) == cash_taken and modes["cash"]["count"] == 2
    assert D(modes["upi"]["amount"]) == D(two["grand_total"]) - D("1000.00") and modes["upi"]["count"] == 1
    assert sum(D(p["amount"]) for p in report["payments"]) == D(one["grand_total"]) + D(two["grand_total"])

    returns = report["returns"]
    assert returns["count"] == 2 and D(returns["cash_total"]) == D("999.00") and D(returns["store_credit_total"]) == D("2999.00")
    assert D(returns["total"]) == D("3998.00")
    assert D(report["net_cash"]) == cash_taken - D("999.00")

    top = {p["name"]: p for p in report["top_products"]}
    assert top["Shirt"]["units"] == 5 and top["Kurta"]["units"] == 1
    sizes = {z["size"]: z["units"] for z in report["units_by_size"]}
    assert sizes == {"M": 3, "L": 3}  # 2 M shirts + 1 M kurta, 3 L shirts


async def test_the_report_is_for_one_branch_and_date_and_only_for_managers(client, db, shop, other):
    v = (await style(client, shop, sizes=["M"], price="999.00"))["variants"][0]["id"]
    sale = await paid_sale(client, shop, [(v, 1)])
    today = sale["business_date"]
    tomorrow = str(date.fromisoformat(today) + timedelta(days=1))

    assert (await day(client, shop["owner"], date=today))["sales"]["count"] == 1
    empty = await day(client, shop["owner"], date=tomorrow)
    assert empty["sales"]["count"] == 0 and empty["payments"] == [] and D(empty["net_cash"]) == 0

    r = await client.get(f"{A}/reports/day", headers=auth_headers(shop["cashier"]))
    assert r.status_code == 403
    r = await client.get(f"{A}/reports/day", params={"branch_id": str(shop["branch"].id)}, headers=auth_headers(other["owner"]))
    assert r.status_code in (403, 404)
    assert (await day(client, other["owner"]))["sales"]["count"] == 0


# --- sales by product and size -----------------------------------------------------------------------------------------------


async def test_sales_by_product_and_size_over_a_range(client, shop):
    shirt = await style(client, shop, sizes=["M", "L"], price="999.00", mrp=None, name="Shirt")
    tee = await style(client, shop, sizes=["M"], price="499.00", mrp=None, name="Tee", category_name="Tees")
    m, large = shirt["variants"][0]["id"], shirt["variants"][1]["id"]
    t = tee["variants"][0]["id"]
    sale = await paid_sale(client, shop, [(m, 2), (large, 1), (t, 3)])
    await sell(client, shop["cashier"], [(m, 1)])  # unpaid: left out
    today = sale["business_date"]
    r = await client.get(f"{A}/reports/sales-by-product", params={"from": today, "to": today}, headers=auth_headers(shop["manager"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["units"] == 6 and D(body["value"]) == D(sale["grand_total"])
    rows = {p["name"]: p for p in body["products"]}
    assert rows["Shirt"]["units"] == 3 and D(rows["Shirt"]["value"]) == D("2997.00")
    assert {z["size"]: z["units"] for z in rows["Shirt"]["sizes"]} == {"M": 2, "L": 1}
    assert [p["name"] for p in body["products"]] == ["Shirt", "Tee"]  # dearest first
    later = str(date.fromisoformat(today) + timedelta(days=2))
    r = await client.get(f"{A}/reports/sales-by-product", params={"from": later, "to": later}, headers=auth_headers(shop["manager"]))
    assert r.json()["products"] == []
    r = await client.get(f"{A}/reports/sales-by-product", params={"from": later, "to": today}, headers=auth_headers(shop["manager"]))
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_range"
    r = await client.get(f"{A}/reports/sales-by-product", params={"from": today, "to": today}, headers=auth_headers(shop["cashier"]))
    assert r.status_code == 403


# --- barcode labels ----------------------------------------------------------------------------------------------------------


async def labels(client, user, items, **extra):
    return await client.post(f"{A}/labels", json={"items": items, **extra}, headers=auth_headers(user))


async def test_a_label_sheet_has_every_label_with_a_barcode_that_scans(client, shop):
    made = await style(client, shop, sizes=["S", "M"], price="999.00", mrp="1299.00", name="Oxford <Shirt>")
    s, m = made["variants"]
    # A manufacturer's code that is not an EAN-13 prints as Code 128.
    r = await client.patch(f"{A}/variants/{m['id']}", json={"barcode": "ARROW-M-01"}, headers=auth_headers(shop["owner"]))
    assert r.status_code == 200, r.text
    r = await labels(client, shop["manager"], [{"variant_id": s["id"], "copies": 3}, {"variant_id": m["id"], "copies": 2}],
                     columns=4, label_height_mm=25)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/html") and "default-src 'none'" in r.headers["content-security-policy"]
    page = r.text
    assert page.count('class="label"') == 5
    assert "repeat(4, 1fr)" in page and "height: 25mm" in page
    assert "Oxford &lt;Shirt&gt;" in page and "<Shirt>" not in page  # the name is escaped
    assert "1,299.00" in page and "999.00" in page

    assert bc.is_ean13(s["barcode"])  # in-store codes carry a valid check digit
    assert page.count(f'aria-label="{s["barcode"]}"') == 3 and page.count('aria-label="ARROW-M-01"') == 2
    # The bars drawn are exactly the symbol of the code: scanning them gives the number printed under them.
    bars = bc.ean13_svg(s["barcode"])
    assert bars in page
    assert bc.code128_svg("ARROW-M-01") in page


async def test_labels_refuse_what_cannot_be_printed(client, shop, other):
    v = (await style(client, shop, sizes=["M"]))["variants"][0]
    assert (await labels(client, shop["manager"], [{"variant_id": str(uuid.uuid4())}])).status_code == 404
    assert (await labels(client, other["owner"], [{"variant_id": v["id"]}])).status_code == 404  # another business's variant
    r = await labels(client, shop["manager"], [{"variant_id": v["id"], "copies": 200}] * 6)
    assert r.status_code == 422 and r.json()["error"]["code"] == "too_many_labels"
    assert (await labels(client, shop["manager"], [])).status_code == 422
    assert (await labels(client, shop["manager"], [{"variant_id": v["id"], "copies": 0}])).status_code == 422
    assert (await labels(client, shop["manager"], [{"variant_id": v["id"]}], columns=9)).status_code == 422
    assert (await labels(client, shop["cashier"], [{"variant_id": v["id"]}])).status_code == 403


# --- a customer's purchases ----------------------------------------------------------------------------------------------------


async def test_a_customers_purchases(client, shop, other):
    v = (await style(client, shop, sizes=["M"], price="999.00"))["variants"][0]["id"]
    made = await client.post(f"{API}/customers", json={"name": "Asha", "phone": "9876543210"}, headers=auth_headers(shop["cashier"]))
    cid = made.json()["id"]
    first = await paid_sale(client, shop, [(v, 1)], customer_id=cid)
    second = await paid_sale(client, shop, [(v, 2)], customer_id=cid)
    await paid_sale(client, shop, [(v, 1)])  # someone else
    r = await client.get(f"{A}/customers/{cid}/sales", headers=auth_headers(shop["cashier"]))
    assert r.status_code == 200 and r.json()["total"] == 2
    assert [s["id"] for s in r.json()["items"]] == [second["id"], first["id"]]  # newest first
    assert len(r.json()["items"][0]["lines"]) == 1
    assert (await client.get(f"{A}/customers/{cid}/sales", headers=auth_headers(other["owner"]))).status_code == 404
    assert (await client.get(f"{A}/customers/{uuid.uuid4()}/sales", headers=auth_headers(shop["owner"]))).status_code == 404


async def test_the_new_routes_are_for_dress_shops_only(client, db, redis_client, shop):
    await set_category(db, redis_client, shop["org"].id, "hardware")
    routes = (("GET", f"{A}/reports/day"), ("GET", f"{A}/reports/sales-by-product"), ("POST", f"{A}/labels"),
              ("GET", f"{A}/customers/{uuid.uuid4()}/sales"))
    for method, url in routes:
        r = await client.request(method, url, json={} if method == "POST" else None, headers=auth_headers(shop["owner"]))
        assert r.status_code == 404 and r.json()["error"]["code"] == "wrong_business_category", url


# --- one shift's sales ---------------------------------------------------------------------------------------------------------


async def test_a_shift_report_counts_only_what_was_paid_in_that_shift(client, db, shop):
    from tests.modules.test_apparel_sales import pay

    cashier = shop["cashier"]
    r = await client.get(f"{A}/reports/shift", headers=auth_headers(cashier))
    assert r.status_code == 404 and r.json()["error"]["code"] == "no_open_shift"

    opened = await client.post(f"{API}/staff/shifts/open", json={"opening_cash": "500.00"}, headers=auth_headers(cashier))
    assert opened.status_code == 201, opened.text
    v = (await style(client, shop, sizes=["M"], price="999.00"))["variants"][0]["id"]
    one = (await sell(client, cashier, [(v, 1)])).json()
    two = (await sell(client, cashier, [(v, 1)])).json()
    await pay(client, cashier, one["id"], "999.00")
    await client.post(f"{A}/sales/{two['id']}/payments/split", headers=idem(cashier), json={"payments": [
        {"payment_mode": "cash", "amount": "300.00"}, {"payment_mode": "upi", "amount": "699.00", "reference_id": "U1"}]})

    report = (await client.get(f"{A}/reports/shift", headers=auth_headers(cashier))).json()
    assert report["status"] == "open" and report["sales"]["count"] == 2 and report["sales"]["units"] == 2
    assert D(report["sales"]["grand_total"]) == D("1998.00")
    modes = {p["mode"]: p for p in report["payments"]}
    assert D(modes["cash"]["amount"]) == D("1299.00") and D(modes["upi"]["amount"]) == D("699.00")
    assert D(report["net_cash"]) == D("1299.00")

    # Another person's shift is theirs: a manager may look at it by id, another cashier may not.
    shift_id = report["shift_id"]
    as_manager = await client.get(f"{A}/reports/shift", params={"shift_id": shift_id}, headers=auth_headers(shop["manager"]))
    assert as_manager.status_code == 200 and as_manager.json()["sales"]["count"] == 2
    manager_own = await client.get(f"{A}/reports/shift", headers=auth_headers(shop["manager"]))
    assert manager_own.status_code == 404  # the manager has no shift of their own open
