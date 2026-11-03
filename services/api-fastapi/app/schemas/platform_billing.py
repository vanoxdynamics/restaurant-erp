import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import PaymentMethod
from app.schemas.common import Reason

PositiveAmount = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]


class PaymentCreate(BaseModel):
    amount: PositiveAmount
    method: PaymentMethod
    reference: Optional[str] = Field(default=None, min_length=2, max_length=100, description="Bank reference, UPI transaction id, cheque number")
    received_at: Optional[datetime] = None  # defaults to now
    note: Optional[str] = Field(default=None, max_length=500)


class PaymentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    amount: Decimal
    currency: str
    method: str
    reference: Optional[str]
    received_at: datetime
    note: Optional[str]
    recorded_by_admin: Optional[uuid.UUID]
    created_at: datetime


class InvoiceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    number: str
    organization_id: uuid.UUID
    organization_name: Optional[str] = None
    kind: str
    status: str
    billing_term: str
    period_start: datetime
    period_end: datetime
    currency: str
    amount: Decimal
    credit_applied: Decimal
    amount_due: Decimal
    amount_paid: Decimal
    balance: Decimal = Decimal("0.00")  # still to pay
    overdue: bool = False
    description: Optional[str]
    issued_at: datetime
    due_at: datetime
    paid_at: Optional[datetime]
    voided_at: Optional[datetime]
    void_reason: Optional[str]
    payments: List[PaymentRead] = []


class PaymentResult(BaseModel):
    invoice: InvoiceRead
    payment: PaymentRead
    settled: bool
    renewed: bool
    credit_added: Decimal


class VoidBody(BaseModel):
    reason: Reason


class SweepSummary(BaseModel):
    result: Dict[str, int]
