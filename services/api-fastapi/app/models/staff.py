import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import Role, ShiftStatus
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_check


class StaffUser(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "staff_users"
    __table_args__ = (
        # PIN-only login needs PINs unique among active staff of a branch.
        Index(
            "uq_staff_users_branch_pin_lookup", "branch_id", "pin_lookup", unique=True,
            postgresql_where=text("is_active AND pin_lookup IS NOT NULL"),
        ),
        enum_check("staff_users", "role", Role),
    )

    branch_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=True, index=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(100), unique=True, index=True, nullable=True)
    full_name: Mapped[str] = mapped_column(String(100), nullable=False)
    hashed_password: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    hashed_pin: Mapped[str] = mapped_column(String(255), nullable=False)  # 4-6 digit fast login PIN
    pin_lookup: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # HMAC(branch, PIN)
    role: Mapped[str] = mapped_column(String(30), default=Role.WAITER.value, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # bump to revoke tokens


class Shift(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "shifts"
    __table_args__ = (
        Index("uq_shifts_user_open", "user_id", unique=True, postgresql_where=text("status = 'open'")),
        enum_check("shifts", "status", ShiftStatus),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("staff_users.id"), nullable=False, index=True)
    branch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False, index=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    opening_cash: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    closing_cash: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    blind_drop_cash: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)  # Cash dropped without knowing expected total
    expected_cash: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    cash_variance: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=ShiftStatus.OPEN.value)
