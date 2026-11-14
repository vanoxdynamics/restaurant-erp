import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, List, Optional

from pydantic import BaseModel, Field, StringConstraints, model_validator

from app.core.enums import StockMovementKind
from app.schemas.common import Money, QuantityOut, Reason

# Stock quantities are in the item's unit with up to 3 decimals, like sale quantities, but a delivery can be far larger.
StockQuantity = Annotated[Decimal, Field(gt=0, le=999_999, max_digits=9, decimal_places=3)]
StockLevelQuantity = Annotated[Decimal, Field(ge=0, le=999_999, max_digits=9, decimal_places=3)]
StockDelta = Annotated[Decimal, Field(ge=-999_999, le=999_999, max_digits=9, decimal_places=3)]
Supplier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)]
InvoiceNumber = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]


class StockItemRead(BaseModel):
    """One tracked item in a branch. `cost_price` and `stock_value` are sent to owners and managers only."""
    item_id: uuid.UUID
    name: str
    category_id: uuid.UUID
    category_name: str
    unit: str
    sku: Optional[str] = None
    barcode: Optional[str] = None
    quantity: QuantityOut
    low_stock_level: Optional[QuantityOut] = None
    is_low: bool  # at or below the low-stock level
    is_out: bool  # nothing left on record
    is_tracked: bool
    cost_price: Optional[Decimal] = None
    stock_value: Optional[Decimal] = None
    updated_at: datetime


class StockSettingsUpdate(BaseModel):
    """Start or stop tracking an item in a branch, and set its alert level. Fields left out are not changed."""
    branch_id: Optional[uuid.UUID] = None
    is_tracked: Optional[bool] = None
    low_stock_level: Optional[StockLevelQuantity] = None  # null clears the alert
    cost_price: Optional[Money] = None


class PurchaseLineCreate(BaseModel):
    item_id: uuid.UUID
    quantity: StockQuantity
    unit_cost: Optional[Money] = None  # what one unit cost; becomes the item's cost price


class PurchaseCreate(BaseModel):
    branch_id: Optional[uuid.UUID] = None
    supplier_name: Optional[Supplier] = None
    invoice_number: Optional[InvoiceNumber] = None
    received_on: Optional[date] = None  # defaults to today at the branch
    notes: Optional[str] = Field(default=None, max_length=500)
    lines: List[PurchaseLineCreate] = Field(min_length=1, max_length=500)


class PurchaseLineRead(BaseModel):
    item_id: uuid.UUID
    name: str
    unit: str
    quantity: QuantityOut
    unit_cost: Optional[Decimal] = None
    line_cost: Optional[Decimal] = None


class PurchaseRead(BaseModel):
    id: uuid.UUID
    branch_id: uuid.UUID
    supplier_name: Optional[str] = None
    invoice_number: Optional[str] = None
    received_on: date
    notes: Optional[str] = None
    total_cost: Decimal
    created_by: Optional[str] = None
    created_at: datetime
    lines: List[PurchaseLineRead] = []


class StockAdjust(BaseModel):
    """
    Correct an item's stock by hand: either `delta` (add or remove this much: damage, expiry, a found carton) or `counted`
    (a physical count; the quantity on record becomes this). Tracking starts if the item was not tracked.
    """
    branch_id: Optional[uuid.UUID] = None
    item_id: uuid.UUID
    delta: Optional[StockDelta] = None
    counted: Optional[StockLevelQuantity] = None
    reason: Reason

    @model_validator(mode="after")
    def one_of(self) -> "StockAdjust":
        if (self.delta is None) == (self.counted is None):
            raise ValueError("Send exactly one of delta or counted")
        if self.delta is not None and self.delta == 0:
            raise ValueError("delta must not be zero")
        return self


class MovementRead(BaseModel):
    id: uuid.UUID
    item_id: uuid.UUID
    name: str
    unit: str
    kind: StockMovementKind
    quantity: QuantityOut  # signed: + in, - out
    balance_after: QuantityOut
    reason: Optional[str] = None
    purchase_id: Optional[uuid.UUID] = None
    order_id: Optional[uuid.UUID] = None
    created_by: Optional[str] = None
    created_at: datetime
