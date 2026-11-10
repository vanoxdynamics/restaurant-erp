from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import DateTime, LargeBinary, String
from sqlalchemy.dialects.postgresql import JSONB

from app.core.enums import BusinessCategory, OrgStatus
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_check
from sqlalchemy.orm import Mapped, mapped_column


class Organization(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A subscriber (a business: restaurant, café, shop). Every branch, staff user and catalog row belongs to exactly one."""
    __tablename__ = "organizations"
    __table_args__ = (
        enum_check("organizations", "status", OrgStatus),
        enum_check("organizations", "category", BusinessCategory),
    )

    name: Mapped[str] = mapped_column(String(150), nullable=False)
    slug: Mapped[str] = mapped_column(String(60), unique=True, index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=OrgStatus.ACTIVE.value,
                                        server_default=OrgStatus.ACTIVE.value, nullable=False)
    legal_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    billing_email: Mapped[Optional[str]] = mapped_column(String(254), nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)  # ISO 3166-1 alpha-2
    suspended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    suspension_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # How the business presents itself on bills, set by the business (owner or manager). NULL name: `name` is used.
    display_name: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    # A small PNG or JPEG printed at the top of bills. Deferred: only loaded when asked for, never with every organization read.
    logo: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True, deferred=True)
    logo_content_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    logo_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # The kind of business; presets the workflow settings (app.core.business). Set by the platform operator.
    category: Mapped[str] = mapped_column(String(20), default=BusinessCategory.RESTAURANT.value,
                                          server_default=BusinessCategory.RESTAURANT.value, nullable=False)
    # Only the settings overridden for this business; everything else comes from the category preset.
    settings: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}", nullable=False)
