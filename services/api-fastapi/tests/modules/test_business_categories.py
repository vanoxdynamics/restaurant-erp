"""
Business categories: the workflow each kind of shop gets (café, grocery, retail next to restaurants), weighed items with decimal
quantities, counter sales with token numbers, shop item fields, barcode lookup and CSV item import.
"""

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.business import effective_settings
from app.core.entitlements import invalidate_entitlements
from app.models import KOT, MenuItem, Organization
from app.modules.billing import receipt as receipts
from tests import factories
from tests.factories import auth_headers, idem
from tests.flows import API, bill, create_order, fire, line, pay
from tests.modules.test_kitchen_module import SLIP, set_modules


async def set_category(db, redis_client, org_id, category: str, **overrides) -> None:
    org = await db.get(Organization, org_id)
    org.category, org.settings = category, overrides
    await db.commit()
    await invalidate_entitlements(redis_client, org_id)


async def make_item(db, **fields) -> MenuItem:
    item = await factories.make_menu_item(db, name=fields.pop("name", "Onion"), price=fields.pop("price", "40.00"),
                                          tax_rate=fields.pop("tax_rate", "5.00"))
    for key, value in fields.items():
        setattr(item, key, value)
    await db.commit()
    return item


async def counter_sale(client, user, items) -> dict:
    return await create_order(client, user, items=items, order_type="counter_sale")


# --- presets ---------------------------------------------------------------------------------------------------------------------


def test_presets_and_overrides():
    assert effective_settings("restaurant") == {"table_service": True, "kitchen_tickets": True, "quick_sale": False,
                                                "barcode": False, "weighed_items": False, "tokens": False}
    assert effective_settings("grocery")["barcode"] is True
    assert effective_settings("cafe", {"table_service": True, "junk": True, "tokens": "yes"})["table_service"] is True
    assert "junk" not in effective_settings("cafe", {"junk": True})
    assert effective_settings("cafe", {"tokens": "yes"})["tokens"] is True  # non-boolean override ignored
    assert effective_settings(None) == effective_settings("restaurant")


async def test_entitlements_carry_category_and_settings(client, db, redis_client, branch, cashier):
    r = await client.get(f"{API}/me/entitlements", headers=auth_headers(cashier))
    assert r.json()["category"] == "restaurant" and r.json()["settings"]["table_service"] is True

    await set_category(db, redis_client, branch.organization_id, "grocery", tokens=True)
    body = (await client.get(f"{API}/me/entitlements", headers=auth_headers(cashier))).json()
    assert body["category"] == "grocery"
    assert body["settings"] == {"table_service": False, "kitchen_tickets": False, "quick_sale": True, "barcode": True,
                                "weighed_items": True, "tokens": True}


# --- grocery: weighed items, no kitchen ------------------------------------------------------------------------------------------


async def test_grocery_sale_with_weighed_item_bills_without_kot(client, db, redis_client, branch, cashier):
    await set_category(db, redis_client, branch.organization_id, "grocery")
    onion = await make_item(db, name="Onion", price="40.00", unit="kg", sold_by_weight=True, hsn_code="0703", barcode="2000001")
    soap = await make_item(db, name="Soap", price="35.00", tax_rate="18.00", barcode="8901030000001")

    order = await counter_sale(client, cashier, [line(onion, "0.750"), line(soap, 2)])
    lines = {i["name_snapshot"]: i for i in order["items"]}
    assert lines["Onion"]["quantity"] == 0.75 and lines["Onion"]["unit_snapshot"] == "kg"
    assert lines["Onion"]["hsn_snapshot"] == "0703" and lines["Onion"]["line_total"] == "30.00"
    assert lines["Soap"]["quantity"] == 2 and isinstance(lines["Soap"]["quantity"], int)  # whole numbers stay JSON integers
    assert order["token_number"] is None

    b = await bill(client, cashier, order["id"])  # no "fire to the kitchen first" for a shop without a kitchen
    assert b["subtotal"] == "100.00"
    # 30.00 @ 5% + 70.00 @ 18% = 1.50 + 12.60
    assert Decimal(b["tax_cgst"]) + Decimal(b["tax_sgst"]) == Decimal("14.10")
    assert (await db.scalars(select(KOT))).all() == []

    r = await pay(client, cashier, b["id"], b["grand_total"], mode="upi", reference="UTR1")
    assert r.status_code == 200 and r.json()["bill"]["status"] == "paid"

    receipt = (await client.get(f"{API}/billing/bills/{b['id']}/receipt", headers=auth_headers(cashier))).json()
    assert "0.75 kg x Onion" in receipt["text"] and "@ 40.00/kg" in receipt["text"] and "HSN 0703" in receipt["text"]
    assert "2 x Soap" in receipt["text"] and "COUNTER SALE" in receipt["text"] and "Guests" not in receipt["text"]


async def test_pieces_must_be_whole_numbers(client, db, redis_client, branch, cashier):
    await set_category(db, redis_client, branch.organization_id, "retail")
    soap = await make_item(db, name="Soap", price="35.00")
    r = await client.post(f"{API}/orders", json={"order_type": "counter_sale", "items": [line(soap, "1.5")]}, headers=idem(cashier))
    assert r.status_code == 422 and r.json()["error"]["code"] == "whole_quantity_required"
    r = await client.post(f"{API}/orders", json={"order_type": "counter_sale", "items": [line(soap, "0.0001")]}, headers=idem(cashier))
    assert r.status_code == 422  # more than 3 decimals


# --- café: counter sale with token, KOT fired by billing --------------------------------------------------------------------------


async def test_cafe_counter_sale_gets_token_and_kot_on_billing(client, db, redis_client, branch, cashier):
    await set_category(db, redis_client, branch.organization_id, "cafe")
    await set_modules(db, redis_client, branch.organization_id, SLIP)  # no kitchen screen: the KOT prints at the counter
    latte = await make_item(db, name="Latte", price="180.00")

    first = await counter_sale(client, cashier, [line(latte)])
    second = await counter_sale(client, cashier, [line(latte, 2)])
    assert (first["token_number"], second["token_number"]) == (1, 2)

    b = await bill(client, cashier, second["id"])
    kots = (await db.scalars(select(KOT).where(KOT.order_id == second["id"]))).all()
    assert len(kots) == 1 and kots[0].fulfilment == "slip"

    queue = (await client.get(f"{API}/kots/print-queue", headers=auth_headers(cashier))).json()
    assert [k["id"] for k in queue] == [str(kots[0].id)]
    slip = (await client.get(f"{API}/kots/{kots[0].id}/slip", headers=auth_headers(cashier))).json()
    assert slip["token"] == 2 and "TOKEN 2" in slip["text"] and "Guests" not in slip["text"]

    receipt = (await client.get(f"{API}/billing/bills/{b['id']}/receipt", headers=auth_headers(cashier))).json()
    assert ("big", "center", "TOKEN 2") in [(r["style"], r["align"], r["text"]) for r in receipt["rows"]]


async def test_restaurant_still_requires_firing_before_billing(client, db, branch, cashier, waiter, table):
    item = await make_item(db, name="Dal", price="200.00")
    order = await create_order(client, waiter, table, items=[line(item)])
    r = await client.post(f"{API}/billing/bills", json={"order_id": order["id"]}, headers=idem(cashier))
    assert r.status_code == 409 and r.json()["error"]["code"] == "unfired_items"
    # A restaurant's takeaway is not a counter sale: same rule, no token.
    takeaway = await create_order(client, cashier, items=[line(item)])
    assert takeaway["token_number"] is None
    await fire(client, cashier, takeaway["id"])
    await bill(client, cashier, takeaway["id"])


async def test_restaurant_counter_sale_has_no_token(client, db, branch, cashier):
    item = await make_item(db, name="Dal", price="200.00")
    order = await counter_sale(client, cashier, [line(item)])
    assert order["token_number"] is None


# --- shop item fields and barcode lookup ----------------------------------------------------------------------------------------


async def test_item_fields_and_barcode_lookup(client, db, branch, manager, cashier):
    category = await factories.make_category(db, name="Staples")
    body = {"category_id": str(category.id), "name": "Basmati Rice", "base_price": "120.00", "tax_rate": "5.00",
            "barcode": "8901234567890", "sku": "RICE-1", "hsn_code": "1006", "unit": "kg", "sold_by_weight": True}
    r = await client.post(f"{API}/catalog/items", json=body, headers=auth_headers(manager))
    assert r.status_code == 201, r.text
    created = r.json()
    assert (created["unit"], created["hsn_code"], created["sold_by_weight"]) == ("kg", "1006", True)

    dup = await client.post(f"{API}/catalog/items", json={**body, "name": "Other", "sku": None}, headers=auth_headers(manager))
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "duplicate_barcode"

    found = await client.get(f"{API}/retail/items/lookup", params={"code": "8901234567890"}, headers=auth_headers(cashier))
    assert found.status_code == 200 and found.json()["id"] == created["id"]
    by_sku = await client.get(f"{API}/retail/items/lookup", params={"code": "RICE-1"}, headers=auth_headers(cashier))
    assert by_sku.json()["id"] == created["id"]
    missing = await client.get(f"{API}/retail/items/lookup", params={"code": "000"}, headers=auth_headers(cashier))
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "barcode_not_found"

    r = await client.patch(f"{API}/catalog/items/{created['id']}", json={"barcode": None, "unit": "pcs"}, headers=auth_headers(manager))
    assert r.status_code == 200 and r.json()["barcode"] is None and r.json()["unit"] == "pcs"


async def test_barcode_lookup_is_per_business(client, db, branch, cashier):
    other_org = await factories.make_org(db, slug="other", name="Other Shop")
    other_cat = await factories.make_category(db, name="X", org=other_org)
    item = await factories.make_menu_item(db, category=other_cat, name="Theirs")
    item.barcode = "555"
    await db.commit()
    r = await client.get(f"{API}/retail/items/lookup", params={"code": "555"}, headers=auth_headers(cashier))
    assert r.status_code == 404


# --- CSV import -------------------------------------------------------------------------------------------------------------------


CSV = """Name,Category,Price,GST Rate,HSN,Unit,Barcode,SKU,Sold by weight
Onion,Vegetables,40,5,0703,kg,2000001,,
Soap,Personal care,35.00,18,3401,pcs,8901030000001,SOAP-1,no
,Vegetables,10,5,,,,,
Bad price,Vegetables,abc,5,,,,,
Bad gst,Vegetables,10,7,,,,,
Twin,Vegetables,10,5,,,2000001,,

Milk,Dairy,28,0,0401,l,,MILK-1,yes
"""


async def test_csv_import_reports_bad_rows_and_imports_good_ones(client, db, branch, manager, cashier):
    dry = await client.post(f"{API}/retail/items/import", json={"csv": CSV, "dry_run": True}, headers=auth_headers(manager))
    assert dry.status_code == 200, dry.text
    assert (dry.json()["created"], dry.json()["dry_run"]) == (3, True)
    assert (await db.scalars(select(MenuItem))).all() == []

    r = await client.post(f"{API}/retail/items/import", json={"csv": CSV}, headers=auth_headers(manager))
    result = r.json()
    assert (result["created"], result["updated"], result["categories_created"]) == (3, 0, 3)
    assert [e["row"] for e in result["errors"]] == [4, 5, 6, 7]
    assert "name is missing" in result["errors"][0]["errors"]
    assert "also on line 2" in result["errors"][3]["errors"][0]

    onion = (await client.get(f"{API}/retail/items/lookup", params={"code": "2000001"}, headers=auth_headers(cashier))).json()
    assert (onion["unit"], onion["sold_by_weight"], onion["base_price"]) == ("kg", True, "40.00")
    milk = (await client.get(f"{API}/retail/items/lookup", params={"code": "MILK-1"}, headers=auth_headers(cashier))).json()
    assert (milk["unit"], milk["tax_rate"], milk["sold_by_weight"]) == ("l", "0.00", True)

    # Importing again updates by SKU / barcode / name instead of duplicating.
    again = await client.post(f"{API}/retail/items/import",
                              json={"csv": "name,category,price,sku\nSoap (new),Personal care,38,SOAP-1\nOnion,Vegetables,42,\n"},
                              headers=auth_headers(manager))
    assert (again.json()["created"], again.json()["updated"]) == (0, 2)
    soap = (await client.get(f"{API}/retail/items/lookup", params={"code": "SOAP-1"}, headers=auth_headers(cashier))).json()
    assert (soap["name"], soap["base_price"]) == ("Soap (new)", "38.00")


async def test_csv_import_needs_columns_and_a_manager(client, db, branch, manager, cashier):
    r = await client.post(f"{API}/retail/items/import", json={"csv": "name,price\nx,1\n"}, headers=auth_headers(manager))
    assert r.status_code == 422 and r.json()["error"]["details"]["missing"] == ["category"]
    r = await client.post(f"{API}/retail/items/import", json={"csv": CSV}, headers=auth_headers(cashier))
    assert r.status_code == 403


async def test_csv_import_refuses_moving_a_barcode_to_another_item(client, db, branch, manager):
    await make_item(db, name="Old", barcode="111")
    r = await client.post(f"{API}/retail/items/import", json={"csv": "name,category,price,barcode,sku\nNew,Mains,5,111,NEW-1\n"},
                          headers=auth_headers(manager))
    # The row matches nothing by SKU, then matches "Old" by barcode: it updates that item rather than clashing.
    assert r.json()["updated"] == 1 and r.json()["errors"] == []


# --- receipts (pure) -------------------------------------------------------------------------------------------------------------


def test_qty_text():
    from app.modules.kots.slip import qty_text
    assert qty_text(2) == "2" and qty_text(Decimal("2.000")) == "2"
    assert qty_text(Decimal("0.750"), "kg") == "0.75 kg" and qty_text(Decimal("500"), "g") == "500 g"


@pytest.mark.parametrize("paper", [58, 80])
def test_receipt_lines_fit_the_paper(paper):
    from datetime import datetime, timezone
    r = receipts.Receipt(
        branch_name="Shop", branch_code="S1", address=None, phone=None, gstin="29ABCDE1234F1Z5", bill_number="S1/2627/000001",
        order_number="ORD-1", order_type="counter_sale", table_number=None, guest_count=1, billed_at=datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc),
        cashier="A", lines=[receipts.ReceiptLine(quantity=Decimal("1.234"), name="Very long loose item name " * 3, amount=Decimal("99.99"),
                                                 unit="kg", unit_price=Decimal("81.03"), hsn="07031000")],
        subtotal=Decimal("99.99"), discount=Decimal("0"), discount_reason=None, cgst=Decimal("2.50"), sgst=Decimal("2.50"),
        grand_total=Decimal("104.99"), token=12,
    )
    rows = receipts.receipt_rows(r, paper)
    width = {58: 32, 80: 48}[paper]
    assert all(len(text) <= (width // 2 if style == "big" else width) for style, _, text in rows)
