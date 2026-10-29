import uuid
from typing import List, Optional

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import TableStatus
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_check


class Branch(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "branches"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)
    address: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata", server_default="Asia/Kolkata")
    gstin: Mapped[Optional[str]] = mapped_column(String(15), nullable=True)
    upi_vpa: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    upi_payee_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    floors: Mapped[List["Floor"]] = relationship(
        "Floor", back_populates="branch", cascade="save-update, merge", lazy="raise"
    )


class Floor(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "floors"

    branch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g., "Ground Floor", "Rooftop"
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    branch: Mapped["Branch"] = relationship("Branch", back_populates="floors", lazy="raise")
    tables: Mapped[List["DiningTable"]] = relationship(
        "DiningTable", back_populates="floor", cascade="save-update, merge", lazy="raise"
    )


class DiningTable(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "dining_tables"
    __table_args__ = (
        UniqueConstraint("floor_id", "table_number", name="uq_dining_tables_floor_number"),
        enum_check("dining_tables", "status", TableStatus),
    )

    floor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("floors.id"), nullable=False, index=True)
    table_number: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    capacity: Mapped[int] = mapped_column(Integer, default=4)
    status: Mapped[str] = mapped_column(String(30), default=TableStatus.VACANT.value)
    merged_into_table_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dining_tables.id", ondelete="SET NULL"), nullable=True, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    floor: Mapped["Floor"] = relationship("Floor", back_populates="tables", lazy="raise")
