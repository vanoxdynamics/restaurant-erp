import uuid
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, ConfigDict


class DiningTableBase(BaseModel):
    table_number: str
    capacity: int = 4
    status: str = "vacant"  # vacant, occupied, billed, reserved


class DiningTableCreate(DiningTableBase):
    floor_id: uuid.UUID


class DiningTableRead(DiningTableBase):
    id: uuid.UUID
    floor_id: uuid.UUID
    merged_into_table_id: Optional[uuid.UUID] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class TableTransferRequest(BaseModel):
    source_table_id: uuid.UUID
    target_table_id: uuid.UUID
    reason: Optional[str] = None


class TableMergeRequest(BaseModel):
    primary_table_id: uuid.UUID
    secondary_table_ids: List[uuid.UUID]


class FloorBase(BaseModel):
    name: str
    sort_order: int = 0


class FloorCreate(FloorBase):
    branch_id: uuid.UUID


class FloorRead(FloorBase):
    id: uuid.UUID
    branch_id: uuid.UUID
    tables: List[DiningTableRead] = []
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class BranchBase(BaseModel):
    name: str
    code: str
    address: Optional[str] = None
    phone: Optional[str] = None
    is_active: bool = True


class BranchCreate(BranchBase):
    pass


class BranchRead(BranchBase):
    id: uuid.UUID
    floors: List[FloorRead] = []
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)
