import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, List, Optional

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.core.enums import ItemUnit, Station
from app.schemas.common import Money, OptionalHsn, TaxRate

ItemName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)]
Name100 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
# Printable codes only: barcodes and SKUs are typed by scanners and people, compared exactly.
Code = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64, pattern=r"^[!-~]+$")]


class ModifierItemBase(BaseModel):
    name: Name100
    price: Money = Decimal("0.00")
    is_available: bool = True


class ModifierItemCreate(ModifierItemBase):
    pass


class ModifierItemRead(ModifierItemBase):
    id: uuid.UUID
    group_id: uuid.UUID
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ModifierGroupBase(BaseModel):
    name: Name100
    min_selection: int = Field(default=0, ge=0, le=20)
    max_selection: int = Field(default=1, ge=1, le=20)

    @model_validator(mode="after")
    def check_selection_range(self) -> "ModifierGroupBase":
        if self.max_selection < self.min_selection:
            raise ValueError("max_selection must be >= min_selection")
        return self


class ModifierGroupCreate(ModifierGroupBase):
    modifiers: List[ModifierItemCreate] = Field(default_factory=list, max_length=50)


class ModifierGroupRead(ModifierGroupBase):
    id: uuid.UUID
    modifiers: List[ModifierItemRead] = []
    model_config = ConfigDict(from_attributes=True)


class ItemVariantBase(BaseModel):
    name: Name100
    price: Money
    is_available_86: bool = True


class ItemVariantCreate(ItemVariantBase):
    pass


class ItemVariantRead(ItemVariantBase):
    id: uuid.UUID
    item_id: uuid.UUID
    is_active: bool = True
    model_config = ConfigDict(from_attributes=True)


class MenuItemBase(BaseModel):
    name: ItemName
    description: Optional[str] = Field(default=None, max_length=2000)
    base_price: Money
    tax_rate: TaxRate = Decimal("5.00")
    station: Station = Station.KITCHEN
    is_active: bool = True
    is_available_86: bool = True
    image_url: Optional[str] = Field(default=None, max_length=255, pattern=r"^https?://")
    sort_order: int = 0
    barcode: Optional[Code] = None
    sku: Optional[Code] = None
    hsn_code: OptionalHsn = None
    unit: ItemUnit = ItemUnit.PIECE
    sold_by_weight: bool = False


class MenuItemCreate(MenuItemBase):
    category_id: uuid.UUID
    variants: List[ItemVariantCreate] = Field(default_factory=list, max_length=20)
    modifier_group_ids: List[uuid.UUID] = Field(default_factory=list, max_length=20)


class MenuItemUpdate(BaseModel):
    name: Optional[ItemName] = None
    description: Optional[str] = Field(default=None, max_length=2000)
    base_price: Optional[Money] = None
    tax_rate: Optional[TaxRate] = None
    station: Optional[Station] = None
    category_id: Optional[uuid.UUID] = None
    is_active: Optional[bool] = None
    is_available_86: Optional[bool] = None
    image_url: Optional[str] = Field(default=None, max_length=255, pattern=r"^https?://")
    sort_order: Optional[int] = None
    modifier_group_ids: Optional[List[uuid.UUID]] = Field(default=None, max_length=20)
    barcode: Optional[Code] = None
    sku: Optional[Code] = None
    hsn_code: OptionalHsn = None
    unit: Optional[ItemUnit] = None
    sold_by_weight: Optional[bool] = None

    # Fields left out are not changed. These columns can never be empty, so an explicit null (for example
    # `"base_price": null`) is refused here instead of reaching the database as a 500.
    _NOT_NULL = ("name", "base_price", "tax_rate", "station", "category_id", "is_active", "is_available_86", "sort_order",
                 "unit", "sold_by_weight")

    @model_validator(mode="after")
    def _no_null_required_fields(self) -> "MenuItemUpdate":
        empty = [f for f in self._NOT_NULL if f in self.model_fields_set and getattr(self, f) is None]
        if empty:
            raise ValueError(f"{', '.join(empty)} cannot be empty")
        return self


class Toggle86Request(BaseModel):
    is_available_86: bool


class MenuItemRead(MenuItemBase):
    id: uuid.UUID
    category_id: uuid.UUID
    variants: List[ItemVariantRead] = []
    modifier_groups: List[ModifierGroupRead] = []
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class CategoryBase(BaseModel):
    name: Name100
    description: Optional[str] = Field(default=None, max_length=2000)
    sort_order: int = 0
    is_active: bool = True


class CategoryCreate(CategoryBase):
    pass


class CategoryUpdate(BaseModel):
    name: Optional[Name100] = None
    description: Optional[str] = Field(default=None, max_length=2000)
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None


class CategoryRead(CategoryBase):
    id: uuid.UUID
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class MenuCategoryRead(CategoryRead):
    items: List[MenuItemRead] = []


class MenuRead(BaseModel):
    categories: List[MenuCategoryRead]

