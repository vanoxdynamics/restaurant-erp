import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.enums import Fulfilment, KOTStatus, Station
from app.schemas.billing import PrintRowRead
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
    fulfilment: Fulfilment = Fulfilment.SCREEN
    print_count: int = 0
    last_printed_at: Optional[datetime] = None
    void_printed_at: Optional[datetime] = None  # when the VOID notice of a cancelled slip was printed
    void_reason: Optional[str] = None
    # Filled in for listings so a kitchen card can say which table it is for.
    order_number: Optional[str] = None
    table_number: Optional[str] = None
    items: List[KOTItemRead] = []
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class SlipLineRead(BaseModel):
    quantity: int
    name: str
    notes: Optional[str] = None
    cancelled: bool = False
    station: Optional[str] = None


class SlipRead(BaseModel):
    """A kitchen slip as data. `text` is the 80 mm plain-text rendering, handy for previews."""
    marker: str  # NEW | ADDITIONAL | REPRINT | VOID
    branch_name: str
    branch_code: str
    kot_number: str
    order_number: str
    station: str
    order_type: str
    table_number: Optional[str] = None
    guest_count: int
    fired_at: str
    fired_by: Optional[str] = None
    round: int
    print_count: int
    status: str
    fulfilment: str
    lines: List[SlipLineRead] = []
    order_notes: Optional[str] = None
    text: str
    warnings: List[str] = []
    paper_mm: int = 80
    rows: List[PrintRowRead] = []  # the slip line by line for the requested paper width (line printers, e.g. Pine Labs)
