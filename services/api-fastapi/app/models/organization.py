from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, String

from app.core.enums import OrgStatus
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_check
from sqlalchemy.orm import Mapped, mapped_column


class Organization(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A subscriber (restaurant business). Every branch, staff user and menu row belongs to exactly one."""
    __tablename__ = "organizations"
    __table_args__ = (enum_check("organizations", "status", OrgStatus),)

    name: Mapped[str] = mapped_column(String(150), nullable=False)
    slug: Mapped[str] = mapped_column(String(60), unique=True, index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=OrgStatus.ACTIVE.value,
                                        server_default=OrgStatus.ACTIVE.value, nullable=False)
    legal_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    billing_email: Mapped[Optional[str]] = mapped_column(String(254), nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)  # ISO 3166-1 alpha-2
    suspended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    suspension_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
