import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.enums import KOTStatus, Station
from app.schemas.common import Pin, Reason


class KOTItemRead(BaseModel):
    id: uuid.UUID
    kot_id: uuid.UUID
    order_item_id: uuid.UUID
    item_name: str
    quantity: int
    notes: Optional[str] = None
    status: KOTStatus
    model_config = ConfigDict(from_attributes=True)


class KOTFireRequest(BaseModel):
    """Fire pending order items to their prep stations. Item details come from the order, not the client."""
    order_id: uuid.UUID
    order_item_ids: Optional[List[uuid.UUID]] = Field(default=None, min_length=1, max_length=100)


class KOTStatusUpdate(BaseModel):
    status: KOTStatus

    @field_validator("status")
    @classmethod
    def not_voided(cls, v: KOTStatus) -> KOTStatus:
        if v == KOTStatus.VOIDED:
            raise ValueError("use the void endpoint to void a KOT")
        return v


class VoidKOTRequest(BaseModel):
    void_reason: Reason
    approver_pin: Optional[Pin] = None


class KOTRead(BaseModel):
    id: uuid.UUID
    branch_id: uuid.UUID
    order_id: uuid.UUID
    kot_number: str
    station: Station
    status: KOTStatus
    void_reason: Optional[str] = None
    items: List[KOTItemRead] = []
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)
