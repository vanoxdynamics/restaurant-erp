import uuid
from typing import List, Optional
from decimal import Decimal
from sqlalchemy import Boolean, ForeignKey, Numeric, String, Text, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Category(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "categories"

    name: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    items: Mapped[List["MenuItem"]] = relationship("MenuItem", back_populates="category", cascade="all, delete-orphan")


class MenuItem(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "menu_items"

    category_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("categories.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(150), index=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    base_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("5.00"))  # Default 5% GST
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_available_86: Mapped[bool] = mapped_column(Boolean, default=True)  # "86" toggle for out-of-stock items
    image_url: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    category: Mapped["Category"] = relationship("Category", back_populates="items")
    variants: Mapped[List["ItemVariant"]] = relationship("ItemVariant", back_populates="item", cascade="all, delete-orphan")


class ItemVariant(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "item_variants"

    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("menu_items.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g., "Half", "Full", "Large"
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    is_available_86: Mapped[bool] = mapped_column(Boolean, default=True)

    item: Mapped["MenuItem"] = relationship("MenuItem", back_populates="variants")


class ModifierGroup(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "modifier_groups"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    min_selection: Mapped[int] = mapped_column(Integer, default=0)
    max_selection: Mapped[int] = mapped_column(Integer, default=1)

    modifiers: Mapped[List["ModifierItem"]] = relationship("ModifierItem", back_populates="group", cascade="all, delete-orphan")


class ModifierItem(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "modifier_items"

    group_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("modifier_groups.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)

    group: Mapped["ModifierGroup"] = relationship("ModifierGroup", back_populates="modifiers")
