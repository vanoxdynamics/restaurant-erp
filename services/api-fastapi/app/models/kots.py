import uuid
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import KOTStatus, Station, VoidRequestStatus
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_check


class KOT(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "kots"
    __table_args__ = (
        UniqueConstraint("branch_id", "kot_number", name="uq_kots_branch_number"),
        Index("ix_kots_branch_station_status", "branch_id", "station", "status"),
        enum_check("kots", "status", KOTStatus),
        enum_check("kots", "station", Station),
    )

    branch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False)
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False, index=True)
    kot_number: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    station: Mapped[str] = mapped_column(String(50), default=Station.KITCHEN.value, index=True)
    status: Mapped[str] = mapped_column(String(30), default=KOTStatus.QUEUED.value, index=True)
    void_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    fired_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("staff_users.id"), nullable=True)
    voided_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("staff_users.id"), nullable=True)
    voided_approved_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("staff_users.id"), nullable=True)

    items: Mapped[List["KOTItem"]] = relationship(
        "KOTItem", back_populates="kot", cascade="all, delete-orphan", lazy="raise"
    )


class KOTItem(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "kot_items"
    __table_args__ = (CheckConstraint("quantity > 0", name="ck_kot_items_quantity"),)

    kot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("kots.id"), nullable=False, index=True)
    order_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("order_items.id"), nullable=False, index=True)
    item_name: Mapped[str] = mapped_column(String(150), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default=KOTStatus.QUEUED.value)

    kot: Mapped["KOT"] = relationship("KOT", back_populates="items", lazy="raise")


class VoidRequest(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A floor request to void a KOT, waiting for a manager's remote decision."""
    __tablename__ = "void_requests"
    __table_args__ = (
        # At most one open request per ticket.
        Index("uq_void_requests_kot_pending", "kot_id", unique=True, postgresql_where=text("status = 'pending'")),
        Index("ix_void_requests_branch_status", "branch_id", "status"),
        enum_check("void_requests", "status", VoidRequestStatus),
    )

    branch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False)
    kot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("kots.id"), nullable=False, index=True)
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=VoidRequestStatus.PENDING.value)
    requested_by_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("staff_users.id"), nullable=False)
    reviewed_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("staff_users.id"), nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
