import uuid
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, Field

from app.core.enums import KOTStatus, Station, VoidRequestStatus
from app.schemas.common import QuantityOut, Reason


class VoidRequestCreate(BaseModel):
    reason: Reason


class VoidRequestReview(BaseModel):
    note: Optional[str] = Field(default=None, max_length=500)


class VoidRequestItem(BaseModel):
    name: str
    quantity: QuantityOut


class VoidRequestRead(BaseModel):
    id: uuid.UUID
    branch_id: uuid.UUID
    kot_id: uuid.UUID
    kot_number: str
    station: Station
    kot_status: KOTStatus
    order_id: uuid.UUID
    order_number: str
    table_number: Optional[str] = None
    items: List[VoidRequestItem]
    amount: Decimal
    reason: str
    status: VoidRequestStatus
    requested_by_id: uuid.UUID
    requested_by_name: str
    reviewed_by_name: Optional[str] = None
    review_note: Optional[str] = None
    created_at: datetime
    reviewed_at: Optional[datetime] = None
