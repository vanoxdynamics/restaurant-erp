import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Optional

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    EmailStr,
    Field,
    SecretStr,
    StringConstraints,
    model_validator,
)

from app.core.enums import MANAGER_ROLES, Role, ShiftStatus
from app.schemas.common import Money, Pin

Username = Annotated[
    str,
    BeforeValidator(lambda v: v.strip().lower() if isinstance(v, str) else v),
    StringConstraints(min_length=3, max_length=50, pattern=r"^[a-z0-9_.]+$"),
]
FullName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class PINAuthRequest(BaseModel):
    pin: Pin
    branch_id: uuid.UUID
    device_id: Optional[str] = Field(default=None, max_length=100)
    # When set, the PIN is checked against this one person (and locks out per person), the way the
    # POS "who's working" screen signs in. Without it the PIN alone identifies the user (legacy).
    staff_id: Optional[uuid.UUID] = None


class DeviceSetupCodeCreate(BaseModel):
    branch_id: Optional[uuid.UUID] = None


class DeviceSetupCode(BaseModel):
    code: str
    branch_id: uuid.UUID
    branch_name: str
    expires_in: int


class DeviceLinkRequest(BaseModel):
    code: Annotated[str, StringConstraints(strip_whitespace=True, min_length=8, max_length=12)]
    device_id: str = Field(min_length=3, max_length=100)


class DeviceTokenResponse(BaseModel):
    device_token: str
    branch_id: uuid.UUID
    branch_name: str


class RosterEntry(BaseModel):
    id: uuid.UUID
    full_name: str
    role: Role
    model_config = ConfigDict(from_attributes=True)


class PasswordLoginRequest(BaseModel):
    username: Username
    password: SecretStr


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    role: Role
    user_id: uuid.UUID
    branch_id: Optional[uuid.UUID] = None
    username: str
    full_name: str


class StaffUserBase(BaseModel):
    username: Username
    email: Optional[EmailStr] = None
    full_name: FullName
    role: Role = Role.WAITER
    branch_id: Optional[uuid.UUID] = None
    is_active: bool = True


class StaffUserCreate(StaffUserBase):
    password: Optional[SecretStr] = Field(default=None, min_length=10, max_length=128)
    pin: Pin

    @model_validator(mode="after")
    def check_password_for_managers(self) -> "StaffUserCreate":
        if self.role in MANAGER_ROLES and self.password is None:
            raise ValueError("password is required for owner and manager accounts")
        return self


class StaffUserUpdate(BaseModel):
    full_name: Optional[FullName] = None
    email: Optional[EmailStr] = None
    role: Optional[Role] = None
    branch_id: Optional[uuid.UUID] = None
    is_active: Optional[bool] = None


class PinResetRequest(BaseModel):
    pin: Pin


class StaffUserRead(StaffUserBase):
    id: uuid.UUID
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ShiftOpenRequest(BaseModel):
    opening_cash: Money = Decimal("0.00")


class ShiftCloseRequest(BaseModel):
    blind_drop_cash: Money  # Blind drop: entered without knowing expected system cash


class ShiftRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    branch_id: uuid.UUID
    start_time: datetime
    end_time: Optional[datetime] = None
    opening_cash: Decimal
    closing_cash: Optional[Decimal] = None
    blind_drop_cash: Optional[Decimal] = None
    status: ShiftStatus
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ShiftReadManager(ShiftRead):
    """Includes reconciliation figures; only returned to managers and owners."""
    expected_cash: Optional[Decimal] = None
    cash_variance: Optional[Decimal] = None


class ShiftHistoryRead(ShiftReadManager):
    user_name: str
    cash_sales: Optional[Decimal] = None
