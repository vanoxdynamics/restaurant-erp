import uuid
from decimal import Decimal
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class PaymentCreate(BaseModel):
    payment_mode: str  # cash, card, upi
    amount: Decimal = Field(..., gt=Decimal("0.00"))
    reference_id: Optional[str] = None


class PaymentRead(BaseModel):
    id: uuid.UUID
    bill_id: uuid.UUID
    payment_mode: str
    amount: Decimal
    reference_id: Optional[str] = None
    status: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class BillCreate(BaseModel):
    order_id: uuid.UUID
    discount_amount: Decimal = Decimal("0.00")
    discount_reason: Optional[str] = None


class SplitPaymentRequest(BaseModel):
    payments: List[PaymentCreate]


class UPIQRResponse(BaseModel):
    bill_id: uuid.UUID
    bill_number: str
    amount: Decimal
    upi_intent_url: str
    qr_payload: str


class BillRead(BaseModel):
    id: uuid.UUID
    order_id: uuid.UUID
    bill_number: str
    subtotal: Decimal
    tax_cgst: Decimal
    tax_sgst: Decimal
    discount_amount: Decimal
    discount_reason: Optional[str] = None
    grand_total: Decimal
    status: str
    settled_at: Optional[datetime] = None
    payments: List[PaymentRead] = []
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)
