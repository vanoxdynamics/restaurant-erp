"""
The hardware shop catalogue: settings, categories, products with their alternate units, the scanner lookup and the counter
search. Plan: docs/retail-modules/02-hardware.md sections 2 and 4.

A barcode identifies one product in one unit, so it is unique across both products (the base unit) and alternate-unit rows.
Each table has its own unique index; the check across the two tables runs under a per-organization advisory lock, so two
saves cannot take the same code at once.
"""

import uuid
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import and_, false, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.auth import CurrentUser
from app.core.enums import HARDWARE_MEASURED_UNITS, HardwarePriceTier, HardwareUnit, Topic
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.events import commit, queue_event
from app.core.pagination import PageParams, paginate
from app.models.floor import Branch
from app.models.hardware import HardwareCategory, HardwareProduct, HardwareProductUnit, HardwareSettings
from app.modules.hardware import pricing
from app.schemas.hardware import (
    HardwareCategoryCreate,
    HardwareCategoryUpdate,
    HardwareSettingsUpdate,
    HardwareProductCreate,
    HardwareProductUpdate,
    HardwareUnitIn,
    HardwareUnitsReplace,
)

SEARCH_LIMIT = 50

# Unique indexes -> the 409 a person can act on.
_CONFLICTS = {
    "uq_hardware_products_org_barcode": ("Another product already has this barcode", "duplicate_barcode"),
    "uq_hardware_product_units_org_barcode": ("Another unit already has this barcode", "duplicate_barcode"),
    "uq_hardware_products_org_sku": ("Another product already has this SKU", "duplicate_sku"),
    "uq_hardware_product_units_product_unit": ("This product already has that unit", "duplicate_unit"),
    "uq_hardware_categories_org_name": ("A category with this name already exists", "duplicate_category"),
}
# Fields a person may not empty on a product.
_REQUIRED = ("category_id", "name", "tax_rate", "base_unit", "allow_fraction", "price", "is_active")


async def flush_or_conflict(db: AsyncSession) -> None:
    """Flush, turning a clash on one of the module's unique indexes into a readable 409."""
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        text = str(exc.orig)
        for index, (message, code) in _CONFLICTS.items():
            if index in text:
                raise Conflict(message, code=code) from exc
        raise


async def announce(db: AsyncSession, org_id: uuid.UUID, name: str, payload: Optional[Dict[str, Any]] = None) -> None:
    """Tell every branch's counters that the catalogue changed (they refetch what they show)."""
    branch_ids = (await db.scalars(select(Branch.id).where(Branch.organization_id == org_id, Branch.is_active.is_(True)))).all()
    for branch_id in branch_ids:
        queue_event(db, branch_id, Topic.CATALOG, name, None, payload)


def default_fraction(unit: str) -> bool:
    """Cut lengths and loose goods may be sold in fractions; counted things may not."""
    return unit in HARDWARE_MEASURED_UNITS


# --- settings ----------------------------------------------------------------------------------------------------------------


async def load_settings(db: AsyncSession, org_id: uuid.UUID, *, for_update: bool = False) -> HardwareSettings:
    """The organization's settings row, made with the defaults the first time it is asked for."""
    await db.execute(pg_insert(HardwareSettings).values(organization_id=org_id)
                     .on_conflict_do_nothing(index_elements=[HardwareSettings.organization_id]))
    stmt = select(HardwareSettings).where(HardwareSettings.organization_id == org_id).execution_options(populate_existing=True)
    if for_update:
        stmt = stmt.with_for_update()
    settings = await db.scalar(stmt)
    assert settings is not None
    return settings


async def read_settings(db: AsyncSession, user: CurrentUser) -> HardwareSettings:
    settings = await load_settings(db, user.organization_id)
    await commit(db)
    return settings


async def update_settings(db: AsyncSession, user: CurrentUser, payload: HardwareSettingsUpdate) -> HardwareSettings:
    settings = await load_settings(db, user.organization_id, for_update=True)
    for field, value in payload.model_dump(exclude_unset=True).items():
        if value is None and field != "state_code":
            raise ValidationFailed(f"{field} cannot be empty", code="invalid_setting")
        setattr(settings, field, value)
    await commit(db)
    return settings


# --- categories --------------------------------------------------------------------------------------------------------------


async def list_categories(db: AsyncSession, user: CurrentUser, include_inactive: bool) -> List[HardwareCategory]:
    stmt = select(HardwareCategory).where(HardwareCategory.organization_id == user.organization_id)
    if not include_inactive:
        stmt = stmt.where(HardwareCategory.is_active.is_(True))
    return list((await db.scalars(stmt.order_by(HardwareCategory.sort_order, HardwareCategory.name))).all())


async def _category(db: AsyncSession, org_id: uuid.UUID, category_id: uuid.UUID, *, active: bool = True) -> HardwareCategory:
    category = await db.scalar(select(HardwareCategory).where(HardwareCategory.id == category_id,
                                                              HardwareCategory.organization_id == org_id))
    if category is None or (active and not category.is_active):
        raise NotFound("Category not found", code="category_not_found")
    return category


async def create_category(db: AsyncSession, user: CurrentUser, payload: HardwareCategoryCreate) -> HardwareCategory:
    category = HardwareCategory(organization_id=user.organization_id, name=payload.name, sort_order=payload.sort_order,
                                is_active=True)
    db.add(category)
    await flush_or_conflict(db)
    await commit(db)
    return category


async def update_category(db: AsyncSession, user: CurrentUser, category_id: uuid.UUID,
                          payload: HardwareCategoryUpdate) -> HardwareCategory:
    category = await _category(db, user.organization_id, category_id, active=False)
    for field, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(category, field, value)
    await flush_or_conflict(db)
    await commit(db)
    return category


# --- barcodes across products and units --------------------------------------------------------------------------------------


async def lock_codes(db: AsyncSession, org_id: uuid.UUID) -> None:
    """Serialise barcode checks within an organization until the transaction ends."""
    await db.execute(select(func.pg_advisory_xact_lock(func.hashtext(f"hardware_codes:{org_id}"))))


async def check_codes(
    db: AsyncSession, org_id: uuid.UUID, codes: Iterable[Optional[str]], *, skip_product: Optional[uuid.UUID] = None,
    skip_units_of: Optional[uuid.UUID] = None,
) -> None:
    """
    Refuse a barcode that is already on a product or a unit row. `skip_product` leaves out that product's own barcode and
    `skip_units_of` that product's unit rows (the rows being rewritten). Call `lock_codes` first.
    """
    wanted = [c for c in codes if c]
    if len(wanted) != len(set(wanted)):
        dupes = sorted({c for c in wanted if wanted.count(c) > 1})
        raise Conflict("The same barcode is given twice", code="duplicate_barcode", details={"barcodes": dupes})
    if not wanted:
        return
    on_products = select(HardwareProduct.barcode).where(HardwareProduct.organization_id == org_id,
                                                        HardwareProduct.barcode.in_(wanted))
    if skip_product is not None:
        on_products = on_products.where(HardwareProduct.id != skip_product)
    on_units = select(HardwareProductUnit.barcode).where(HardwareProductUnit.organization_id == org_id,
                                                         HardwareProductUnit.barcode.in_(wanted))
    if skip_units_of is not None:
        on_units = on_units.where(HardwareProductUnit.product_id != skip_units_of)
    found = set((await db.scalars(on_products)).all()) | set((await db.scalars(on_units)).all())
    taken = sorted(c for c in found if c)
    if taken:
        raise Conflict("This barcode already belongs to another product or unit", code="duplicate_barcode",
                       details={"barcodes": taken})


# --- reading products --------------------------------------------------------------------------------------------------------


PRODUCT_LOADERS = (selectinload(HardwareProduct.units),)


def prices_of(product: HardwareProduct) -> pricing.ProductPrices:
    return pricing.ProductPrices(price=product.price, wholesale_price=product.wholesale_price, min_price=product.min_price,
                                 mrp=product.mrp)


def unit_prices(unit: HardwareProductUnit) -> pricing.UnitPrices:
    return pricing.UnitPrices(factor=unit.factor, price=unit.price, wholesale_price=unit.wholesale_price,
                              min_price=unit.min_price)


def option(product: HardwareProduct, unit: Optional[HardwareProductUnit]) -> Dict[str, Any]:
    """One way to sell the product (`unit` None: the base unit), every price worked out per that unit."""
    base, kit = prices_of(product), unit_prices(unit) if unit is not None else None
    return {
        "unit": unit.unit if unit is not None else product.base_unit,
        "factor": unit.factor if unit is not None else pricing.ONE,
        "is_base": unit is None,
        "barcode": unit.barcode if unit is not None else product.barcode,
        "retail_price": pricing.retail_price(base, kit),
        "wholesale_price": pricing.wholesale_price(base, kit),
        "retail_floor": pricing.floor_price(base, kit, HardwarePriceTier.RETAIL),
        "wholesale_floor": pricing.floor_price(base, kit, HardwarePriceTier.WHOLESALE),
        "mrp": pricing.ceiling_price(base, kit),
    }


_PRODUCT_FIELDS = ("id", "category_id", "name", "brand", "size_spec", "sku", "barcode", "hsn_code", "tax_rate", "base_unit",
                   "allow_fraction", "price", "wholesale_price", "min_price", "mrp", "is_active", "created_at")


async def product_rows(db: AsyncSession, products: Sequence[HardwareProduct]) -> List[Dict[str, Any]]:
    """Products as response rows, each with its stored units and every way to sell it."""
    if not products:
        return []
    names = dict((await db.execute(select(HardwareCategory.id, HardwareCategory.name)
                                   .where(HardwareCategory.id.in_({p.category_id for p in products})))).all())
    return [{
        **{c: getattr(p, c) for c in _PRODUCT_FIELDS},
        "category_name": names.get(p.category_id),
        "units": list(p.units),
        "options": [option(p, None)] + [option(p, u) for u in p.units],
    } for p in products]


async def _product(db: AsyncSession, org_id: uuid.UUID, product_id: uuid.UUID, *, for_update: bool = False) -> HardwareProduct:
    stmt = (select(HardwareProduct).where(HardwareProduct.id == product_id, HardwareProduct.organization_id == org_id)
            .options(*PRODUCT_LOADERS).execution_options(populate_existing=True))
    if for_update:
        stmt = stmt.with_for_update(of=HardwareProduct)
    product = await db.scalar(stmt)
    if product is None:
        raise NotFound("Product not found", code="product_not_found")
    return product


async def get_product(db: AsyncSession, user: CurrentUser, product_id: uuid.UUID) -> Dict[str, Any]:
    product = await _product(db, user.organization_id, product_id)
    return (await product_rows(db, [product]))[0]


def _matches(org_id: uuid.UUID, text: str) -> Any:
    """Every word must appear in the name, brand, size or SKU; or the whole text is a product's or unit's exact barcode."""
    words = [w.replace("%", "").replace("_", "") for w in text.split()]
    per_word = [or_(HardwareProduct.name.ilike(f"%{w}%"), HardwareProduct.brand.ilike(f"%{w}%"),
                    HardwareProduct.size_spec.ilike(f"%{w}%"), HardwareProduct.sku.ilike(f"%{w}%")) for w in words if w]
    by_unit_code = select(HardwareProductUnit.product_id).where(HardwareProductUnit.organization_id == org_id,
                                                                HardwareProductUnit.barcode == text)
    return or_(and_(*per_word) if per_word else false(), HardwareProduct.barcode == text,
               HardwareProduct.id.in_(by_unit_code))


async def list_products(
    db: AsyncSession, user: CurrentUser, params: PageParams, *, search: Optional[str], category_id: Optional[uuid.UUID],
    include_inactive: bool,
) -> Tuple[List[Dict[str, Any]], int]:
    stmt = (select(HardwareProduct).where(HardwareProduct.organization_id == user.organization_id)
            .options(*PRODUCT_LOADERS).order_by(HardwareProduct.name, HardwareProduct.size_spec, HardwareProduct.id))
    if not include_inactive:
        stmt = stmt.where(HardwareProduct.is_active.is_(True))
    if category_id is not None:
        stmt = stmt.where(HardwareProduct.category_id == category_id)
    if search and search.strip():
        stmt = stmt.where(_matches(user.organization_id, search.strip()))
    products, total = await paginate(db, stmt, params)
    return await product_rows(db, products), total


# --- writing products --------------------------------------------------------------------------------------------------------


def _unit_row(product: HardwareProduct, data: HardwareUnitIn) -> HardwareProductUnit:
    return HardwareProductUnit(organization_id=product.organization_id, product_id=product.id, unit=data.unit.value,
                               factor=data.factor, price=data.price, wholesale_price=data.wholesale_price,
                               min_price=data.min_price, barcode=data.barcode)


async def create_product(db: AsyncSession, user: CurrentUser, payload: HardwareProductCreate) -> Dict[str, Any]:
    org_id = user.organization_id
    await _category(db, org_id, payload.category_id)
    settings = await load_settings(db, org_id)
    await lock_codes(db, org_id)
    await check_codes(db, org_id, [payload.barcode, *(u.barcode for u in payload.units)])
    product = HardwareProduct(
        id=uuid.uuid4(), organization_id=org_id, category_id=payload.category_id, name=payload.name, brand=payload.brand,
        size_spec=payload.size_spec, sku=payload.sku, barcode=payload.barcode, hsn_code=payload.hsn_code,
        tax_rate=payload.tax_rate if payload.tax_rate is not None else settings.default_tax_rate,
        base_unit=payload.base_unit.value,
        allow_fraction=payload.allow_fraction if payload.allow_fraction is not None else default_fraction(payload.base_unit),
        price=payload.price, wholesale_price=payload.wholesale_price, min_price=payload.min_price, mrp=payload.mrp,
        is_active=True,
    )
    product.units = [_unit_row(product, u) for u in payload.units]
    db.add(product)
    await flush_or_conflict(db)
    await announce(db, org_id, "hardware.catalog.changed", {"product_id": str(product.id)})
    await commit(db)
    return await get_product(db, user, product.id)


async def update_product(db: AsyncSession, user: CurrentUser, product_id: uuid.UUID, payload: HardwareProductUpdate) -> Dict[str, Any]:
    org_id = user.organization_id
    product = await _product(db, org_id, product_id, for_update=True)
    changes = payload.model_dump(exclude_unset=True)
    for field in _REQUIRED:
        if field in changes and changes[field] is None:
            raise ValidationFailed(f"{field} cannot be empty", code="invalid_field")
    if changes.get("category_id") is not None:
        await _category(db, org_id, changes["category_id"])
    if changes.get("base_unit") is not None and any(u.unit == changes["base_unit"] for u in product.units):
        raise ValidationFailed("The product already has this unit as an alternate unit; remove it first",
                               code="unit_is_base", details={"unit": changes["base_unit"]})
    if changes.get("barcode"):
        await lock_codes(db, org_id)
        await check_codes(db, org_id, [changes["barcode"]], skip_product=product.id)
    for field, value in changes.items():
        setattr(product, field, value.value if isinstance(value, HardwareUnit) else value)
    await flush_or_conflict(db)
    await announce(db, org_id, "hardware.catalog.changed", {"product_id": str(product.id)})
    await commit(db)
    return await get_product(db, user, product.id)


async def replace_units(db: AsyncSession, user: CurrentUser, product_id: uuid.UUID, payload: HardwareUnitsReplace) -> Dict[str, Any]:
    """The product's alternate units become exactly this list: matching units are updated, others added or removed."""
    org_id = user.organization_id
    product = await _product(db, org_id, product_id, for_update=True)
    if any(u.unit == product.base_unit for u in payload.units):
        raise ValidationFailed("An alternate unit cannot be the base unit", code="unit_is_base",
                               details={"unit": product.base_unit})
    await lock_codes(db, org_id)
    await check_codes(db, org_id, [product.barcode, *(u.barcode for u in payload.units)], skip_product=product.id,
                      skip_units_of=product.id)

    wanted = {u.unit.value: u for u in payload.units}
    for gone in [u for u in product.units if u.unit not in wanted]:
        product.units.remove(gone)
    await db.flush()  # removed rows go first, so their barcodes are free for the rows that stay
    have = {u.unit: u for u in product.units}
    for unit, data in wanted.items():
        row = have.get(unit)
        if row is None:
            product.units.append(_unit_row(product, data))
            continue
        for field in ("factor", "price", "wholesale_price", "min_price", "barcode"):
            setattr(row, field, getattr(data, field))
    await flush_or_conflict(db)
    await announce(db, org_id, "hardware.catalog.changed", {"product_id": str(product.id)})
    await commit(db)
    return await get_product(db, user, product.id)


# --- the counter: scanner lookup and search ----------------------------------------------------------------------------------


async def lookup(db: AsyncSession, user: CurrentUser, code: str) -> Dict[str, Any]:
    """
    The active product whose code is exactly `code`, and the unit it sells: a product barcode sells the base unit, a unit's
    barcode that unit, and a SKU the base unit. Barcodes win over SKUs.
    """
    code = code.strip()
    org_id = user.organization_id
    active = (HardwareProduct.organization_id == org_id, HardwareProduct.is_active.is_(True))
    product_id = await db.scalar(select(HardwareProduct.id).where(*active, HardwareProduct.barcode == code))
    unit_id: Optional[uuid.UUID] = None
    if product_id is None:
        found = (await db.execute(
            select(HardwareProductUnit.product_id, HardwareProductUnit.id)
            .join(HardwareProduct, HardwareProduct.id == HardwareProductUnit.product_id)
            .where(*active, HardwareProductUnit.barcode == code))).first()
        if found is not None:
            product_id, unit_id = found
    if product_id is None:
        product_id = await db.scalar(select(HardwareProduct.id).where(*active, HardwareProduct.sku == code))
    if product_id is None:
        raise NotFound("No item has this barcode", code="barcode_not_found", details={"code": code})
    product = await _product(db, org_id, product_id)
    row = (await product_rows(db, [product]))[0]
    chosen = next((u for u in product.units if u.id == unit_id), None)
    return {"product": row, "unit": option(product, chosen)}


async def search(db: AsyncSession, user: CurrentUser, q: Optional[str], category_id: Optional[uuid.UUID],
                 limit: int) -> List[Dict[str, Any]]:
    """Active products for the counter: every word of `q` in the name, brand, size or SKU, or an exact barcode."""
    stmt = (select(HardwareProduct).where(HardwareProduct.organization_id == user.organization_id,
                                          HardwareProduct.is_active.is_(True))
            .options(*PRODUCT_LOADERS).order_by(HardwareProduct.name, HardwareProduct.size_spec, HardwareProduct.id)
            .limit(min(limit, SEARCH_LIMIT)))
    if category_id is not None:
        stmt = stmt.where(HardwareProduct.category_id == category_id)
    if q and q.strip():
        stmt = stmt.where(_matches(user.organization_id, q.strip()))
    return await product_rows(db, (await db.scalars(stmt)).all())
