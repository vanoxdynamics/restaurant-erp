import uuid
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, ConfigDict


class KOTItemCreate(BaseModel):
    order_item_id: uuid.UUID
    item_name: str
    quantity: int = 1
    notes: Optional[str] = None


class KOTItemRead(BaseModel):
    id: uuid.UUID
    kot_id: uuid.UUID
    order_item_id: uuid.UUID
    item_name: str
    quantity: int
    notes: Optional[str] = None
    status: str
    model_config = ConfigDict(from_attributes=True)


class KOTCreate(BaseModel):
    order_id: uuid.UUID
    station: str = "kitchen"  # kitchen, bar, grill, dessert
    items: List[KOTItemCreate]


class KOTStatusUpdate(BaseModel):
    status: str  # queued, preparing, ready, served


class VoidKOTRequest(BaseModel):
    void_reason: str


class KOTRead(BaseModel):
    id: uuid.UUID
    order_id: uuid.UUID
    kot_number: str
    station: str
    status: str
    void_reason: Optional[str] = None
    items: List[KOTItemRead] = []
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)
