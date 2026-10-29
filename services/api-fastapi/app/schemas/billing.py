import uuid
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.enums import BillStatus, PaymentMode, PaymentStatus
from app.schemas.common import Money, Pin, PositiveMoney, Reason


class PaymentCreate(BaseModel):
    payment_mode: PaymentMode
    amount: PositiveMoney
    reference_id: Optional[str] = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def check_reference(self) -> "PaymentCreate":
        if self.payment_mode in (PaymentMode.UPI, PaymentMode.CARD) and not self.reference_id:
            raise ValueError("reference_id is required for upi and card payments")
        return self


class PaymentRead(BaseModel):
    id: uuid.UUID
    bill_id: uuid.UUID
    payment_mode: PaymentMode
    amount: Decimal
    reference_id: Optional[str] = None
    status: PaymentStatus
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class BillCreate(BaseModel):
    order_id: uuid.UUID
    discount_amount: Money = Decimal("0.00")
    discount_reason: Optional[str] = Field(default=None, max_length=500)
    approver_pin: Optional[Pin] = None

    @model_validator(mode="after")
    def check_discount_reason(self) -> "BillCreate":
        if self.discount_amount > 0 and not (self.discount_reason and self.discount_reason.strip()):
            raise ValueError("discount_reason is required when a discount is applied")
        return self


class SplitPaymentRequest(BaseModel):
    payments: List[PaymentCreate] = Field(min_length=2, max_length=10)


class BillVoidRequest(BaseModel):
    reason: Reason
    approver_pin: Optional[Pin] = None


class UPIQRResponse(BaseModel):
    bill_id: uuid.UUID
    bill_number: str
    amount: Decimal
    upi_intent_url: str
    qr_payload: str


class BillRead(BaseModel):
    id: uuid.UUID
    branch_id: uuid.UUID
    order_id: uuid.UUID
    bill_number: str
    subtotal: Decimal
    tax_cgst: Decimal
    tax_sgst: Decimal
    discount_amount: Decimal
    discount_reason: Optional[str] = None
    grand_total: Decimal
    paid_amount: Decimal
    balance_due: Decimal
    status: BillStatus
    settled_at: Optional[datetime] = None
    payments: List[PaymentRead] = []
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class PaymentResult(BaseModel):
    payment: PaymentRead
    bill: BillRead
    change_due: Decimal = Decimal("0.00")
