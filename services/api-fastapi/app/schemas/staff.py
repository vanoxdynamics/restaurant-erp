import uuid
from decimal import Decimal
from typing import Optional
from datetime import datetime
from pydantic import BaseModel, ConfigDict, EmailStr, Field


class PINAuthRequest(BaseModel):
    pin: str = Field(..., min_length=4, max_length=6)
    branch_id: Optional[uuid.UUID] = None


class PasswordLoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    user_id: uuid.UUID
    username: str
    full_name: str


class StaffUserBase(BaseModel):
    username: str
    email: Optional[EmailStr] = None
    full_name: str
    role: str = "waiter"
    branch_id: Optional[uuid.UUID] = None
    is_active: bool = True


class StaffUserCreate(StaffUserBase):
    password: Optional[str] = None
    pin: str = Field(..., min_length=4, max_length=6)


class StaffUserRead(StaffUserBase):
    id: uuid.UUID
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ShiftOpenRequest(BaseModel):
    branch_id: uuid.UUID
    opening_cash: Decimal = Decimal("0.00")


class BlindDropRequest(BaseModel):
    blind_drop_cash: Decimal = Field(..., ge=Decimal("0.00"))


class ShiftCloseRequest(BaseModel):
    blind_drop_cash: Decimal = Field(..., ge=Decimal("0.00"))  # Blind drop: entered without knowing expected system cash


class ShiftRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    branch_id: uuid.UUID
    start_time: datetime
    end_time: Optional[datetime] = None
    opening_cash: Decimal
    closing_cash: Optional[Decimal] = None
    blind_drop_cash: Optional[Decimal] = None
    status: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)
