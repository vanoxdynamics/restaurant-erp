import uuid
from decimal import Decimal
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class OrderItemBase(BaseModel):
    item_id: uuid.UUID
    variant_id: Optional[uuid.UUID] = None
    quantity: int = Field(default=1, ge=1)
    notes: Optional[str] = None


class OrderItemCreate(OrderItemBase):
    pass


class OrderItemRead(BaseModel):
    id: uuid.UUID
    order_id: uuid.UUID
    item_id: uuid.UUID
    variant_id: Optional[uuid.UUID] = None
    name_snapshot: str
    unit_price_snapshot: Decimal
    tax_rate_snapshot: Decimal
    quantity: int
    status: str
    notes: Optional[str] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class OrderCreate(BaseModel):
    branch_id: uuid.UUID
    table_id: Optional[uuid.UUID] = None
    order_type: str = "dine_in"
    guest_count: int = Field(default=1, ge=1)
    items: List[OrderItemCreate] = []


class OrderUpdate(BaseModel):
    version: int  # Expected current version for optimistic lock protection
    status: Optional[str] = None
    guest_count: Optional[int] = None
    table_id: Optional[uuid.UUID] = None


class OrderRead(BaseModel):
    id: uuid.UUID
    branch_id: uuid.UUID
    table_id: Optional[uuid.UUID] = None
    waiter_id: Optional[uuid.UUID] = None
    order_number: str
    order_type: str
    status: str
    guest_count: int
    subtotal: Decimal
    total_amount: Decimal
    version: int
    items: List[OrderItemRead] = []
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)
