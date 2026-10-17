import uuid
from typing import List
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.schemas.catalog import (
    CategoryCreate,
    CategoryRead,
    MenuItemCreate,
    MenuItemRead,
    MenuItemUpdate,
    ModifierGroupCreate,
    ModifierGroupRead,
    Toggle86Request,
)

router = APIRouter(prefix="/catalog", tags=["Catalog & Menu Engineering"])


@router.get("/categories", response_model=List[CategoryRead])
async def list_categories(db: AsyncSession = Depends(get_db)):
    """List all menu categories."""
    return []


@router.post("/categories", response_model=CategoryRead, status_code=status.HTTP_201_CREATED)
async def create_category(payload: CategoryCreate, db: AsyncSession = Depends(get_db)):
    """Create a new menu category."""
    raise NotImplementedError("Feature implementation pending")


@router.get("/items", response_model=List[MenuItemRead])
async def list_items(category_id: uuid.UUID = None, db: AsyncSession = Depends(get_db)):
    """List menu items with variant and modifier availability."""
    return []


@router.post("/items", response_model=MenuItemRead, status_code=status.HTTP_201_CREATED)
async def create_item(payload: MenuItemCreate, db: AsyncSession = Depends(get_db)):
    """Add a new menu item to the catalog."""
    raise NotImplementedError("Feature implementation pending")


@router.patch("/items/{item_id}/toggle-86", response_model=MenuItemRead)
async def toggle_item_86(
    item_id: uuid.UUID,
    payload: Toggle86Request,
    db: AsyncSession = Depends(get_db),
):
    """Instantly mark item as 86 (out-of-stock) or re-enable across all POS terminals."""
    raise NotImplementedError("Feature implementation pending")


@router.get("/modifier-groups", response_model=List[ModifierGroupRead])
async def list_modifier_groups(db: AsyncSession = Depends(get_db)):
    """List modifier groups and items."""
    return []
