from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Optional

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, LargeBinary, Numeric, String
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
        CheckConstraint("offline_max_hours BETWEEN 1 AND 72", name="ck_organizations_offline_max_hours"),
        CheckConstraint("offline_max_sales BETWEEN 1 AND 2000", name="ck_organizations_offline_max_sales"),
        CheckConstraint("offline_max_cash > 0 AND offline_max_cash <= 500000", name="ck_organizations_offline_max_cash"),
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
    # Offline billing, the business's own switch (the owner's choice) and the limits of each device's offline lease.
    # Nothing bills offline unless the `offline_billing` module, this switch and the device's own switch are all on.
    offline_allowed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    offline_max_hours: Mapped[int] = mapped_column(Integer, default=24, server_default="24", nullable=False)
    offline_max_sales: Mapped[int] = mapped_column(Integer, default=300, server_default="300", nullable=False)
    offline_max_cash: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("50000.00"),
                                                      server_default="50000.00", nullable=False)
