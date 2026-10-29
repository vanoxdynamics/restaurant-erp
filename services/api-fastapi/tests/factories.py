"""Async builders for test data. Each commits so the API under test sees the rows."""

import uuid
from decimal import Decimal
from typing import Dict, Iterable, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import token_claims
from app.core.security import compute_pin_lookup, create_access_token, get_password_hash, get_pin_hash
from app.models import (
    Branch,
    Category,
    DiningTable,
    Floor,
    ItemVariant,
    MenuItem,
    ModifierGroup,
    ModifierItem,
    StaffUser,
)


async def make_branch(db: AsyncSession, *, code: str = "BLR1", name: str = "Indiranagar", **kw) -> Branch:
    branch = Branch(code=code, name=name, **kw)
    branch.floors = []
    db.add(branch)
    await db.commit()
    return branch


async def make_floor(db: AsyncSession, branch: Branch, *, name: str = "Ground") -> Floor:
    floor = Floor(branch_id=branch.id, name=name)
    floor.tables = []
    db.add(floor)
    await db.commit()
    return floor


async def make_table(db: AsyncSession, floor: Floor, *, number: str = "T1", capacity: int = 4) -> DiningTable:
    table = DiningTable(floor_id=floor.id, table_number=number, capacity=capacity, status="vacant")
    db.add(table)
    await db.commit()
    return table


async def make_staff(
    db: AsyncSession,
    branch: Optional[Branch],
    *,
    role: str = "waiter",
    pin: str = "1234",
    password: Optional[str] = "correct-horse-battery",
    username: Optional[str] = None,
    is_active: bool = True,
) -> StaffUser:
    user = StaffUser(
        branch_id=branch.id if branch else None,
        username=username or f"{role}_{uuid.uuid4().hex[:6]}",
        full_name=f"Test {role.title()}",
        role=role,
        hashed_pin=get_pin_hash(pin),
        pin_lookup=compute_pin_lookup(branch.id, pin) if branch else None,
        hashed_password=get_password_hash(password) if password else None,
        is_active=is_active,
    )
    db.add(user)
    await db.commit()
    return user


def auth_headers(user: StaffUser, auth_method: str = "pwd", **extra: str) -> Dict[str, str]:
    token = create_access_token(user.id, claims=token_claims(user, auth_method))
    return {"Authorization": f"Bearer {token}", **extra}


def idem(user: StaffUser, key: Optional[str] = None) -> Dict[str, str]:
    return auth_headers(user, **{"X-Idempotency-Key": key or uuid.uuid4().hex})


async def make_category(db: AsyncSession, *, name: str = "Mains") -> Category:
    category = Category(name=name)
    category.items = []
    db.add(category)
    await db.commit()
    return category


async def make_menu_item(
    db: AsyncSession,
    *,
    category: Optional[Category] = None,
    name: str = "Paneer Tikka",
    price: str = "250.00",
    tax_rate: str = "5.00",
    station: str = "kitchen",
    variants: Iterable[tuple] = (),
    modifier_groups: Iterable[ModifierGroup] = (),
    available: bool = True,
) -> MenuItem:
    category = category or await make_category(db)
    item = MenuItem(
        category_id=category.id,
        name=name,
        base_price=Decimal(price),
        tax_rate=Decimal(tax_rate),
        station=station,
        is_available_86=available,
    )
    item.variants = [ItemVariant(name=n, price=Decimal(p)) for n, p in variants]
    item.modifier_groups = list(modifier_groups)
    db.add(item)
    await db.commit()
    return item


async def make_modifier_group(
    db: AsyncSession, *, name: str = "Spice", min_selection: int = 0, max_selection: int = 1,
    modifiers: Iterable[tuple] = (("Mild", "0.00"), ("Extra Hot", "20.00")),
) -> ModifierGroup:
    group = ModifierGroup(name=name, min_selection=min_selection, max_selection=max_selection)
    group.modifiers = [ModifierItem(name=n, price=Decimal(p)) for n, p in modifiers]
    db.add(group)
    await db.commit()
    return group
