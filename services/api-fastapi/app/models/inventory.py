import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import StockMovementKind
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_check


class StockLevel(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    How much of an item a branch has. A row exists only for items the branch tracks; items without one sell untracked. The
    quantity is in the item's own unit (pieces, kg, ...) and may go below zero: a sale is never refused because the count on
    record is behind the shelf.
    """
    __tablename__ = "stock_levels"
    __table_args__ = (
        UniqueConstraint("branch_id", "item_id", name="uq_stock_levels_branch_item"),
        CheckConstraint("low_stock_level IS NULL OR low_stock_level >= 0", name="ck_stock_levels_low_stock_level"),
        CheckConstraint("cost_price IS NULL OR cost_price >= 0", name="ck_stock_levels_cost_price"),
    )

    branch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False, index=True)
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("menu_items.id"), nullable=False, index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=Decimal("0"), server_default="0", nullable=False)
    # At or below this the item is "low". NULL: no alert.
    low_stock_level: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 3), nullable=True)
    cost_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)  # per unit, from the last purchase
    # Switched off: the quantity is kept but sales no longer change it.
    is_tracked: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)


class StockPurchase(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Stock received from a supplier in one delivery. Its lines are the ledger rows that point at it."""
    __tablename__ = "stock_purchases"
    __table_args__ = (CheckConstraint("total_cost >= 0", name="ck_stock_purchases_total_cost"),)

    branch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False, index=True)
    supplier_name: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    invoice_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    received_on: Mapped[date] = mapped_column(Date, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    total_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), server_default="0", nullable=False)
    created_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("staff_users.id"), nullable=True)


class StockMovement(Base, UUIDPrimaryKeyMixin):
    """The stock ledger: one row per change, never edited. A mistake is corrected by another row."""
    __tablename__ = "stock_movements"
    __table_args__ = (
        enum_check("stock_movements", "kind", StockMovementKind),
        CheckConstraint("quantity <> 0", name="ck_stock_movements_quantity"),
        CheckConstraint("unit_cost IS NULL OR unit_cost >= 0", name="ck_stock_movements_unit_cost"),
        Index("ix_stock_movements_branch_item_created", "branch_id", "item_id", "created_at"),
    )

    branch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False)
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("menu_items.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)  # signed: + in, - out
    balance_after: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    unit_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    purchase_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stock_purchases.id"), nullable=True, index=True)
    order_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("orders.id"), nullable=True, index=True)
    created_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("staff_users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
