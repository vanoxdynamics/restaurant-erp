import uuid
from datetime import datetime
from typing import Annotated, List, Optional
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from app.core.enums import TableStatus

TableNumber = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)]
Name100 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class DiningTableBase(BaseModel):
    table_number: TableNumber
    capacity: int = Field(default=4, ge=1, le=50)


class DiningTableCreate(DiningTableBase):
    pass


class DiningTableRead(DiningTableBase):
    id: uuid.UUID
    floor_id: uuid.UUID
    status: TableStatus
    merged_into_table_id: Optional[uuid.UUID] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class TableTransferRequest(BaseModel):
    source_table_id: uuid.UUID
    target_table_id: uuid.UUID
    reason: Optional[str] = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def check_distinct(self) -> "TableTransferRequest":
        if self.source_table_id == self.target_table_id:
            raise ValueError("source and target tables must differ")
        return self


class TableMergeRequest(BaseModel):
    primary_table_id: uuid.UUID
    secondary_table_ids: List[uuid.UUID] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def check_ids(self) -> "TableMergeRequest":
        if len(set(self.secondary_table_ids)) != len(self.secondary_table_ids):
            raise ValueError("secondary_table_ids contains duplicates")
        if self.primary_table_id in self.secondary_table_ids:
            raise ValueError("primary table cannot also be a secondary table")
        return self


class FloorBase(BaseModel):
    name: Name100
    sort_order: int = 0


class FloorCreate(FloorBase):
    pass


class FloorRead(FloorBase):
    id: uuid.UUID
    branch_id: uuid.UUID
    tables: List[DiningTableRead] = []
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class BranchBase(BaseModel):
    name: Name100
    code: str = Field(pattern=r"^[A-Z0-9]{2,6}$")
    address: Optional[str] = Field(default=None, max_length=255)
    phone: Optional[str] = Field(default=None, max_length=30)
    is_active: bool = True
    timezone: str = "Asia/Kolkata"
    gstin: Optional[str] = Field(default=None, pattern=r"^[0-9A-Z]{15}$")
    upi_vpa: Optional[str] = Field(default=None, max_length=100, pattern=r"^[\w.\-]+@[\w.\-]+$")
    upi_payee_name: Optional[str] = Field(default=None, max_length=100)

    @field_validator("code", mode="before")
    @classmethod
    def uppercase_code(cls, v: object) -> object:
        return v.strip().upper() if isinstance(v, str) else v

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except Exception as exc:
            raise ValueError(f"unknown timezone {v!r}") from exc
        return v


class BranchCreate(BranchBase):
    pass


class BranchRead(BranchBase):
    id: uuid.UUID
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)
