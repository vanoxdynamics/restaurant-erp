import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, get_current_user, require_roles
from app.core.database import get_db
from app.core.enums import Role, Station
from app.modules.catalog import service
from app.schemas.catalog import (
    CategoryCreate,
    CategoryRead,
    CategoryUpdate,
    MenuItemCreate,
    MenuItemRead,
    MenuItemUpdate,
    MenuRead,
    ModifierGroupCreate,
    ModifierGroupRead,
    Toggle86Request,
)

router = APIRouter(
    prefix="/catalog",
    tags=["Catalog & Menu Engineering"],
    dependencies=[Depends(get_current_user)],
)

managers = require_roles(Role.OWNER, Role.MANAGER)
toggle_86_roles = require_roles(Role.OWNER, Role.MANAGER, Role.CAPTAIN, Role.KITCHEN)


@router.get("/categories", response_model=List[CategoryRead])
async def list_categories(
    include_inactive: bool = False,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List menu categories."""
    return await service.list_categories(db, include_inactive and user.is_manager)


@router.post("/categories", response_model=CategoryRead, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(managers)])
async def create_category(payload: CategoryCreate, db: AsyncSession = Depends(get_db)):
    """Create a new menu category."""
    return await service.create_category(db, payload)


@router.patch("/categories/{category_id}", response_model=CategoryRead, dependencies=[Depends(managers)])
async def update_category(category_id: uuid.UUID, payload: CategoryUpdate, db: AsyncSession = Depends(get_db)):
    """Update or soft-delete (is_active=false) a category."""
    return await service.update_category(db, category_id, payload)


@router.get("/menu", response_model=MenuRead)
async def get_menu(
    response: Response,
    if_none_match: Optional[str] = Header(None),
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Full active menu (categories → items → variants/modifiers) for POS boot. Supports ETag caching."""
    etag = f'"{await service.menu_etag(db)}"'
    if if_none_match == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})
    response.headers["ETag"] = etag
    return MenuRead(categories=await service.get_menu(db, user))


@router.get("/items", response_model=List[MenuItemRead])
async def list_items(
    category_id: Optional[uuid.UUID] = None,
    station: Optional[Station] = None,
    available_only: bool = False,
    include_inactive: bool = False,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List menu items with variant and modifier availability."""
    return await service.list_items(db, category_id, station, available_only, include_inactive and user.is_manager)


@router.get("/items/{item_id}", response_model=MenuItemRead)
async def get_item(item_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Retrieve a single menu item."""
    return await service.get_item(db, item_id)


@router.post("/items", response_model=MenuItemRead, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(managers)])
async def create_item(payload: MenuItemCreate, db: AsyncSession = Depends(get_db)):
    """Add a new menu item (with variants and modifier groups) to the catalog."""
    return await service.create_item(db, payload)


@router.patch("/items/{item_id}", response_model=MenuItemRead, dependencies=[Depends(managers)])
async def update_item(item_id: uuid.UUID, payload: MenuItemUpdate, db: AsyncSession = Depends(get_db)):
    """Update a menu item. Existing orders keep their price snapshots."""
    return await service.update_item(db, item_id, payload)


@router.patch("/items/{item_id}/toggle-86", response_model=MenuItemRead, dependencies=[Depends(toggle_86_roles)])
async def toggle_item_86(item_id: uuid.UUID, payload: Toggle86Request, db: AsyncSession = Depends(get_db)):
    """Instantly mark item as 86 (out-of-stock) or re-enable across all POS terminals."""
    return await service.toggle_item_86(db, item_id, payload.is_available_86)


@router.patch("/variants/{variant_id}/toggle-86", response_model=MenuItemRead,
              dependencies=[Depends(toggle_86_roles)])
async def toggle_variant_86(variant_id: uuid.UUID, payload: Toggle86Request, db: AsyncSession = Depends(get_db)):
    """Mark a single variant as 86 or re-enable it."""
    return await service.toggle_variant_86(db, variant_id, payload.is_available_86)


@router.get("/modifier-groups", response_model=List[ModifierGroupRead])
async def list_modifier_groups(db: AsyncSession = Depends(get_db)):
    """List modifier groups and items."""
    return await service.list_modifier_groups(db)


@router.post("/modifier-groups", response_model=ModifierGroupRead, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(managers)])
async def create_modifier_group(payload: ModifierGroupCreate, db: AsyncSession = Depends(get_db)):
    """Create a modifier group with its modifier items."""
    return await service.create_modifier_group(db, payload)
