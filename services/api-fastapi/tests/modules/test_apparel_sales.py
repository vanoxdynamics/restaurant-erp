"""Dress shops, stage A3: sales (tax invoice in one request), tenders, void, receipt and the cash day."""

import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app.models.staff import Shift
from app.models.apparel import ApparelSale, ApparelSaleLine, ApparelStockMovement
from tests.factories import auth_headers, idem
from tests.flows import API
from tests.modules.apparel_helpers import A, category, make_shop, product, set_category
from tests.modules.test_rls import count, probe  # noqa: F401  (fixture)

D = Decimal


@pytest.fixture
async def shop(db, redis_client):
    return await make_shop(db, redis_client)


@pytest.fixture
async def other(db, redis_client):
    return await make_shop(db, redis_client, slug="globex", code="GX1")


async def style(client, shop, stock=20, **kw):
    """A style whose variants all have `stock` pieces on the shelf."""
    cat = await category(client, shop["owner"], kw.pop("category_name", "Shirts"))
    made = await product(client, shop["owner"], cat["id"], **kw)
    r = await client.post(f"{A}/purchases", json={"lines": [{"variant_id": v["id"], "quantity": stock} for v in made["variants"]]},
                          headers=idem(shop["owner"]))
    assert r.status_code == 201, r.text
    return made


async def sell(client, user, lines, **extra):
    body = {"lines": [item if isinstance(item, dict) else {"variant_id": item[0], "quantity": item[1]} for item in lines], **extra}
    return await client.post(f"{A}/sales", json=body, headers=idem(user))


async def on_hand(client, user, variant_id):
    rows = (await client.get(f"{A}/stock", headers=auth_headers(user))).json()
    return next(r for r in rows if r["variant_id"] == variant_id)["quantity"]


async def pay(client, user, sale_id, amount, mode="cash", reference=None, key=None):
    body = {"payment_mode": mode, "amount": str(amount), **({"reference_id": reference} if reference else {})}
    return await client.post(f"{A}/sales/{sale_id}/payments", json=body, headers=idem(user, key))


# --- making the invoice ------------------------------------------------------------------------------------------------------


async def test_a_sale_is_a_numbered_invoice_and_takes_pieces_out_of_stock(client, db, shop):
    made = await style(client, shop, sizes=["M"], price="999.00", mrp="1299.00")
    v = made["variants"][0]
    r = await sell(client, shop["cashier"], [(v["id"], 1)])
    assert r.status_code == 201, r.text
    sale = r.json()
    assert sale["sale_number"].startswith("KF1/") and sale["status"] == "unpaid"
    assert (sale["subtotal"], sale["taxable_value"], sale["tax_cgst"], sale["tax_sgst"], sale["grand_total"]) == (
        "999.00", "951.43", "23.79", "23.78", "999.00")
    line = sale["lines"][0]
    assert line["tax_rate"] == "5.00" and line["size_snapshot"] == "M" and line["mrp_snapshot"] == "1299.00"
    assert await on_hand(client, shop["owner"], v["id"]) == 19
    ledger = (await client.get(f"{A}/movements", params={"kind": "sale"}, headers=auth_headers(shop["owner"]))).json()["items"]
    assert [(e["quantity"], e["balance_after"]) for e in ledger] == [(-1, 19)]
    assert (await client.get(f"{A}/sales/{sale['id']}", headers=auth_headers(shop["cashier"]))).json()["id"] == sale["id"]


async def test_each_piece_is_taxed_by_its_own_price(client, shop):
    cheap = await style(client, shop, sizes=["M"], price="999.00", mrp=None, name="Shirt")
    dear = await style(client, shop, sizes=["M"], price="2999.00", mrp=None, name="Kurta", category_name="Kurtas")
    r = await sell(client, shop["cashier"], [(cheap["variants"][0]["id"], 1), (dear["variants"][0]["id"], 1)])
    rates = sorted(line["tax_rate"] for line in r.json()["lines"])
    assert rates == ["18.00", "5.00"]
    by_name = {line["name_snapshot"]: line for line in r.json()["lines"]}
    assert (by_name["Kurta"]["taxable_value"], by_name["Kurta"]["cgst"], by_name["Kurta"]["sgst"]) == ("2541.53", "228.74", "228.73")


async def test_stored_lines_add_up_to_the_sale_for_a_mixed_sale_with_a_bill_discount(client, db, shop):
    shirts = await style(client, shop, sizes=["S", "M", "L"], price="999.00", mrp=None, name="Shirt")
    kurtas = await style(client, shop, sizes=["S", "M", "L", "XL"], price="2999.00", mrp=None, name="Kurta",
                         category_name="Kurtas")
    lines = [(v["id"], i % 3 + 1) for i, v in enumerate(shirts["variants"] + kurtas["variants"])]
    lines += [{"variant_id": kurtas["variants"][0]["id"], "quantity": 1, "line_discount": "100.00"},
              {"variant_id": shirts["variants"][0]["id"], "quantity": 2},
              {"variant_id": shirts["variants"][1]["id"], "quantity": 1}]
    r = await sell(client, shop["cashier"], lines, bill_discount="150.00", discount_reason="Festival offer")
    assert r.status_code == 201, r.text
    sale = r.json()
    assert len(sale["lines"]) == 10
    total = lambda key: sum(D(line[key]) for line in sale["lines"])  # noqa: E731
    assert total("taxable_value") == D(sale["taxable_value"])
    assert total("cgst") == D(sale["tax_cgst"]) and total("sgst") == D(sale["tax_sgst"]) and total("igst") == D(sale["tax_igst"])
    assert total("line_total") == D(sale["grand_total"]) - D(sale["round_off"])
    assert total("discount") == D(sale["discount_amount"]) == D("250.00")
    assert D(sale["grand_total"]) == D(sale["grand_total"]).to_integral_value()  # rounded to the rupee


async def test_discounts_need_a_reason_and_big_ones_a_manager(client, db, shop):
    made = await style(client, shop, sizes=["M"], price="1000.00", mrp=None)
    v = made["variants"][0]["id"]
    r = await sell(client, shop["cashier"], [(v, 1)], bill_discount="50.00")
    assert r.status_code == 422
    r = await sell(client, shop["cashier"], [(v, 1)], bill_discount="50.00", discount_reason="Loyal customer")
    assert r.status_code == 201  # 5%: under the threshold
    r = await sell(client, shop["cashier"], [(v, 1)], bill_discount="300.00", discount_reason="Damaged")
    assert r.status_code == 403 and r.json()["error"]["code"] == "approval_required"
    r = await sell(client, shop["cashier"], [(v, 1)], bill_discount="300.00", discount_reason="Damaged", approver_pin="7001")
    assert r.status_code == 201 and r.json()["discount_amount"] == "300.00"
    r = await sell(client, shop["manager"], [(v, 1)], bill_discount="300.00", discount_reason="Damaged")
    assert r.status_code == 201


async def test_price_changes_need_a_manager_and_never_exceed_the_mrp(client, shop):
    made = await style(client, shop, sizes=["M"], price="999.00", mrp="1299.00")
    v = made["variants"][0]["id"]
    line = {"variant_id": v, "quantity": 1, "unit_price": "899.00"}
    r = await sell(client, shop["cashier"], [line])
    assert r.status_code == 403 and r.json()["error"]["code"] == "approval_required"
    r = await sell(client, shop["cashier"], [line], approver_pin="7001")
    assert r.status_code == 201 and r.json()["lines"][0]["unit_price"] == "899.00"
    r = await sell(client, shop["manager"], [{**line, "unit_price": "1300.00"}])
    assert r.status_code == 422 and r.json()["error"]["code"] == "price_above_mrp"


async def test_inactive_and_unknown_variants_are_refused(client, shop):
    made = await style(client, shop, sizes=["M", "L"])
    m, large = made["variants"]
    await client.patch(f"{A}/variants/{large['id']}", json={"is_active": False}, headers=auth_headers(shop["owner"]))
    r = await sell(client, shop["cashier"], [(large["id"], 1)])
    assert r.status_code == 422 and r.json()["error"]["code"] == "variant_inactive"
    r = await sell(client, shop["cashier"], [(str(uuid.uuid4()), 1)])
    assert r.status_code == 404 and r.json()["error"]["code"] == "variant_not_found"
    assert await on_hand(client, shop["owner"], m["id"]) == 20


async def test_a_repeated_request_and_a_repeated_client_sale_id_make_one_sale(client, db, shop):
    v = (await style(client, shop, sizes=["M"]))["variants"][0]["id"]
    headers = idem(shop["cashier"], "same-key")
    body = {"lines": [{"variant_id": v, "quantity": 2}]}
    a = await client.post(f"{A}/sales", json=body, headers=headers)
    b = await client.post(f"{A}/sales", json=body, headers=headers)
    assert a.status_code == b.status_code == 201 and a.json()["id"] == b.json()["id"]
    first = await sell(client, shop["cashier"], [(v, 1)], client_sale_id="till-1-0001")
    again = await sell(client, shop["cashier"], [(v, 1)], client_sale_id="till-1-0001")
    assert first.json()["id"] == again.json()["id"]
    assert await db.scalar(select(func.count()).select_from(ApparelSale)) == 2
    assert await on_hand(client, shop["owner"], v) == 17  # 2 + 1 pieces, not 5


async def test_concurrent_sales_of_the_same_variant_keep_the_ledger_consistent(client, db, shop):
    v = (await style(client, shop, sizes=["M"], stock=10))["variants"][0]["id"]
    results = await asyncio.gather(*[sell(client, shop["cashier"], [(v, 1)]) for _ in range(5)])
    assert all(r.status_code == 201 for r in results)
    assert len({r.json()["sale_number"] for r in results}) == 5
    assert await on_hand(client, shop["owner"], v) == 5
    moves = (await db.scalars(select(ApparelStockMovement).where(ApparelStockMovement.kind == "sale")
                              .order_by(ApparelStockMovement.created_at))).all()
    assert [m.balance_after for m in moves] == [9, 8, 7, 6, 5]


# --- paying ------------------------------------------------------------------------------------------------------------------


async def test_cash_gives_change_and_other_modes_may_not_overpay(client, shop):
    v = (await style(client, shop, sizes=["M"], price="999.00"))["variants"][0]["id"]
    sale = (await sell(client, shop["cashier"], [(v, 1)])).json()
    r = await pay(client, shop["cashier"], sale["id"], "999.00", mode="upi")
    assert r.status_code == 422  # reference required
    r = await pay(client, shop["cashier"], sale["id"], "1000.00", mode="card", reference="APP1")
    assert r.status_code == 422 and r.json()["error"]["code"] == "overpayment"
    r = await pay(client, shop["cashier"], sale["id"], "500.00")
    assert r.status_code == 200 and r.json()["sale"]["status"] == "partially_paid" and r.json()["sale"]["balance_due"] == "499.00"
    r = await pay(client, shop["cashier"], sale["id"], "600.00")
    body = r.json()
    assert body["sale"]["status"] == "paid" and body["change_due"] == "101.00" and body["payment"]["amount"] == "499.00"
    assert (await pay(client, shop["cashier"], sale["id"], "10.00")).status_code == 409


async def test_split_payment_settles_the_sale(client, shop):
    v = (await style(client, shop, sizes=["M"], price="999.00"))["variants"][0]["id"]
    sale = (await sell(client, shop["cashier"], [(v, 1)])).json()
    body = {"payments": [{"payment_mode": "cash", "amount": "400.00"},
                         {"payment_mode": "card", "amount": "500.00", "reference_id": "A1"}]}
    r = await client.post(f"{A}/sales/{sale['id']}/payments/split", json=body, headers=idem(shop["cashier"]))
    assert r.status_code == 422 and r.json()["error"]["code"] == "split_mismatch"
    body["payments"][1]["amount"] = "599.00"
    r = await client.post(f"{A}/sales/{sale['id']}/payments/split", json=body, headers=idem(shop["cashier"]))
    assert r.status_code == 200 and r.json()["status"] == "paid" and len(r.json()["payments"]) == 2


async def test_terminal_payment_settles_then_can_be_reversed_by_a_manager(client, shop):
    v = (await style(client, shop, sizes=["M"], price="999.00"))["variants"][0]["id"]
    sale = (await sell(client, shop["cashier"], [(v, 1)])).json()
    r = await client.post(f"{A}/sales/{sale['id']}/payments/terminal", json={"payment_mode": "card", "amount": "999.00"},
                          headers=idem(shop["cashier"]))
    assert r.status_code == 201 and r.json()["payment"]["status"] == "pending"
    payment = r.json()["payment"]
    assert (await pay(client, shop["cashier"], sale["id"], "10.00")).status_code == 409  # a terminal is charging it
    done = {"outcome": "approved", "approval_code": "OK123"}
    r = await client.post(f"{A}/payments/{payment['id']}/terminal/complete", json=done, headers=auth_headers(shop["cashier"]))
    assert r.json()["sale"]["status"] == "paid"
    again = await client.post(f"{A}/payments/{payment['id']}/terminal/complete", json=done, headers=auth_headers(shop["cashier"]))
    assert again.status_code == 200 and again.json()["sale"]["paid_amount"] == "999.00"
    r = await client.post(f"{A}/payments/{payment['id']}/reverse", json={"reason": "Voided on device"},
                          headers=auth_headers(shop["cashier"]))
    assert r.status_code == 403  # a cashier needs a manager
    r = await client.post(f"{A}/payments/{payment['id']}/reverse", json={"reason": "Voided on device", "approver_pin": "7001"},
                          headers=auth_headers(shop["cashier"]))
    assert r.status_code == 200 and r.json()["sale"]["status"] == "unpaid" and r.json()["payment"]["status"] == "reversed"


async def test_repeated_payment_request_records_once(client, shop):
    v = (await style(client, shop, sizes=["M"], price="999.00"))["variants"][0]["id"]
    sale = (await sell(client, shop["cashier"], [(v, 1)])).json()
    a = await pay(client, shop["cashier"], sale["id"], "300.00", key="pay-key-1")
    b = await pay(client, shop["cashier"], sale["id"], "300.00", key="pay-key-1")
    assert a.status_code == b.status_code == 200, (a.text, b.text)
    assert a.json()["payment"]["id"] == b.json()["payment"]["id"]
    assert b.json()["sale"]["paid_amount"] == "300.00"


# --- voiding -----------------------------------------------------------------------------------------------------------------


async def test_voiding_restores_stock_and_only_unpaid_sales_can_be_voided(client, db, shop):
    v = (await style(client, shop, sizes=["M"], price="999.00"))["variants"][0]["id"]
    sale = (await sell(client, shop["cashier"], [(v, 3)])).json()
    assert await on_hand(client, shop["owner"], v) == 17
    body = {"reason": "Customer left"}
    # No payment was ever tried: the cashier just confirms, without a reason or a manager.
    r = await client.post(f"{A}/sales/{sale['id']}/void", json={}, headers=auth_headers(shop["cashier"]))
    assert r.status_code == 200 and r.json()["status"] == "voided"
    assert await on_hand(client, shop["owner"], v) == 20
    kinds = [e["kind"] for e in (await client.get(f"{A}/movements", params={"variant_id": v},
                                                  headers=auth_headers(shop["owner"]))).json()["items"]]
    assert kinds[:2] == ["sale_void", "sale"]
    assert (await pay(client, shop["cashier"], sale["id"], "10.00")).status_code == 409  # a voided sale takes no money

    # Once a payment was attempted (here a declined card), a cashier needs a manager's PIN.
    tried = (await sell(client, shop["cashier"], [(v, 1)])).json()
    started = await client.post(f"{A}/sales/{tried['id']}/payments/terminal", json={"payment_mode": "card", "amount": "999.00"},
                                headers=idem(shop["cashier"]))
    await client.post(f"{A}/payments/{started.json()['payment']['id']}/terminal/complete", json={"outcome": "declined"},
                      headers=auth_headers(shop["cashier"]))
    r = await client.post(f"{A}/sales/{tried['id']}/void", json=body, headers=auth_headers(shop["cashier"]))
    assert r.status_code == 403 and r.json()["error"]["code"] == "approval_required"
    r = await client.post(f"{A}/sales/{tried['id']}/void", json={**body, "approver_pin": "7001"}, headers=auth_headers(shop["cashier"]))
    assert r.status_code == 200 and r.json()["status"] == "voided"

    paid = (await sell(client, shop["cashier"], [(v, 1)])).json()
    await pay(client, shop["cashier"], paid["id"], "999.00")
    r = await client.post(f"{A}/sales/{paid['id']}/void", json=body, headers=auth_headers(shop["manager"]))
    assert r.status_code == 409 and r.json()["error"]["code"] == "refund_required"
    part = (await sell(client, shop["cashier"], [(v, 1)])).json()
    await pay(client, shop["cashier"], part["id"], "100.00")
    r = await client.post(f"{A}/sales/{part['id']}/void", json=body, headers=auth_headers(shop["manager"]))
    assert r.status_code == 409


# --- the printed invoice -----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("paper", [58, 80])
async def test_receipt_renders_at_both_paper_widths(client, shop, paper):
    v = (await style(client, shop, sizes=["M"], price="999.00", mrp="1299.00", name="Oxford Shirt"))["variants"][0]["id"]
    sale = (await sell(client, shop["cashier"], [(v, 2)])).json()
    await pay(client, shop["cashier"], sale["id"], "2000.00")
    r = await client.get(f"{A}/sales/{sale['id']}/receipt", params={"paper_mm": paper}, headers=auth_headers(shop["cashier"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sale_number"] == sale["sale_number"] and body["paper_mm"] == paper
    width = {58: 32, 80: 48}[paper]
    assert body["rows"] and all(len(row["text"]) <= width for row in body["rows"])
    assert "Oxford Shirt" in body["text"] and "M / Blue" in body["text"] and "1,998" in body["text"]
    text_r = await client.get(f"{A}/sales/{sale['id']}/receipt", params={"format": "text", "paper_mm": paper},
                              headers=auth_headers(shop["cashier"]))
    assert text_r.headers["content-type"].startswith("text/plain") and sale["sale_number"] in text_r.text
    html_r = await client.get(f"{A}/sales/{sale['id']}/receipt", params={"format": "html", "paper_mm": paper},
                              headers=auth_headers(shop["cashier"]))
    assert html_r.headers["content-type"].startswith("text/html")
    assert (await client.get(f"{A}/sales/{sale['id']}/receipt", params={"paper_mm": 70},
                             headers=auth_headers(shop["cashier"]))).status_code == 422


# --- the cash day ------------------------------------------------------------------------------------------------------------


async def test_closing_the_cash_day_counts_the_dress_shops_cash(client, db, shop):
    cashier = shop["cashier"]
    opened = await client.post(f"{API}/staff/cash-day/open", json={"opening_cash": "500.00"}, headers=auth_headers(cashier))
    assert opened.status_code == 201, opened.text
    v = (await style(client, shop, sizes=["M"], price="999.00"))["variants"][0]["id"]
    one = (await sell(client, cashier, [(v, 1)])).json()
    two = (await sell(client, cashier, [(v, 1)])).json()
    await pay(client, cashier, one["id"], "999.00")
    await client.post(f"{A}/sales/{two['id']}/payments/split", headers=idem(cashier), json={"payments": [
        {"payment_mode": "cash", "amount": "300.00"}, {"payment_mode": "upi", "amount": "699.00", "reference_id": "U1"}]})
    closed = await client.post(f"{API}/staff/cash-day/close", json={"counted_cash": "1799.00"}, headers=auth_headers(cashier))
    assert closed.status_code == 200, closed.text
    shift = await db.scalar(select(Shift))
    await db.refresh(shift)
    assert (shift.expected_cash, shift.cash_variance) == (D("1799.00"), D("0.00"))  # 500 + 999 + 300, UPI is not cash


# --- who may, and whose --------------------------------------------------------------------------------------------------------


async def test_sales_belong_to_their_business_and_category(client, db, redis_client, shop, other):
    v = (await style(client, shop, sizes=["M"]))["variants"][0]["id"]
    sale = (await sell(client, shop["cashier"], [(v, 1)])).json()
    them = other["owner"]
    assert (await sell(client, them, [(v, 1)])).status_code == 404
    assert (await client.get(f"{A}/sales/{sale['id']}", headers=auth_headers(them))).status_code == 404
    assert (await pay(client, them, sale["id"], "10.00")).status_code == 404
    assert (await client.post(f"{A}/sales/{sale['id']}/void", json={"reason": "theirs"}, headers=auth_headers(them))).status_code == 404
    assert (await client.get(f"{A}/sales", headers=auth_headers(them))).json()["total"] == 0
    assert (await client.get(f"{A}/sales", params={"on_date": sale["business_date"], "search": sale["sale_number"][-4:]},
                             headers=auth_headers(shop["manager"]))).json()["total"] == 1

    await set_category(db, redis_client, shop["org"].id, "hardware")
    for method, url in (("POST", f"{A}/sales"), ("GET", f"{A}/sales"), ("GET", f"{A}/sales/{sale['id']}"),
                        ("POST", f"{A}/sales/{sale['id']}/payments"), ("GET", f"{A}/sales/{sale['id']}/receipt")):
        r = await client.request(method, url, json={} if method == "POST" else None, headers=auth_headers(shop["owner"]))
        assert r.status_code == 404 and r.json()["error"]["code"] == "wrong_business_category", url


async def test_rls_covers_the_sales_tables(client, db, probe, shop, other):  # noqa: F811
    for s in (shop, other):
        v = (await style(client, s, sizes=["M"]))["variants"][0]["id"]
        sale = (await sell(client, s["cashier"], [(v, 1)])).json()
        await pay(client, s["cashier"], sale["id"], "10.00")
    async with probe() as session:
        for table in ("apparel_sales", "apparel_sale_lines", "apparel_payments"):
            everything = await count(session, table)
            await session.execute(text("RESET app.org_scope"))
            mine = await count(session, table, shop["org"].id)
            theirs = await count(session, table, other["org"].id)
            assert 0 < mine < everything and 0 < theirs < everything and mine + theirs == everything, table
            await session.execute(text("RESET app.org_scope"))
    assert await db.scalar(select(func.count()).select_from(ApparelSaleLine)) == 2
