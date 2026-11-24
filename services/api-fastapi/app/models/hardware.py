"""
The hardware shop module's tables (`hardware_*`). Plan: docs/retail-modules/02-hardware.md.

A *product* is one sellable thing at one size ("PVC Elbow 1/2 inch"); there are no variants. Stock is counted and priced in
the product's *base unit* (metre, piece); *alternate units* (coil = 90 m, box = 100 pcs) are other ways to sell or buy it.
Prices per base unit carry 4 decimals because a base unit can be small (a screw out of a box of 1,000). Every table carries its
own tenant column (organization or branch) for row-level security.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import HardwareMovementKind, HardwareUnit
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_check

PRICE = Numeric(12, 4)  # money per base unit
MONEY = Numeric(10, 2)
QUANTITY = Numeric(14, 3)  # in the product's base unit


class HardwareSettings(Base, TimestampMixin):
    """One row per organization, made with the defaults on first read."""
    __tablename__ = "hardware_settings"
    __table_args__ = (
        CheckConstraint("default_tax_rate >= 0 AND default_tax_rate <= 40", name="ck_hardware_settings_default_tax_rate"),
        CheckConstraint("estimate_valid_days >= 1 AND estimate_valid_days <= 365", name="ck_hardware_settings_estimate_days"),
        CheckConstraint("state_code IS NULL OR state_code ~ '^[0-9]{2}$'", name="ck_hardware_settings_state_code"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), primary_key=True)
    prices_include_tax: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    round_to_rupee: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    default_tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("18.00"), server_default="18.00",
                                                      nullable=False)
    estimate_valid_days: Mapped[int] = mapped_column(Integer, default=15, server_default="15", nullable=False)
    # The shop's GST state code, used when a branch has no GSTIN of its own.
    state_code: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)


class HardwareCategory(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "hardware_categories"
    __table_args__ = (Index("uq_hardware_categories_org_name", "organization_id", func.lower(text("name")), unique=True),)

    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)


class HardwareProduct(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One sellable thing at one size. Soft-deleted only (`is_active`), because sale history points at it."""
    __tablename__ = "hardware_products"
    __table_args__ = (
        Index("uq_hardware_products_org_sku", "organization_id", "sku", unique=True, postgresql_where=text("sku IS NOT NULL")),
        Index("uq_hardware_products_org_barcode", "organization_id", "barcode", unique=True,
              postgresql_where=text("barcode IS NOT NULL")),
        Index("ix_hardware_products_org_name", "organization_id", "name"),
        enum_check("hardware_products", "base_unit", HardwareUnit),
        CheckConstraint("tax_rate >= 0 AND tax_rate <= 40", name="ck_hardware_products_tax_rate"),
        CheckConstraint("hsn_code IS NULL OR hsn_code ~ '^[0-9]{4,8}$'", name="ck_hardware_products_hsn_code"),
        CheckConstraint("price >= 0 AND (wholesale_price IS NULL OR wholesale_price >= 0) "
                        "AND (min_price IS NULL OR min_price >= 0) AND (mrp IS NULL OR mrp >= 0)",
                        name="ck_hardware_products_prices"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)
    category_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("hardware_categories.id"), nullable=False,
                                                   index=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    brand: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    size_spec: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)  # "1/2 inch", "2.5 sq mm", "6A"
    sku: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    barcode: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # scanning it sells one base unit
    hsn_code: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    base_unit: Mapped[str] = mapped_column(String(10), nullable=False)
    allow_fraction: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    price: Mapped[Decimal] = mapped_column(PRICE, nullable=False)  # retail, per base unit
    wholesale_price: Mapped[Optional[Decimal]] = mapped_column(PRICE, nullable=True)  # null: wholesale buyers pay retail
    min_price: Mapped[Optional[Decimal]] = mapped_column(PRICE, nullable=True)  # selling below it needs a manager
    mrp: Mapped[Optional[Decimal]] = mapped_column(PRICE, nullable=True)  # tax included; a line may not exceed it
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)

    units: Mapped[List["HardwareProductUnit"]] = relationship(
        "HardwareProductUnit", back_populates="product", lazy="raise", cascade="all, delete-orphan",
        order_by="(HardwareProductUnit.factor, HardwareProductUnit.unit)",
    )


class HardwareProductUnit(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    Another unit a product is sold or bought in: `factor` base units make one of it. Its prices are per this unit; a null price
    is worked out from the product's base-unit price (docs section 3.2).
    """
    __tablename__ = "hardware_product_units"
    __table_args__ = (
        UniqueConstraint("product_id", "unit", name="uq_hardware_product_units_product_unit"),
        Index("uq_hardware_product_units_org_barcode", "organization_id", "barcode", unique=True,
              postgresql_where=text("barcode IS NOT NULL")),
        enum_check("hardware_product_units", "unit", HardwareUnit),
        CheckConstraint("factor > 0", name="ck_hardware_product_units_factor"),
        CheckConstraint("(price IS NULL OR price >= 0) AND (wholesale_price IS NULL OR wholesale_price >= 0) "
                        "AND (min_price IS NULL OR min_price >= 0)", name="ck_hardware_product_units_prices"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hardware_products.id", ondelete="CASCADE"), nullable=False, index=True)
    unit: Mapped[str] = mapped_column(String(10), nullable=False)
    factor: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    price: Mapped[Optional[Decimal]] = mapped_column(MONEY, nullable=True)
    wholesale_price: Mapped[Optional[Decimal]] = mapped_column(MONEY, nullable=True)
    min_price: Mapped[Optional[Decimal]] = mapped_column(MONEY, nullable=True)
    barcode: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # scanning it sells one of this unit

    product: Mapped[HardwareProduct] = relationship("HardwareProduct", back_populates="units", lazy="raise")


class HardwareStock(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Base units of a product in a branch. A missing row means none; the quantity may go below zero (the shelf is the truth)."""
    __tablename__ = "hardware_stock"
    __table_args__ = (
        UniqueConstraint("branch_id", "product_id", name="uq_hardware_stock_branch_product"),
        CheckConstraint("low_stock_level IS NULL OR low_stock_level >= 0", name="ck_hardware_stock_low_stock_level"),
        CheckConstraint("cost_price IS NULL OR cost_price >= 0", name="ck_hardware_stock_cost_price"),
    )

    branch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False, index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("hardware_products.id"), nullable=False, index=True)
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, default=Decimal("0"), server_default="0", nullable=False)
    low_stock_level: Mapped[Optional[Decimal]] = mapped_column(QUANTITY, nullable=True)  # at or below this the product is low
    cost_price: Mapped[Optional[Decimal]] = mapped_column(PRICE, nullable=True)  # per base unit, from the last delivery


class HardwarePurchase(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Goods received in one delivery. Its lines are the ledger rows that point at it."""
    __tablename__ = "hardware_purchases"
    __table_args__ = (CheckConstraint("total_cost >= 0", name="ck_hardware_purchases_total_cost"),)

    branch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False, index=True)
    supplier_name: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    invoice_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    received_on: Mapped[date] = mapped_column(Date, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    total_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), server_default="0", nullable=False)
    created_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("staff_users.id"), nullable=True)


class HardwareStockMovement(Base, UUIDPrimaryKeyMixin):
    """
    The stock ledger: one row per change, never edited, always in the base unit. A mistake is corrected by another row.
    A purchase row also remembers what was typed (`entered_*`: 2 coils at 3,000.00) so the delivery reads back as received.
    `sale_id` and `return_id` are added with the sales (H3) and returns (H7) tables.
    """
    __tablename__ = "hardware_stock_movements"
    __table_args__ = (
        enum_check("hardware_stock_movements", "kind", HardwareMovementKind),
        CheckConstraint("quantity <> 0", name="ck_hardware_stock_movements_quantity"),
        CheckConstraint("unit_cost IS NULL OR unit_cost >= 0", name="ck_hardware_stock_movements_unit_cost"),
        Index("ix_hardware_stock_movements_branch_product_created", "branch_id", "product_id", "created_at"),
    )

    branch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("hardware_products.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)  # base unit, signed: + in, - out
    balance_after: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    unit_cost: Mapped[Optional[Decimal]] = mapped_column(PRICE, nullable=True)  # per base unit
    entered_unit: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    entered_quantity: Mapped[Optional[Decimal]] = mapped_column(QUANTITY, nullable=True)
    entered_unit_cost: Mapped[Optional[Decimal]] = mapped_column(PRICE, nullable=True)  # per entered unit
    reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    purchase_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hardware_purchases.id"), nullable=True, index=True)
    created_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("staff_users.id"), nullable=True)
    # The moment the row is written (not the transaction's start), so the ledger's time order is the order stock changed in.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("clock_timestamp()"), nullable=False)
