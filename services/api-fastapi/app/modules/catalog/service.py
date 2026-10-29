import hashlib
import uuid
from typing import List, Optional, Sequence

from sqlalchemy import func, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.auth import CurrentUser
from app.core.enums import Station, Topic
from app.core.errors import NotFound
from app.core.events import commit, queue_event
from app.core.lookups import get_or_404
from app.models.catalog import Category, ItemVariant, MenuItem, ModifierGroup, ModifierItem
from app.models.floor import Branch
from app.schemas.catalog import (
    CategoryCreate,
    CategoryUpdate,
    MenuItemCreate,
    MenuItemUpdate,
    ModifierGroupCreate,
)

ITEM_LOADERS = (
    selectinload(MenuItem.variants),
    selectinload(MenuItem.modifier_groups).selectinload(ModifierGroup.modifiers),
)


async def list_categories(db: AsyncSession, include_inactive: bool) -> Sequence[Category]:
    stmt = select(Category).order_by(Category.sort_order, Category.name)
    if not include_inactive:
        stmt = stmt.where(Category.is_active.is_(True))
    return (await db.scalars(stmt)).all()


async def create_category(db: AsyncSession, payload: CategoryCreate) -> Category:
    category = Category(**payload.model_dump())
    db.add(category)
    await commit(db)
    return category


async def update_category(db: AsyncSession, category_id: uuid.UUID, payload: CategoryUpdate) -> Category:
    category = await get_or_404(db, Category, category_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(category, field, value)
    await commit(db)
    return category


async def get_item(db: AsyncSession, item_id: uuid.UUID) -> MenuItem:
    return await get_or_404(db, MenuItem, item_id, *ITEM_LOADERS, populate_existing=True)


async def list_items(
    db: AsyncSession,
    category_id: Optional[uuid.UUID],
    station: Optional[Station],
    available_only: bool,
    include_inactive: bool,
) -> Sequence[MenuItem]:
    stmt = select(MenuItem).options(*ITEM_LOADERS).order_by(MenuItem.sort_order, MenuItem.name)
    if category_id is not None:
        stmt = stmt.where(MenuItem.category_id == category_id)
    if station is not None:
        stmt = stmt.where(MenuItem.station == station.value)
    if available_only:
        stmt = stmt.where(MenuItem.is_available_86.is_(True))
    if not include_inactive:
        stmt = stmt.where(MenuItem.is_active.is_(True))
    return (await db.scalars(stmt)).all()


async def _load_modifier_groups(db: AsyncSession, group_ids: List[uuid.UUID]) -> List[ModifierGroup]:
    if not group_ids:
        return []
    groups = (await db.scalars(select(ModifierGroup).where(ModifierGroup.id.in_(group_ids)))).all()
    missing = set(group_ids) - {g.id for g in groups}
    if missing:
        raise NotFound("Modifier group not found", details={"modifier_group_ids": sorted(map(str, missing))})
    return list(groups)


async def create_item(db: AsyncSession, payload: MenuItemCreate) -> MenuItem:
    category = await db.get(Category, payload.category_id)
    if category is None or not category.is_active:
        raise NotFound("Category not found")

    data = payload.model_dump(exclude={"variants", "modifier_group_ids"})
    data["station"] = payload.station.value
    item = MenuItem(**data)
    item.variants = [ItemVariant(**v.model_dump()) for v in payload.variants]
    item.modifier_groups = await _load_modifier_groups(db, payload.modifier_group_ids)
    db.add(item)
    await commit(db)
    return await get_item(db, item.id)


async def update_item(db: AsyncSession, item_id: uuid.UUID, payload: MenuItemUpdate) -> MenuItem:
    item = await get_item(db, item_id)
    changes = payload.model_dump(exclude_unset=True)
    group_ids = changes.pop("modifier_group_ids", None)
    if "category_id" in changes:
        category = await db.get(Category, changes["category_id"])
        if category is None or not category.is_active:
            raise NotFound("Category not found")
    for field, value in changes.items():
        setattr(item, field, value.value if isinstance(value, Station) else value)
    if group_ids is not None:
        item.modifier_groups = await _load_modifier_groups(db, group_ids)
    await commit(db)
    return await get_item(db, item.id)


async def _queue_86_event(db: AsyncSession, item_id: uuid.UUID, variant_id: Optional[uuid.UUID], available: bool) -> None:
    # The menu is global, so every active branch's POS terminals must hear about it.
    branch_ids = (await db.scalars(select(Branch.id).where(Branch.is_active.is_(True)))).all()
    for branch_id in branch_ids:
        queue_event(db, branch_id, Topic.CATALOG, "item.86_changed", item_id,
                    {"item_id": str(item_id), "variant_id": str(variant_id) if variant_id else None,
                     "is_available_86": available})


async def toggle_item_86(db: AsyncSession, item_id: uuid.UUID, available: bool) -> MenuItem:
    item = await get_item(db, item_id)
    item.is_available_86 = available
    await _queue_86_event(db, item.id, None, available)
    await commit(db)
    return await get_item(db, item.id)


async def toggle_variant_86(db: AsyncSession, variant_id: uuid.UUID, available: bool) -> MenuItem:
    variant = await get_or_404(db, ItemVariant, variant_id)
    variant.is_available_86 = available
    await _queue_86_event(db, variant.item_id, variant.id, available)
    await commit(db)
    return await get_item(db, variant.item_id)


async def list_modifier_groups(db: AsyncSession) -> Sequence[ModifierGroup]:
    stmt = select(ModifierGroup).options(selectinload(ModifierGroup.modifiers)).order_by(ModifierGroup.name)
    return (await db.scalars(stmt)).all()


async def create_modifier_group(db: AsyncSession, payload: ModifierGroupCreate) -> ModifierGroup:
    group = ModifierGroup(**payload.model_dump(exclude={"modifiers"}))
    group.modifiers = [ModifierItem(**m.model_dump()) for m in payload.modifiers]
    db.add(group)
    await commit(db)
    return await get_or_404(db, ModifierGroup, group.id, selectinload(ModifierGroup.modifiers), populate_existing=True)


async def menu_etag(db: AsyncSession) -> str:
    """Cheap fingerprint of the whole menu so POS terminals can skip unchanged reloads."""
    parts = union_all(
        select(func.max(Category.updated_at).label("ts"), func.count().label("n")),
        select(func.max(MenuItem.updated_at), func.count()),
        select(func.max(ItemVariant.updated_at), func.count()),
        select(func.max(ModifierGroup.updated_at), func.count()),
        select(func.max(ModifierItem.updated_at), func.count()),
    )
    sub = parts.subquery()
    rows = (await db.execute(select(sub.c.ts, sub.c.n))).all()
    return hashlib.sha256(repr(rows).encode()).hexdigest()[:32]


async def get_menu(db: AsyncSession, user: CurrentUser) -> List[dict]:
    categories = await list_categories(db, include_inactive=False)
    items = await list_items(db, None, None, available_only=False, include_inactive=False)
    by_category: dict = {}
    for item in items:
        by_category.setdefault(item.category_id, []).append(item)
    return [
        {**{c: getattr(cat, c) for c in ("id", "name", "description", "sort_order", "is_active", "created_at")},
         "items": by_category.get(cat.id, [])}
        for cat in categories
    ]
