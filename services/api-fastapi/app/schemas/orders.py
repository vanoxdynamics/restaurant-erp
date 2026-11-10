import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.enums import OrderItemStatus, OrderStatus, OrderType, Station
from app.schemas.common import Notes, Pin, Quantity, QuantityOut, Reason


class OrderItemBase(BaseModel):
    item_id: uuid.UUID
    variant_id: Optional[uuid.UUID] = None
    quantity: Quantity = Decimal(1)
    notes: Notes = None


class OrderItemCreate(OrderItemBase):
    modifier_ids: List[uuid.UUID] = Field(default_factory=list, max_length=20)


class OrderItemModifierRead(BaseModel):
    id: uuid.UUID
    modifier_item_id: uuid.UUID
    name_snapshot: str
    price_snapshot: Decimal
    model_config = ConfigDict(from_attributes=True)


class OrderItemRead(BaseModel):
    id: uuid.UUID
    order_id: uuid.UUID
    item_id: uuid.UUID
    variant_id: Optional[uuid.UUID] = None
    name_snapshot: str
    variant_name_snapshot: Optional[str] = None
    station_snapshot: Station
    unit_price_snapshot: Decimal
    tax_rate_snapshot: Decimal
    quantity: QuantityOut
    unit_snapshot: str = "pcs"
    hsn_snapshot: Optional[str] = None
    line_total: Decimal
    status: OrderItemStatus
    notes: Optional[str] = None
    modifiers: List[OrderItemModifierRead] = []
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class OrderCreate(BaseModel):
    # Required only for owners operating without a home branch; others default to their own branch.
    branch_id: Optional[uuid.UUID] = None
    table_id: Optional[uuid.UUID] = None
    order_type: OrderType = OrderType.DINE_IN
    guest_count: int = Field(default=1, ge=1, le=100)
    notes: Notes = None
    items: List[OrderItemCreate] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def check_table(self) -> "OrderCreate":
        if self.order_type == OrderType.DINE_IN and self.table_id is None:
            raise ValueError("table_id is required for dine_in orders")
        if self.order_type != OrderType.DINE_IN and self.table_id is not None:
            raise ValueError("table_id is only allowed for dine_in orders")
        return self


class OrderUpdate(BaseModel):
    version: int  # Expected current version for optimistic lock protection
    guest_count: Optional[int] = Field(default=None, ge=1, le=100)
    notes: Notes = None


class OrderItemsAppend(BaseModel):
    version: int
    items: List[OrderItemCreate] = Field(min_length=1, max_length=100)


class OrderCancelRequest(BaseModel):
    version: int
    reason: Reason
    approver_pin: Optional[Pin] = None


class OrderItemCancelRequest(BaseModel):
    reason: Reason
    approver_pin: Optional[Pin] = None


class OrderRead(BaseModel):
    id: uuid.UUID
    branch_id: uuid.UUID
    table_id: Optional[uuid.UUID] = None
    waiter_id: Optional[uuid.UUID] = None
    order_number: str
    order_type: OrderType
    status: OrderStatus
    guest_count: int
    notes: Optional[str] = None
    token_number: Optional[int] = None
    business_date: date
    subtotal: Decimal = Field(description="Sum of non-cancelled line totals, before tax.")
    total_amount: Decimal = Field(description="Pre-tax running total; tax is computed on the bill.")
    version: int
    items: List[OrderItemRead] = []
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)
