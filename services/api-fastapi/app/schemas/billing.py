import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional, Union

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
    provider: str = "manual"
    # For terminal payments: billing_ref_no (the reference given to the terminal) and the terminal's masked transaction details.
    provider_meta: Optional[Dict[str, Any]] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


TerminalDetails = Dict[str, Union[str, int, float, bool, None]]


class TerminalPaymentStart(BaseModel):
    payment_mode: Literal[PaymentMode.CARD, PaymentMode.UPI]
    amount: PositiveMoney
    provider: Literal["pinelabs"] = "pinelabs"


class TerminalPaymentComplete(BaseModel):
    outcome: Literal["approved", "declined"]
    approval_code: Optional[str] = Field(default=None, max_length=20)
    message: Optional[str] = Field(default=None, max_length=255)
    # "terminal": the answer came from the device; "manual": the cashier resolved an unconfirmed payment from the charge slip.
    source: Literal["terminal", "manual"] = "terminal"
    details: TerminalDetails = Field(default_factory=dict, max_length=40)

    @model_validator(mode="after")
    def check_approval(self) -> "TerminalPaymentComplete":
        if self.outcome == "approved" and not (self.approval_code and self.approval_code.strip()):
            raise ValueError("approval_code is required for an approved payment")
        return self


class PaymentReverse(BaseModel):
    reason: Reason
    approver_pin: Optional[Pin] = None
    details: TerminalDetails = Field(default_factory=dict, max_length=40)
    # Validate (reversible, manager approval) without changing anything: the app checks before voiding on the terminal, so a
    # wrong PIN can never leave money refunded on the card while the bill still shows it paid.
    check_only: bool = False


class PrintRowRead(BaseModel):
    """One printed line: style normal | bold | big (double width), align left | center. Already wrapped to the paper."""
    style: str
    align: str
    text: str


class ReceiptRead(BaseModel):
    bill_number: str
    paper_mm: int
    rows: List[PrintRowRead]
    text: str
    logo_data_url: Optional[str] = None  # the business's logo, for printers that can print an image above the rows


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
