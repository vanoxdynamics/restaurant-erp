import uuid
from decimal import Decimal
from typing import Optional
from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class StaffUser(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "staff_users"

    branch_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=True, index=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(100), unique=True, index=True, nullable=True)
    full_name: Mapped[str] = mapped_column(String(100), nullable=False)
    hashed_password: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    hashed_pin: Mapped[str] = mapped_column(String(255), nullable=False)  # 4-6 digit fast login PIN
    role: Mapped[str] = mapped_column(String(30), default="waiter", index=True)  # owner, manager, captain, waiter, cashier, kitchen
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Shift(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "shifts"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("staff_users.id"), nullable=False, index=True)
    branch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False, index=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    opening_cash: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    closing_cash: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    blind_drop_cash: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)  # Cash dropped without knowing expected total
    status: Mapped[str] = mapped_column(String(20), default="open")  # open, closed
