from datetime import datetime
from typing import Annotated, Optional

from pydantic import BaseModel, Field, StringConstraints

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
