import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Optional

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

DeviceName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class DeviceRead(BaseModel):
    """A device linked to a branch. `series_letter` names its own bill series for offline bills, once offline is enabled."""
    id: uuid.UUID
    branch_id: uuid.UUID
    device_id: str
    name: Optional[str] = None
    offline_enabled: bool
    series_letter: Optional[str] = None
    last_seen_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    revoked_by_id: Optional[uuid.UUID] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class DeviceUpdate(BaseModel):
    """Only the fields sent change. `name: null` clears the name."""
    name: Optional[DeviceName] = None
    offline_enabled: Optional[bool] = None

    @model_validator(mode="after")
    def check_switch(self) -> "DeviceUpdate":
        if "offline_enabled" in self.model_fields_set and self.offline_enabled is None:
            raise ValueError("offline_enabled must be true or false")
        return self


class OfflineLeaseRead(BaseModel):
    """
    Permission, signed by the server, for this device to bill offline until `expires_at`, for at most `max_sales` sales and
    `max_cash` in cash. `lease` is the signed token the device keeps and sends with its offline sales.
    """
    lease: str
    device_id: str
    branch_id: uuid.UUID
    series_letter: str
    issued_at: datetime
    expires_at: datetime
    max_sales: int
    max_cash: Decimal
    server_time: datetime
