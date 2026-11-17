"""
The dress shop module's tables (`apparel_*`). Plan: docs/retail-modules/01-apparel.md.

A *product* is a style ("Men's Oxford Shirt") and is never sold directly; a *variant* is one size and colour of it, and is what
is scanned, sold and counted. Every table carries its own tenant column (organization or branch) for row-level security.
"""

import uuid
from decimal import Decimal
from datetime import date, datetime
from typing import List, Optional

from sqlalchemy import (
    BigInteger,
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

from app.core.enums import ApparelMovementKind, ApparelPaymentMode, ApparelTaxMode, BillStatus
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_check
from app.models.payment_columns import PaymentColumns


class ApparelSettings(Base, TimestampMixin):
    """One row per organization, made with the defaults on first read. The GST values are settings: the government changes them."""
    __tablename__ = "apparel_settings"
    __table_args__ = (
        CheckConstraint("low_rate >= 0 AND low_rate <= 40 AND high_rate >= 0 AND high_rate <= 40", name="ck_apparel_settings_rates"),
        CheckConstraint("slab_price >= 0", name="ck_apparel_settings_slab_price"),
        CheckConstraint("return_days >= 0", name="ck_apparel_settings_return_days"),
        CheckConstraint("next_barcode >= 1 AND next_barcode < 10000000000", name="ck_apparel_settings_next_barcode"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), primary_key=True)
    prices_include_tax: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    round_to_rupee: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    low_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("5.00"), server_default="5.00", nullable=False)
    slab_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("2500.00"), server_default="2500.00", nullable=False)
    high_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("18.00"), server_default="18.00", nullable=False)
    # Returns after this many days need a manager's PIN; 0 means no limit.
    return_days: Mapped[int] = mapped_column(Integer, default=7, server_default="7", nullable=False)
    # The next in-store barcode: "29" + this number in 10 digits + the EAN-13 check digit.
    next_barcode: Mapped[int] = mapped_column(BigInteger, default=1, server_default="1", nullable=False)


class ApparelCategory(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "apparel_categories"
    __table_args__ = (Index("uq_apparel_categories_org_name", "organization_id", func.lower(text("name")), unique=True),)

    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)


class ApparelProduct(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A style. Soft-deleted only (`is_active`), because sale history points at it."""
    __tablename__ = "apparel_products"
    __table_args__ = (
        Index("uq_apparel_products_org_style", "organization_id", "style_code", unique=True,
              postgresql_where=text("style_code IS NOT NULL")),
        Index("ix_apparel_products_org_name", "organization_id", "name"),
        enum_check("apparel_products", "tax_mode", ApparelTaxMode),
        CheckConstraint("tax_mode <> 'fixed' OR tax_rate IS NOT NULL", name="ck_apparel_products_fixed_rate"),
        CheckConstraint("tax_rate IS NULL OR (tax_rate >= 0 AND tax_rate <= 40)", name="ck_apparel_products_tax_rate"),
        CheckConstraint("hsn_code IS NULL OR hsn_code ~ '^[0-9]{4,8}$'", name="ck_apparel_products_hsn_code"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)
    category_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("apparel_categories.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    brand: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    style_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    hsn_code: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    # `slab`: the rate follows the price of the piece (settings); `fixed`: belts, bags and the like at their own rate.
    tax_mode: Mapped[str] = mapped_column(String(10), default=ApparelTaxMode.SLAB.value, server_default="slab", nullable=False)
    tax_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)

    variants: Mapped[List["ApparelVariant"]] = relationship(
        "ApparelVariant", back_populates="product", lazy="raise", cascade="save-update, merge",
        order_by="(ApparelVariant.sort_order, ApparelVariant.created_at)",
    )


class ApparelVariant(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One size and colour of a product: what is scanned, sold and counted."""
    __tablename__ = "apparel_variants"
    __table_args__ = (
        UniqueConstraint("product_id", "size", "colour", name="uq_apparel_variants_product_size_colour"),
        Index("uq_apparel_variants_org_sku", "organization_id", "sku", unique=True, postgresql_where=text("sku IS NOT NULL")),
        Index("uq_apparel_variants_org_barcode", "organization_id", "barcode", unique=True,
              postgresql_where=text("barcode IS NOT NULL")),
        CheckConstraint("price >= 0 AND (mrp IS NULL OR price <= mrp)", name="ck_apparel_variants_price"),
        CheckConstraint("size <> '' AND colour <> ''", name="ck_apparel_variants_size_colour"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("apparel_products.id"), nullable=False, index=True)
    size: Mapped[str] = mapped_column(String(20), nullable=False)  # "M", "32", "Free"
    colour: Mapped[str] = mapped_column(String(40), nullable=False)  # "Blue"; "Default" when the style has one colour
    sku: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    barcode: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    mrp: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)  # the printed price
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)  # the selling price
    # Keeps sizes in the order the owner typed them (S, M, L, XL).
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)

    product: Mapped["ApparelProduct"] = relationship("ApparelProduct", back_populates="variants", lazy="raise")


class ApparelStock(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Pieces of a variant in a branch. A missing row means none; the quantity may go below zero (the shelf is the truth)."""
    __tablename__ = "apparel_stock"
    __table_args__ = (
        UniqueConstraint("branch_id", "variant_id", name="uq_apparel_stock_branch_variant"),
        CheckConstraint("low_stock_level IS NULL OR low_stock_level >= 0", name="ck_apparel_stock_low_stock_level"),
        CheckConstraint("cost_price IS NULL OR cost_price >= 0", name="ck_apparel_stock_cost_price"),
    )

    branch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False, index=True)
    variant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("apparel_variants.id"), nullable=False, index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    low_stock_level: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # at or below this the variant is low
    cost_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)  # per piece, from the last delivery


class ApparelPurchase(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Pieces received in one delivery. Its lines are the ledger rows that point at it."""
    __tablename__ = "apparel_purchases"
    __table_args__ = (CheckConstraint("total_cost >= 0", name="ck_apparel_purchases_total_cost"),)

    branch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False, index=True)
    supplier_name: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    invoice_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    received_on: Mapped[date] = mapped_column(Date, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    total_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), server_default="0", nullable=False)
    created_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("staff_users.id"), nullable=True)


class ApparelStockMovement(Base, UUIDPrimaryKeyMixin):
    """The stock ledger: one row per change, never edited. A mistake is corrected by another row."""
    __tablename__ = "apparel_stock_movements"
    __table_args__ = (
        enum_check("apparel_stock_movements", "kind", ApparelMovementKind),
        CheckConstraint("quantity <> 0", name="ck_apparel_stock_movements_quantity"),
        CheckConstraint("unit_cost IS NULL OR unit_cost >= 0", name="ck_apparel_stock_movements_unit_cost"),
        Index("ix_apparel_stock_movements_branch_variant_created", "branch_id", "variant_id", "created_at"),
    )

    branch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False)
    variant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("apparel_variants.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)  # signed: + in, - out
    balance_after: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    purchase_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("apparel_purchases.id"), nullable=True, index=True)
    sale_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("apparel_sales.id"), nullable=True, index=True)
    created_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("staff_users.id"), nullable=True)
    # The moment the row is written (not the transaction's start), so the ledger's time order is the order stock changed in, even
    # when a sale waited on another's lock.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("clock_timestamp()"), nullable=False)


class ApparelSale(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A dress shop invoice: made complete, with its number, in one request. Its pieces leave stock at once."""
    __tablename__ = "apparel_sales"
    __table_args__ = (
        UniqueConstraint("branch_id", "sale_number", name="uq_apparel_sales_branch_number"),
        Index("uq_apparel_sales_branch_client_sale", "branch_id", "client_sale_id", unique=True,
              postgresql_where=text("client_sale_id IS NOT NULL")),
        enum_check("apparel_sales", "status", BillStatus),
        CheckConstraint("grand_total >= 0 AND paid_amount >= 0", name="ck_apparel_sales_amounts"),
    )

    branch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False, index=True)
    sale_number: Mapped[str] = mapped_column(String(50), nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    customer_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("customers.id"), nullable=True, index=True)
    salesperson_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("staff_users.id"), nullable=True)
    created_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("staff_users.id"), nullable=True)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"), nullable=False)
    discount_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    discount_approved_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("staff_users.id"), nullable=True)
    taxable_value: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    tax_cgst: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    tax_sgst: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    tax_igst: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"), nullable=False)
    round_off: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"), nullable=False)
    grand_total: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    paid_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default=BillStatus.UNPAID.value, nullable=False)
    settled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    voided_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("staff_users.id"), nullable=True)
    void_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    client_sale_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    lines: Mapped[List["ApparelSaleLine"]] = relationship(
        "ApparelSaleLine", back_populates="sale", cascade="all, delete-orphan", order_by="ApparelSaleLine.created_at", lazy="raise")
    payments: Mapped[List["ApparelPayment"]] = relationship(
        "ApparelPayment", back_populates="sale", order_by="ApparelPayment.created_at", lazy="raise")

    @property
    def balance_due(self) -> Decimal:
        return max(self.grand_total - self.paid_amount, Decimal("0.00"))


class ApparelSaleLine(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "apparel_sale_lines"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_apparel_sale_lines_quantity"),
        CheckConstraint("returned_quantity >= 0 AND returned_quantity <= quantity", name="ck_apparel_sale_lines_returned"),
    )

    branch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False, index=True)
    sale_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("apparel_sales.id", ondelete="CASCADE"), nullable=False, index=True)
    variant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("apparel_variants.id"), nullable=False, index=True)
    name_snapshot: Mapped[str] = mapped_column(String(150), nullable=False)
    size_snapshot: Mapped[str] = mapped_column(String(20), nullable=False)
    colour_snapshot: Mapped[str] = mapped_column(String(40), nullable=False)
    hsn_snapshot: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    barcode_snapshot: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    mrp_snapshot: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    line_discount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"), nullable=False)
    discount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"), nullable=False)  # incl. bill discount share
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    taxable_value: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    cgst: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    sgst: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    igst: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"), nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    returned_quantity: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)

    sale: Mapped[ApparelSale] = relationship("ApparelSale", back_populates="lines", lazy="raise")


class ApparelPayment(Base, UUIDPrimaryKeyMixin, TimestampMixin, PaymentColumns):
    __tablename__ = "apparel_payments"
    __table_args__ = (
        enum_check("apparel_payments", "payment_mode", ApparelPaymentMode),
        CheckConstraint("amount > 0", name="ck_apparel_payments_amount"),
        Index("uq_apparel_payments_sale_idempotency", "sale_id", "idempotency_key", unique=True,
              postgresql_where=text("idempotency_key IS NOT NULL")),
    )

    branch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False, index=True)
    sale_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("apparel_sales.id"), nullable=False, index=True)

    sale: Mapped[ApparelSale] = relationship("ApparelSale", back_populates="payments", lazy="raise")
