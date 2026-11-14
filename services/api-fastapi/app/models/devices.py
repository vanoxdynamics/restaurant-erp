import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Device(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    A tablet, phone or card terminal linked to a branch. The record is what lets a business see its devices, cut one off
    (`revoked_at`) and allow one to bill offline (`offline_enabled`, the `offline_billing` module).

    `series_letter` names the device's own bill series for offline bills. It is given once and never taken back or reused, even
    after the device is revoked, so two devices can never print the same bill number.
    """

    __tablename__ = "devices"
    __table_args__ = (
        Index("uq_devices_branch_series", "branch_id", "series_letter", unique=True,
              postgresql_where=text("series_letter IS NOT NULL")),
        CheckConstraint("series_letter IS NULL OR series_letter ~ '^[A-Z]$'", name="ck_devices_series_letter"),
        CheckConstraint("NOT offline_enabled OR (series_letter IS NOT NULL AND revoked_at IS NULL)", name="ck_devices_offline_needs_series"),
    )

    branch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False, index=True)
    # The id the device chose when it was linked; it is the subject of the device's token.
    device_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)  # "Front counter", set by a manager
    offline_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    series_letter: Mapped[Optional[str]] = mapped_column(String(1), nullable=True)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("staff_users.id"), nullable=True)
