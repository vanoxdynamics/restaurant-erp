import uuid
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import Boolean, CheckConstraint, Column, ForeignKey, Integer, Numeric, String, Table, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import Station
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_check

menu_item_modifier_groups = Table(
    "menu_item_modifier_groups",
    Base.metadata,
    Column("menu_item_id", UUID(as_uuid=True), ForeignKey("menu_items.id", ondelete="CASCADE"), primary_key=True),
    Column("modifier_group_id", UUID(as_uuid=True), ForeignKey("modifier_groups.id", ondelete="CASCADE"), primary_key=True),
    Column("sort_order", Integer, nullable=False, server_default="0"),
)


class Category(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "categories"

    name: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Catalog rows are referenced by order history: soft-delete via is_active, never cascade deletes.
    items: Mapped[List["MenuItem"]] = relationship(
        "MenuItem", back_populates="category", cascade="save-update, merge", lazy="raise"
    )


class MenuItem(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "menu_items"
    __table_args__ = (
        CheckConstraint("base_price >= 0", name="ck_menu_items_base_price"),
        CheckConstraint("tax_rate >= 0 AND tax_rate <= 28", name="ck_menu_items_tax_rate"),
        enum_check("menu_items", "station", Station),
    )

    category_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("categories.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(150), index=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    base_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("5.00"))  # Default 5% GST
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_available_86: Mapped[bool] = mapped_column(Boolean, default=True)  # "86" toggle for out-of-stock items
    image_url: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    station: Mapped[str] = mapped_column(String(50), default=Station.KITCHEN.value, server_default=Station.KITCHEN.value)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    category: Mapped["Category"] = relationship("Category", back_populates="items", lazy="raise")
    variants: Mapped[List["ItemVariant"]] = relationship(
        "ItemVariant", back_populates="item", cascade="save-update, merge", lazy="raise"
    )
    modifier_groups: Mapped[List["ModifierGroup"]] = relationship(
        "ModifierGroup", secondary=menu_item_modifier_groups, lazy="raise"
    )


class ItemVariant(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "item_variants"
    __table_args__ = (CheckConstraint("price >= 0", name="ck_item_variants_price"),)

    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("menu_items.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g., "Half", "Full", "Large"
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    is_available_86: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    item: Mapped["MenuItem"] = relationship("MenuItem", back_populates="variants", lazy="raise")


class ModifierGroup(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "modifier_groups"
    __table_args__ = (
        CheckConstraint(
            "min_selection >= 0 AND max_selection >= min_selection",
            name="ck_modifier_groups_selection",
        ),
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    min_selection: Mapped[int] = mapped_column(Integer, default=0)
    max_selection: Mapped[int] = mapped_column(Integer, default=1)

    modifiers: Mapped[List["ModifierItem"]] = relationship(
        "ModifierItem", back_populates="group", cascade="all, delete-orphan", lazy="raise"
    )


class ModifierItem(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "modifier_items"
    __table_args__ = (CheckConstraint("price >= 0", name="ck_modifier_items_price"),)

    group_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("modifier_groups.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)

    group: Mapped["ModifierGroup"] = relationship("ModifierGroup", back_populates="modifiers", lazy="raise")
