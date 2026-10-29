import uuid
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.billing import BillCreate, PaymentCreate, SplitPaymentRequest
from app.schemas.catalog import MenuItemCreate, ModifierGroupCreate
from app.schemas.floor import BranchCreate, TableMergeRequest, TableTransferRequest
from app.schemas.kots import KOTStatusUpdate
from app.schemas.orders import OrderCreate, OrderItemCreate, OrderItemsAppend
from app.schemas.staff import PINAuthRequest, StaffUserCreate

U = uuid.uuid4


@pytest.mark.parametrize("factory", [
    lambda: MenuItemCreate(name="X", base_price=Decimal("-1"), category_id=U()),
    lambda: MenuItemCreate(name="X", base_price=Decimal("12345678901.00"), category_id=U()),
    lambda: MenuItemCreate(name="X", base_price=Decimal("10.001"), category_id=U()),
    lambda: MenuItemCreate(name="X", base_price=Decimal("10"), tax_rate=Decimal("30"), category_id=U()),
    lambda: MenuItemCreate(name="X", base_price=Decimal("10"), station="pantry", category_id=U()),
    lambda: MenuItemCreate(name="", base_price=Decimal("10"), category_id=U()),
    lambda: ModifierGroupCreate(name="G", min_selection=2, max_selection=1),
    lambda: OrderItemCreate(item_id=U(), quantity=0),
    lambda: OrderCreate(order_type="dine_in"),
    lambda: OrderCreate(order_type="takeaway", table_id=U()),
    lambda: OrderCreate(order_type="buffet"),
    lambda: OrderItemsAppend(version=1, items=[]),
    lambda: KOTStatusUpdate(status="voided"),
    lambda: PINAuthRequest(pin="123", branch_id=U()),
    lambda: PINAuthRequest(pin="12a4", branch_id=U()),
    lambda: PINAuthRequest(pin="1234"),
    lambda: StaffUserCreate(username="boss", full_name="B", role="owner", pin="1234"),
    lambda: StaffUserCreate(username="w", full_name="W", pin="1234"),
    lambda: StaffUserCreate(username="bad name!", full_name="W", pin="1234"),
    lambda: StaffUserCreate(username="waiter2", full_name="W", role="superuser", pin="1234"),
    lambda: PaymentCreate(payment_mode="upi", amount=Decimal("10")),
    lambda: PaymentCreate(payment_mode="cash", amount=Decimal("0")),
    lambda: PaymentCreate(payment_mode="split", amount=Decimal("10")),
    lambda: BillCreate(order_id=U(), discount_amount=Decimal("10")),
    lambda: BillCreate(order_id=U(), discount_amount=Decimal("-1"), discount_reason="x"),
    lambda: SplitPaymentRequest(payments=[PaymentCreate(payment_mode="cash", amount=Decimal("1"))]),
    lambda: TableMergeRequest(primary_table_id=(t := U()), secondary_table_ids=[t]),
    lambda: TableMergeRequest(primary_table_id=U(), secondary_table_ids=[(t := U()), t]),
    lambda: TableMergeRequest(primary_table_id=U(), secondary_table_ids=[]),
    lambda: TableTransferRequest(source_table_id=(t := U()), target_table_id=t),
    lambda: BranchCreate(name="B", code="toolongcode"),
    lambda: BranchCreate(name="B", code="BLR1", timezone="Mars/Olympus"),
])
def test_invalid_inputs_are_rejected(factory):
    with pytest.raises(ValidationError):
        factory()


def test_valid_samples():
    assert OrderCreate(order_type="dine_in", table_id=U()).guest_count == 1
    assert OrderCreate(order_type="takeaway").table_id is None
    assert StaffUserCreate(username="Waiter.One", full_name="W", pin="123456").username == "waiter.one"
    assert BranchCreate(name="B", code="blr1").code == "BLR1"
    assert PaymentCreate(payment_mode="upi", amount=Decimal("10.50"), reference_id="UTR1").amount == Decimal("10.50")
    assert BillCreate(order_id=U(), discount_amount=Decimal("5"), discount_reason="regular").discount_amount == 5
