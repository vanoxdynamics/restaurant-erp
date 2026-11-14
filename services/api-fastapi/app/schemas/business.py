from datetime import datetime
from decimal import Decimal
from typing import Annotated, Optional

from pydantic import BaseModel, Field, StringConstraints, model_validator

DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=150)]


class BusinessProfileRead(BaseModel):
    """How the business appears on its bills."""
    name: str  # what the bill prints: the display name, or the registered name when none is set
    display_name: Optional[str] = None
    registered_name: str  # the name the platform has on record
    logo_data_url: Optional[str] = None  # data:image/png;base64,... (small), or none
    logo_updated_at: Optional[datetime] = None


class BusinessProfileUpdate(BaseModel):
    display_name: Optional[DisplayName] = None  # null goes back to the registered name


class LogoUpload(BaseModel):
    image_base64: str = Field(min_length=8, max_length=14_000_000)  # base64 image; the exact size limit (500 KB) is checked in the service so the message is friendly


class OfflineSettingsRead(BaseModel):
    """
    The business's own offline billing switch and the limits of each device's offline lease. `module_enabled` says whether
    the subscription includes offline billing; without it the switch cannot be turned on.
    """
    offline_allowed: bool
    offline_max_hours: int
    offline_max_sales: int
    offline_max_cash: Decimal
    module_enabled: bool


class OfflineSettingsUpdate(BaseModel):
    """Only the fields sent change."""
    offline_allowed: Optional[bool] = None
    offline_max_hours: Optional[int] = Field(default=None, ge=1, le=72)
    offline_max_sales: Optional[int] = Field(default=None, ge=1, le=2000)
    offline_max_cash: Optional[Decimal] = Field(default=None, gt=0, le=500000, max_digits=10, decimal_places=2)

    @model_validator(mode="after")
    def no_nulls(self) -> "OfflineSettingsUpdate":
        for name in self.model_fields_set:
            if getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self
