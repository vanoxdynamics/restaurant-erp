"""
The dress shop module's tables (`apparel_*`). Plan: docs/retail-modules/01-apparel.md.

A *product* is a style ("Men's Oxford Shirt") and is never sold directly; a *variant* is one size and colour of it, and is what
is scanned, sold and counted. Every table carries its own tenant column (organization or branch) for row-level security.
"""

import uuid
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import BigInteger, Boolean, CheckConstraint, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import ApparelTaxMode
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_check


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
