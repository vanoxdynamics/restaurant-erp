import uuid
from typing import List, Optional
from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class KOT(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "kots"

    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False, index=True)
    kot_number: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    station: Mapped[str] = mapped_column(String(50), default="kitchen", index=True)  # kitchen, bar, grill, dessert
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)  # queued, preparing, ready, served, voided
    void_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    items: Mapped[List["KOTItem"]] = relationship("KOTItem", back_populates="kot", cascade="all, delete-orphan")


class KOTItem(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "kot_items"

    kot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("kots.id"), nullable=False, index=True)
    order_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("order_items.id"), nullable=False)
    item_name: Mapped[str] = mapped_column(String(150), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="queued")

    kot: Mapped["KOT"] = relationship("KOT", back_populates="items")
