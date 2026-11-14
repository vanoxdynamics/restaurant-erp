import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Optional

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints

from app.core.enums import BillStatus


def normalize_phone(value: object) -> object:
    """
    Keep the digits of a phone number, so "+91 98765 43210", "098765-43210" and "9876543210" are one customer. Indian mobiles are
    stored as 10 digits; other numbers keep their country code.
    """
    if value is None or not isinstance(value, str):
        return value
    if not value.strip():
        return None
    if re.search(r"[^\d\s+().-]", value):
        raise ValueError("phone may only contain digits, spaces and + - ( )")
    digits = re.sub(r"\D", "", value)
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if not 7 <= len(digits) <= 15:
        raise ValueError("phone must have 7 to 15 digits")
    return digits


def _blank_to_none(value: object) -> object:
    return None if isinstance(value, str) and not value.strip() else value


def _upper(value: object) -> object:
    value = _blank_to_none(value)
    return value.strip().upper() if isinstance(value, str) else value


Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)]
Phone = Annotated[Optional[str], BeforeValidator(normalize_phone)]
# The constraints sit on the text itself, inside Optional, so a cleared (null or blank) field is not checked against them.
Gstin = Annotated[Optional[Annotated[str, StringConstraints(pattern=r"^[0-9A-Z]{15}$")]], BeforeValidator(_upper)]
Email = Annotated[
    Optional[Annotated[str, StringConstraints(strip_whitespace=True, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")]],
    BeforeValidator(_blank_to_none),
]
FreeText = Annotated[Optional[Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]], BeforeValidator(_blank_to_none)]


class CustomerCreate(BaseModel):
    name: Name
    phone: Phone = None
    email: Email = None
    gstin: Gstin = None
    address: FreeText = None
    notes: FreeText = None


class CustomerUpdate(BaseModel):
    """Only the fields sent are changed; send null to clear one. `is_active` is for owners and managers."""

    name: Optional[Name] = None
    phone: Phone = None
    email: Email = None
    gstin: Gstin = None
    address: FreeText = None
    notes: FreeText = None
    is_active: Optional[bool] = None


class CustomerRead(BaseModel):
    id: uuid.UUID
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    gstin: Optional[str] = None
    address: Optional[str] = None
    notes: Optional[str] = None
    is_active: bool
    visits: int = Field(default=0, description="Paid bills.")
    total_spent: Decimal = Field(default=Decimal("0.00"), description="Sum of paid bills, tax included.")
    last_visit_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class CustomerBillRead(BaseModel):
    id: uuid.UUID
    order_id: uuid.UUID
    branch_id: uuid.UUID
    bill_number: str
    business_date: date
    grand_total: Decimal
    status: BillStatus
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class OrderCustomerSet(BaseModel):
    customer_id: Optional[uuid.UUID] = Field(description="The customer to put on the order; null takes the customer off.")
