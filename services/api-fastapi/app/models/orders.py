import uuid
from datetime import date
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import OrderItemStatus, OrderStatus, OrderType, Station
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_check


class Order(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("branch_id", "order_number", name="uq_orders_branch_number"),
        enum_check("orders", "status", OrderStatus),
        enum_check("orders", "order_type", OrderType),
        CheckConstraint("guest_count >= 1", name="ck_orders_guest_count"),
        # A sale made on a device carries the id the device gave it; the same sale sent again, by anyone, at any time, finds
        # the first order instead of making a second one.
        Index("uq_orders_branch_client_sale", "branch_id", "client_sale_id", unique=True,
              postgresql_where=text("client_sale_id IS NOT NULL")),
    )

    branch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False, index=True)
    table_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("dining_tables.id"), nullable=True, index=True)
    waiter_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("staff_users.id"), nullable=True, index=True)
    opened_by_shift_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("shifts.id"), nullable=True)
    # Who the sale is to, when known: printed on the bill, and how a customer's visits and spend are counted.
    customer_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", name="fk_orders_customer_id_customers"), nullable=True, index=True)
    order_number: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    order_type: Mapped[str] = mapped_column(String(30), default=OrderType.DINE_IN.value)
    status: Mapped[str] = mapped_column(String(30), default=OrderStatus.ACTIVE.value, index=True)
    guest_count: Mapped[int] = mapped_column(Integer, default=1)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cancel_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    business_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    # Daily per-branch number called out when a counter order is ready (businesses with token numbers on).
    token_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # The device's own id for this sale (offline billing); unique per branch, see uq_orders_branch_client_sale.
    client_sale_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)  # Optimistic concurrency lock

    items: Mapped[List["OrderItem"]] = relationship(
        "OrderItem", back_populates="order", cascade="all, delete-orphan", lazy="raise",
        order_by="OrderItem.created_at",
    )

    __mapper_args__ = {"version_id_col": version}


class OrderItem(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "order_items"
    __table_args__ = (
        enum_check("order_items", "status", OrderItemStatus),
        CheckConstraint("quantity > 0", name="ck_order_items_quantity"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False, index=True)
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("menu_items.id"), nullable=False, index=True)
    variant_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("item_variants.id"), nullable=True)
    name_snapshot: Mapped[str] = mapped_column(String(150), nullable=False)
    variant_name_snapshot: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    station_snapshot: Mapped[str] = mapped_column(String(50), default=Station.KITCHEN.value, server_default=Station.KITCHEN.value)
    unit_price_snapshot: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    tax_rate_snapshot: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("5.00"))
    quantity: Mapped[Decimal] = mapped_column(Numeric(10, 3), default=Decimal("1"))
    unit_snapshot: Mapped[str] = mapped_column(String(10), default="pcs", server_default="pcs", nullable=False)
    hsn_snapshot: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    line_total: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"), server_default="0")
    status: Mapped[str] = mapped_column(String(30), default=OrderItemStatus.ORDERED.value)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    order: Mapped["Order"] = relationship("Order", back_populates="items", lazy="raise")
    modifiers: Mapped[List["OrderItemModifier"]] = relationship(
        "OrderItemModifier", back_populates="order_item", cascade="all, delete-orphan", lazy="raise"
    )


class OrderItemModifier(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "order_item_modifiers"

    order_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("order_items.id"), nullable=False, index=True)
    modifier_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("modifier_items.id"), nullable=False)
    name_snapshot: Mapped[str] = mapped_column(String(100), nullable=False)
    price_snapshot: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    order_item: Mapped["OrderItem"] = relationship("OrderItem", back_populates="modifiers", lazy="raise")
