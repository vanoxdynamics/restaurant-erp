"""Stage F of the shop modules: new categories, category guard, extra cash sources, document numbering, shop receipts."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.auth import CurrentUser, require_category
from app.core.business import CATEGORY_LABELS, PRESETS, effective_settings
from app.core.cash import extra_cash, register_cash_source, unregister_cash_source
from app.core.entitlements import invalidate_entitlements
from app.core.enums import BusinessCategory, Role
from app.core.errors import NotFound
from app.core.numbering import next_document_number
from app.models import Organization, Shift
from app.modules.retailkit import receipt as rk
from tests import factories
from tests.factories import auth_headers
from tests.flows import API


async def set_category(db, redis_client, org_id, category: str) -> None:
    org = await db.get(Organization, org_id)
    org.category = category
    await db.commit()
    await invalidate_entitlements(redis_client, org_id)


def as_user(staff) -> CurrentUser:
    return CurrentUser(id=staff.id, branch_id=staff.branch_id, role=Role(staff.role), username=staff.username,
                       full_name=staff.full_name, auth_method="pwd", organization_id=staff.organization_id)


def test_new_categories_have_labels_and_presets():
    for category in (BusinessCategory.APPAREL, BusinessCategory.HARDWARE):
        assert category.value in CATEGORY_LABELS and category.value in PRESETS
        assert effective_settings(category.value) == {"table_service": False, "kitchen_tickets": False, "quick_sale": True,
                                                      "barcode": True, "weighed_items": False, "tokens": False}
    assert CATEGORY_LABELS["apparel"] == "Dress / footwear shop"
    assert CATEGORY_LABELS["hardware"] == "Hardware / electrical shop"


async def test_organizations_can_be_saved_as_the_new_categories(client, db, redis_client, branch, cashier):
    for category in ("apparel", "hardware"):
        await set_category(db, redis_client, branch.organization_id, category)
        r = await client.get(f"{API}/me/entitlements", headers=auth_headers(cashier))
        assert r.json()["category"] == category


async def test_category_guard(db, redis_client, branch, cashier):
    guard = require_category(BusinessCategory.APPAREL)
    user = as_user(cashier)
    with pytest.raises(NotFound) as e:  # a restaurant cannot reach dress-shop routes
        await guard(user=user, db=db, redis_client=redis_client)
    assert e.value.code == "wrong_business_category"

    await set_category(db, redis_client, branch.organization_id, "apparel")
    assert (await guard(user=user, db=db, redis_client=redis_client)) is user
    with pytest.raises(NotFound):
        await require_category(BusinessCategory.HARDWARE)(user=user, db=db, redis_client=redis_client)
    both = require_category(BusinessCategory.APPAREL, BusinessCategory.HARDWARE)
    assert (await both(user=user, db=db, redis_client=redis_client)) is user


async def test_no_sources_means_no_extra_cash(db, branch):
    assert await extra_cash(db, branch_id=branch.id, since=datetime.now(timezone.utc)) == Decimal("0")
    assert await extra_cash(db, branch_id=uuid.uuid4(), shift_id=uuid.uuid4()) == Decimal("0")


async def test_cash_sources_add_to_the_cash_day_and_personal_shifts(client, db, branch, cashier):
    calls = []

    async def sales(db, *, branch_id, since, shift_id):
        calls.append({"branch_id": branch_id, "since": since, "shift_id": shift_id})
        return Decimal("150.50") if branch_id == branch.id else Decimal("999")

    async def refunds(db, *, branch_id, since, shift_id):
        return Decimal("-20.00")

    register_cash_source(sales)
    register_cash_source(sales)  # registering twice must not count twice
    register_cash_source(refunds)
    try:
        url = f"{API}/staff/cash-day"
        opened = await client.post(f"{url}/open", json={"opening_cash": "500.00"}, headers=auth_headers(cashier))
        assert opened.status_code == 201, opened.text
        closed = await client.post(f"{url}/close", json={"counted_cash": "630.50"}, headers=auth_headers(cashier))
        assert closed.status_code == 200, closed.text
        assert len(calls) == 1
        assert calls[0]["branch_id"] == branch.id and calls[0]["since"] is not None and calls[0]["shift_id"] is None

        # A personal shift asks by shift id.
        r = await client.post(f"{API}/staff/shifts/open", json={"opening_cash": "100.00"}, headers=auth_headers(cashier))
        assert r.status_code == 201, r.text
        r = await client.post(f"{API}/staff/shifts/close", json={"blind_drop_cash": "230.50"}, headers=auth_headers(cashier))
        assert r.status_code == 200, r.text
        assert calls[-1]["shift_id"] is not None and calls[-1]["since"] is None
    finally:
        unregister_cash_source(sales)
        unregister_cash_source(refunds)

    shifts = (await db.scalars(select(Shift).order_by(Shift.start_time))).all()
    assert [s.expected_cash for s in shifts] == [Decimal("630.50"), Decimal("230.50")]
    assert [s.cash_variance for s in shifts] == [Decimal("0.00"), Decimal("0.00")]


async def test_credit_note_and_estimate_series(db, branch):
    other = await factories.make_branch(db, code="MUM1", name="Bandra")
    bill = await next_document_number(db, branch, "bill")
    fy = bill.split("/")[1]
    assert bill == f"BLR1/{fy}/000001"
    assert await next_document_number(db, branch, "credit_note") == f"CN/BLR1/{fy}/000001"
    assert await next_document_number(db, branch, "credit_note") == f"CN/BLR1/{fy}/000002"
    assert await next_document_number(db, branch, "estimate") == f"EST/BLR1/{fy}/000001"
    assert await next_document_number(db, other, "estimate") == f"EST/MUM1/{fy}/000001"
    assert await next_document_number(db, branch, "bill") == f"BLR1/{fy}/000002"  # the bill series is not disturbed
    assert (await next_document_number(db, branch, "order")).startswith("ORD-BLR1-")
    await db.rollback()


def _invoice(**overrides) -> rk.RetailReceipt:
    fields = dict(
        title="TAX INVOICE", number="BLR1/2627/000001", issued_at=datetime(2026, 11, 16, 12, 30, tzinfo=timezone.utc), branch_name="Indiranagar",
        business_name="Kavya Fashions", gstin="29ABCDE1234F1Z5", customer_name="Asha", customer_gstin="29AAAAA0000A1Z5",
        lines=[rk.RetailLine(name="Oxford Shirt", amount=Decimal("999.00"), quantity_text="1", detail="M / Blue", hsn="6205",
                             tax_rate=Decimal("5.00"), mrp_total=Decimal("1299.00")),
               rk.RetailLine(name="Silk Kurta with a very long name that has to wrap", amount=Decimal("2999.00"),
                             quantity_text="1", detail="L / Maroon", tax_rate=Decimal("18.00"))],
        subtotal=Decimal("3998.00"), discount=Decimal("0"), taxable_value=Decimal("3492.96"), cgst=Decimal("252.53"),
        sgst=Decimal("252.51"), igst=Decimal("0"), round_off=Decimal("0"), grand_total=Decimal("3998.00"),
        tax_summary=rk.tax_summary([(Decimal("18.00"), Decimal("2541.53"), Decimal("457.47")),
                                    (Decimal("5.00"), Decimal("951.43"), Decimal("47.57"))]),
        payments=[rk.RetailPayment(mode="cash", amount=Decimal("3998.00"))],
    )
    fields.update(overrides)
    return rk.RetailReceipt(**fields)  # type: ignore[arg-type]


def test_shop_receipt_renders_at_both_widths():
    receipt = _invoice()
    assert [r[0] for r in receipt.tax_summary] == [Decimal("5.00"), Decimal("18.00")]
    for width, columns in ((58, 32), (80, 48)):
        rows = rk.rows(receipt, width)
        assert all(len(text) <= columns for _, _, text in rows), width
        text = rk.render_text(receipt, width)
        assert "TAX INVOICE" in text and "M / Blue" in text and "You saved 300.00" in text and "GST 5%" in text
        assert "CGST" in text and "IGST" not in text and "GSTIN: 29AAAAA0000A1Z5" in text
    assert "<title>BLR1/2627/000001</title>" in rk.render_html(receipt, 58)


def test_shop_receipt_igst_round_off_and_footer():
    receipt = _invoice(
        title="ESTIMATE", number="EST/BLR1/2627/000001", customer_name=None, customer_gstin=None,
        lines=[rk.RetailLine(name="Cable", amount=Decimal("3500.00"), quantity_text="1 coil", detail="90 m x 38.89")],
        cgst=Decimal("0"), sgst=Decimal("0"), igst=Decimal("533.90"), round_off=Decimal("-0.40"),
        grand_total=Decimal("3499.60"), payments=[], tax_summary=[],
        reference_lines=["Valid until 01 Dec 2026"], footer_lines=[("On account", "3,499.60")], closing="",
    )
    text = rk.render_text(receipt, 80)
    assert "IGST" in text and "CGST" not in text and "Round off" in text and "-0.40" in text
    assert "Valid until 01 Dec 2026" in text and "On account" in text and "You saved" not in text
