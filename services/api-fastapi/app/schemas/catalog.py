import uuid
from decimal import Decimal
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class ModifierItemBase(BaseModel):
    name: str
    price: Decimal = Decimal("0.00")
    is_available: bool = True


class ModifierItemCreate(ModifierItemBase):
    pass


class ModifierItemRead(ModifierItemBase):
    id: uuid.UUID
    group_id: uuid.UUID
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ModifierGroupBase(BaseModel):
    name: str
    min_selection: int = 0
    max_selection: int = 1


class ModifierGroupCreate(ModifierGroupBase):
    pass


class ModifierGroupRead(ModifierGroupBase):
    id: uuid.UUID
    modifiers: List[ModifierItemRead] = []
    model_config = ConfigDict(from_attributes=True)


class ItemVariantBase(BaseModel):
    name: str
    price: Decimal
    is_available_86: bool = True


class ItemVariantCreate(ItemVariantBase):
    pass


class ItemVariantRead(ItemVariantBase):
    id: uuid.UUID
    item_id: uuid.UUID
    model_config = ConfigDict(from_attributes=True)


class MenuItemBase(BaseModel):
    name: str
    description: Optional[str] = None
    base_price: Decimal
    tax_rate: Decimal = Decimal("5.00")
    is_active: bool = True
    is_available_86: bool = True
    image_url: Optional[str] = None


class MenuItemCreate(MenuItemBase):
    category_id: uuid.UUID


class MenuItemUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    base_price: Optional[Decimal] = None
    tax_rate: Optional[Decimal] = None
    is_active: Optional[bool] = None
    is_available_86: Optional[bool] = None
    image_url: Optional[str] = None


class Toggle86Request(BaseModel):
    is_available_86: bool


class MenuItemRead(MenuItemBase):
    id: uuid.UUID
    category_id: uuid.UUID
    variants: List[ItemVariantRead] = []
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class CategoryBase(BaseModel):
    name: str
    description: Optional[str] = None
    sort_order: int = 0
    is_active: bool = True


class CategoryCreate(CategoryBase):
    pass


class CategoryRead(CategoryBase):
    id: uuid.UUID
    items: List[MenuItemRead] = []
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)
