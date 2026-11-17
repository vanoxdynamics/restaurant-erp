"""Dress shops, stage A5: returns, credit notes, cash refunds, store credit and exchanges."""

import asyncio
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app.models.apparel import ApparelReturn, ApparelSale, ApparelStoreCredit
from app.models.staff import Shift
from tests.factories import auth_headers, idem
from tests.flows import API
from tests.modules.apparel_helpers import A, make_shop, set_category
from tests.modules.test_apparel_sales import on_hand, pay, sell, style
from tests.modules.test_rls import count, probe  # noqa: F401  (fixture)

D = Decimal


@pytest.fixture
async def shop(db, redis_client):
    return await make_shop(db, redis_client)


@pytest.fixture
async def other(db, redis_client):
    return await make_shop(db, redis_client, slug="globex", code="GX1")


async def paid_sale(client, shop, lines, mode="cash", **extra):
    sale = (await sell(client, shop["cashier"], lines, **extra)).json()
    r = await pay(client, shop["cashier"], sale["id"], sale["grand_total"], mode=mode, reference="REF1" if mode != "cash" else None)
    assert r.status_code == 200, r.text
    return r.json()["sale"]


async def give_back(client, user, sale, picks, refund_mode="cash", key=None, **extra):
    """`picks` are (line index, quantity[, restock])."""
    lines = [{"sale_line_id": sale["lines"][i]["id"], "quantity": q, "restock": rest[0] if rest else True}
             for i, q, *rest in picks]
    body = {"sale_id": sale["id"], "lines": lines, "reason": "Did not fit", "refund_mode": refund_mode, **extra}
    return await client.post(f"{A}/returns", json=body, headers=idem(user, key))


async def credit_of(client, user, code):
    return await client.get(f"{A}/store-credits/{code}", headers=auth_headers(user))


# --- cash returns ------------------------------------------------------------------------------------------------------------


async def test_partial_cash_return_values_the_pieces_from_the_sale_and_restocks(client, db, shop):
    v = (await style(client, shop, sizes=["M"], price="999.00"))["variants"][0]["id"]
    sale = await paid_sale(client, shop, [(v, 2)])
    assert await on_hand(client, shop["owner"], v) == 18
    r = await give_back(client, shop["cashier"], sale, [(0, 1)])
    assert r.status_code == 201, r.text
    ret = r.json()
    assert ret["return_number"].startswith("CN/KF1/") and ret["refund_mode"] == "cash" and ret["store_credit_code"] is None
    assert (ret["total_amount"], ret["taxable_value"], ret["tax_cgst"], ret["tax_sgst"]) == ("999.00", "951.43", "23.79", "23.79")  # half of the two-piece line
    assert await on_hand(client, shop["owner"], v) == 19
    line = (await client.get(f"{A}/sales/{sale['id']}", headers=auth_headers(shop["cashier"]))).json()["lines"][0]
    assert line["returned_quantity"] == 1
    kinds = [e["kind"] for e in (await client.get(f"{A}/movements", params={"variant_id": v}, headers=auth_headers(shop["owner"]))).json()["items"]]
    assert kinds[0] == "return"


async def test_damaged_goods_are_refunded_but_do_not_go_back_on_the_shelf(client, shop):
    v = (await style(client, shop, sizes=["M"], price="999.00"))["variants"][0]["id"]
    sale = await paid_sale(client, shop, [(v, 1)])
    r = await give_back(client, shop["cashier"], sale, [(0, 1, False)])
    assert r.status_code == 201 and r.json()["total_amount"] == "999.00"
    assert await on_hand(client, shop["owner"], v) == 19


async def test_returns_of_a_whole_line_add_up_to_the_line_exactly(client, shop):
    # 3 pieces at 333.33 with a bill discount: no piece-by-piece rounding can add up unless the last return takes what is left.
    v = (await style(client, shop, sizes=["M"], price="333.33", mrp=None))["variants"][0]["id"]
    sale = await paid_sale(client, shop, [(v, 3)], bill_discount="10.01", discount_reason="Round up")
    line = sale["lines"][0]
    returns = [(await give_back(client, shop["manager"], sale, [(0, 1)])).json() for _ in range(3)]
    for key, field in (("total_amount", "line_total"), ("taxable_value", "taxable_value"), ("tax_cgst", "cgst"), ("tax_sgst", "sgst")):
        assert sum(D(r[key]) for r in returns) == D(line[field]), key
    fourth = await give_back(client, shop["manager"], sale, [(0, 1)])
    assert fourth.status_code == 409 and fourth.json()["error"]["code"] == "return_exceeds_sold"


async def test_pieces_cannot_be_returned_twice_or_beyond_what_was_sold(client, shop):
    v = (await style(client, shop, sizes=["M"], price="999.00"))["variants"][0]["id"]
    sale = await paid_sale(client, shop, [(v, 2)])
    r = await give_back(client, shop["cashier"], sale, [(0, 3)])
    assert r.status_code == 409 and r.json()["error"]["code"] == "return_exceeds_sold"
    assert (await give_back(client, shop["cashier"], sale, [(0, 2)])).status_code == 201
    r = await give_back(client, shop["cashier"], sale, [(0, 1)])
    assert r.status_code == 409 and r.json()["error"]["code"] == "return_exceeds_sold"
    assert await on_hand(client, shop["owner"], v) == 20


async def test_only_a_paid_sale_can_be_returned_and_a_line_is_listed_once(client, shop):
    v = (await style(client, shop, sizes=["M"], price="999.00"))["variants"][0]["id"]
    unpaid = (await sell(client, shop["cashier"], [(v, 1)])).json()
    r = await give_back(client, shop["cashier"], unpaid, [(0, 1)])
    assert r.status_code == 409 and r.json()["error"]["code"] == "sale_not_paid"
    paid = await paid_sale(client, shop, [(v, 2)])
    r = await give_back(client, shop["cashier"], paid, [(0, 1), (0, 1)])
    assert r.status_code == 422 and r.json()["error"]["code"] == "duplicate_line"
    r = await client.post(f"{A}/returns", json={"sale_id": paid["id"], "lines": [{"sale_line_id": str(uuid.uuid4()), "quantity": 1}],
                                                "reason": "x y z", "refund_mode": "cash"}, headers=idem(shop["cashier"]))
    assert r.status_code == 404 and r.json()["error"]["code"] == "sale_line_not_found"


async def test_old_sales_need_a_manager_to_take_back(client, db, shop):
    v = (await style(client, shop, sizes=["M"], price="999.00"))["variants"][0]["id"]
    sale = await paid_sale(client, shop, [(v, 3)])
    stored = await db.get(ApparelSale, uuid.UUID(sale["id"]))
    stored.business_date = stored.business_date - timedelta(days=8)  # the default window is 7 days
    await db.commit()
    r = await give_back(client, shop["cashier"], sale, [(0, 1)])
    assert r.status_code == 403 and r.json()["error"]["code"] == "approval_required"
    r = await give_back(client, shop["cashier"], sale, [(0, 1)], approver_pin="7001")
    assert r.status_code == 201
    assert (await db.scalar(select(ApparelReturn.approved_by_id))) == shop["manager"].id
    assert (await give_back(client, shop["manager"], sale, [(0, 1)])).status_code == 201  # a manager approves themselves
    await client.put(f"{A}/settings", json={"return_days": 0}, headers=auth_headers(shop["owner"]))  # 0 means no limit
    assert (await give_back(client, shop["cashier"], sale, [(0, 1)])).status_code == 201


async def test_a_repeated_return_request_makes_one_return(client, db, shop):
    v = (await style(client, shop, sizes=["M"], price="999.00"))["variants"][0]["id"]
    sale = await paid_sale(client, shop, [(v, 3)])
    a = await give_back(client, shop["cashier"], sale, [(0, 1)], key="return-key-1")
    b = await give_back(client, shop["cashier"], sale, [(0, 1)], key="return-key-1")
    assert a.status_code == b.status_code == 201 and a.json()["id"] == b.json()["id"]
    c = await give_back(client, shop["cashier"], sale, [(0, 1)], client_return_id="till-return-1")
    d = await give_back(client, shop["cashier"], sale, [(0, 1)], client_return_id="till-return-1")
    assert c.json()["id"] == d.json()["id"]
    assert await db.scalar(select(func.count()).select_from(ApparelReturn)) == 2
    assert await on_hand(client, shop["owner"], v) == 19  # 3 sold, 2 distinct returns


# --- the cash day ------------------------------------------------------------------------------------------------------------


async def test_a_cash_refund_reduces_the_cash_the_drawer_should_hold(client, db, shop):
    cashier = shop["cashier"]
    opened = await client.post(f"{API}/staff/cash-day/open", json={"opening_cash": "500.00"}, headers=auth_headers(cashier))
    assert opened.status_code == 201, opened.text
    v = (await style(client, shop, sizes=["M"], price="999.00"))["variants"][0]["id"]
    sale = await paid_sale(client, shop, [(v, 2)])
    assert (await give_back(client, cashier, sale, [(0, 1)])).status_code == 201  # 999 cash back
    assert (await give_back(client, cashier, sale, [(0, 1)], refund_mode="store_credit")).status_code == 201  # not cash
    closed = await client.post(f"{API}/staff/cash-day/close", json={"counted_cash": "1499.00"}, headers=auth_headers(cashier))
    assert closed.status_code == 200, closed.text
    shift = await db.scalar(select(Shift))
    await db.refresh(shift)
    assert (shift.expected_cash, shift.cash_variance) == (D("1499.00"), D("0.00"))  # 500 + 1998 - 999


# --- store credit and exchanges ----------------------------------------------------------------------------------------------


async def test_store_credit_is_made_by_a_return_and_can_be_checked_at_any_branch(client, db, shop):
    v = (await style(client, shop, sizes=["M"], price="999.00"))["variants"][0]["id"]
    sale = await paid_sale(client, shop, [(v, 2)])
    r = await give_back(client, shop["cashier"], sale, [(0, 1)], refund_mode="store_credit")
    ret = r.json()
    assert ret["store_credit_code"] == ret["return_number"]
    credit = (await credit_of(client, shop["cashier"], ret["return_number"])).json()  # the code contains slashes
    assert (credit["amount"], credit["balance"], credit["status"]) == ("999.00", "999.00", "open")
    assert (await credit_of(client, shop["cashier"], "CN/KF1/nope")).status_code == 404
    stored = await db.scalar(select(ApparelStoreCredit))
    assert stored.organization_id == shop["org"].id


async def test_an_exchange_for_a_dearer_item_collects_only_the_difference(client, shop):
    cheap = (await style(client, shop, sizes=["M"], price="999.00", name="Shirt"))["variants"][0]["id"]
    dear = (await style(client, shop, sizes=["M"], price="1999.00", mrp=None, name="Jacket", category_name="Jackets"))["variants"][0]["id"]
    first = await paid_sale(client, shop, [(cheap, 1)])
    ret = (await give_back(client, shop["cashier"], first, [(0, 1)], refund_mode="store_credit")).json()
    new = (await sell(client, shop["cashier"], [(dear, 1)])).json()
    body = {"payment_mode": "credit_note", "amount": new["grand_total"], "credit_note_code": ret["return_number"]}
    r = await client.post(f"{A}/sales/{new['id']}/payments", json=body, headers=idem(shop["cashier"]))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["payment"]["amount"] == "999.00" and out["payment"]["payment_mode"] == "credit_note"
    assert out["sale"]["status"] == "partially_paid" and out["sale"]["balance_due"] == "1000.00"
    assert (await credit_of(client, shop["cashier"], ret["return_number"])).json()["status"] == "used"
    r = await pay(client, shop["cashier"], new["id"], "1000.00")
    assert r.json()["sale"]["status"] == "paid"


async def test_store_credit_partial_use_leaves_a_balance_and_a_used_credit_cannot_be_used_again(client, shop):
    cheap = (await style(client, shop, sizes=["M"], price="999.00", name="Shirt"))["variants"][0]["id"]
    small = (await style(client, shop, sizes=["M"], price="400.00", mrp=None, name="Socks", category_name="Socks"))["variants"][0]["id"]
    ret = (await give_back(client, shop["cashier"], await paid_sale(client, shop, [(cheap, 1)]), [(0, 1)], refund_mode="store_credit")).json()
    code = ret["return_number"]

    async def spend(sale, amount):
        body = {"payment_mode": "credit_note", "amount": amount, "credit_note_code": code}
        return await client.post(f"{A}/sales/{sale['id']}/payments", json=body, headers=idem(shop["cashier"]))

    one = (await sell(client, shop["cashier"], [(small, 1)])).json()
    r = await spend(one, "400.00")
    assert r.json()["sale"]["status"] == "paid"
    credit = (await credit_of(client, shop["cashier"], code)).json()
    assert (credit["balance"], credit["status"]) == ("599.00", "open")
    two = (await sell(client, shop["cashier"], [(small, 2)])).json()  # owes 800: the credit pays what it has
    r = await spend(two, "800.00")
    assert r.json()["payment"]["amount"] == "599.00" and r.json()["sale"]["balance_due"] == "201.00"
    assert (await credit_of(client, shop["cashier"], code)).json()["status"] == "used"
    r = await spend(two, "201.00")
    assert r.status_code == 409 and r.json()["error"]["code"] == "store_credit_unavailable"
    r = await client.post(f"{A}/sales/{two['id']}/payments", json={"payment_mode": "credit_note", "amount": "10.00"},
                          headers=idem(shop["cashier"]))
    assert r.status_code == 422  # a code is required
    split = {"payments": [{"payment_mode": "credit_note", "amount": "100.00", "credit_note_code": code},
                          {"payment_mode": "cash", "amount": "101.00"}]}
    r = await client.post(f"{A}/sales/{two['id']}/payments/split", json=split, headers=idem(shop["cashier"]))
    assert r.status_code == 422 and r.json()["error"]["code"] == "credit_note_not_in_split"


async def test_two_sales_cannot_overspend_one_store_credit(client, db, shop):
    v = (await style(client, shop, sizes=["M"], price="999.00"))["variants"][0]["id"]
    ret = (await give_back(client, shop["cashier"], await paid_sale(client, shop, [(v, 1)]), [(0, 1)], refund_mode="store_credit")).json()
    sales = [(await sell(client, shop["cashier"], [(v, 1)])).json() for _ in range(3)]

    async def spend(sale):
        body = {"payment_mode": "credit_note", "amount": sale["grand_total"], "credit_note_code": ret["return_number"]}
        return await client.post(f"{A}/sales/{sale['id']}/payments", json=body, headers=idem(shop["cashier"]))

    results = await asyncio.gather(*[spend(s) for s in sales])
    assert sorted(r.status_code for r in results) == [200, 409, 409]
    credit = await db.scalar(select(ApparelStoreCredit))
    await db.refresh(credit)
    assert (credit.balance, credit.status) == (D("0.00"), "used")
    paid = await db.scalar(text("SELECT COALESCE(SUM(amount), 0) FROM apparel_payments WHERE payment_mode = 'credit_note'"))
    assert paid == D("999.00")


# --- credit note, who may, whose ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("paper", [58, 80])
async def test_credit_note_renders_at_both_paper_widths(client, shop, paper):
    v = (await style(client, shop, sizes=["M"], price="999.00", name="Oxford Shirt"))["variants"][0]["id"]
    sale = await paid_sale(client, shop, [(v, 2)])
    ret = (await give_back(client, shop["cashier"], sale, [(0, 1)], refund_mode="store_credit")).json()
    r = await client.get(f"{A}/returns/{ret['id']}/receipt", params={"paper_mm": paper}, headers=auth_headers(shop["cashier"]))
    assert r.status_code == 200, r.text
    width = {58: 32, 80: 48}[paper]
    body = r.json()
    assert body["sale_number"] == ret["return_number"] and all(len(row["text"]) <= width for row in body["rows"])
    assert "CREDIT NOTE" in body["text"] and sale["sale_number"] in body["text"] and "Store credit" in body["text"]
    html = await client.get(f"{A}/returns/{ret['id']}/receipt", params={"format": "html", "paper_mm": paper},
                            headers=auth_headers(shop["cashier"]))
    assert html.headers["content-type"].startswith("text/html")


async def test_returns_are_found_by_sale_and_customer_phone(client, db, shop):
    v = (await style(client, shop, sizes=["M"], price="999.00"))["variants"][0]["id"]
    customer = await client.post(f"{API}/customers", json={"name": "Asha", "phone": "9876543210"}, headers=auth_headers(shop["cashier"]))
    assert customer.status_code == 201, customer.text
    sale = await paid_sale(client, shop, [(v, 2)], customer_id=customer.json()["id"])
    await paid_sale(client, shop, [(v, 1)])
    r = await client.get(f"{A}/sales", params={"phone": "98765"}, headers=auth_headers(shop["cashier"]))
    assert [s["id"] for s in r.json()["items"]] == [sale["id"]]
    await give_back(client, shop["cashier"], sale, [(0, 1)])
    r = await client.get(f"{A}/returns", params={"sale_id": sale["id"]}, headers=auth_headers(shop["cashier"]))
    assert r.json()["total"] == 1
    assert (await client.get(f"{A}/returns/{r.json()['items'][0]['id']}", headers=auth_headers(shop["cashier"]))).status_code == 200


async def test_returns_belong_to_their_business_and_category(client, db, redis_client, shop, other):
    v = (await style(client, shop, sizes=["M"], price="999.00"))["variants"][0]["id"]
    sale = await paid_sale(client, shop, [(v, 2)])
    ret = (await give_back(client, shop["cashier"], sale, [(0, 1)], refund_mode="store_credit")).json()
    them = other["owner"]
    assert (await give_back(client, them, sale, [(0, 1)])).status_code == 404
    assert (await client.get(f"{A}/returns/{ret['id']}", headers=auth_headers(them))).status_code == 404
    assert (await client.get(f"{A}/returns/{ret['id']}/receipt", headers=auth_headers(them))).status_code == 404
    assert (await credit_of(client, them, ret["return_number"])).status_code == 404
    mine = (await sell(client, them, [(v, 1)]))
    assert mine.status_code == 404

    await set_category(db, redis_client, shop["org"].id, "hardware")
    for method, url in (("POST", f"{A}/returns"), ("GET", f"{A}/returns"), ("GET", f"{A}/returns/{ret['id']}"),
                        ("GET", f"{A}/store-credits/{ret['return_number']}")):
        r = await client.request(method, url, json={} if method == "POST" else None, headers=auth_headers(shop["owner"]))
        assert r.status_code == 404 and r.json()["error"]["code"] == "wrong_business_category", url


async def test_rls_covers_the_return_tables(client, db, probe, shop, other):  # noqa: F811
    for s in (shop, other):
        v = (await style(client, s, sizes=["M"], price="999.00"))["variants"][0]["id"]
        sale = await paid_sale(client, s, [(v, 1)])
        assert (await give_back(client, s["cashier"], sale, [(0, 1)], refund_mode="store_credit")).status_code == 201
    async with probe() as session:
        for table in ("apparel_returns", "apparel_return_lines", "apparel_store_credits"):
            everything = await count(session, table)
            await session.execute(text("RESET app.org_scope"))
            mine = await count(session, table, shop["org"].id)
            theirs = await count(session, table, other["org"].id)
            assert 0 < mine < everything and 0 < theirs < everything and mine + theirs == everything, table
            await session.execute(text("RESET app.org_scope"))
