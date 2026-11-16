"""
The dress shop catalogue: settings, categories, products (styles) and their variants (size x colour), in-store barcodes, the
scanner lookup and the counter search.
"""

import uuid
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.auth import CurrentUser
from app.core.enums import ApparelTaxMode, Topic
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.events import commit, queue_event
from app.core.pagination import PageParams, paginate
from app.models.apparel import ApparelCategory, ApparelProduct, ApparelSettings, ApparelVariant
from app.models.floor import Branch
from app.modules.apparel import stockview
from app.modules.retailkit.barcode import ean13_check_digit
from app.schemas.apparel import (
    ApparelCategoryCreate,
    ApparelCategoryUpdate,
    ApparelSettingsUpdate,
    ProductCreate,
    ProductUpdate,
    VariantGrid,
    VariantIn,
    VariantUpdate,
)

IN_STORE_PREFIX = "29"  # EAN-13 prefixes 20-29 are reserved for codes a shop makes itself
SEARCH_LIMIT = 50

# Unique indexes -> the 409 a person can act on.
_CONFLICTS = {
    "uq_apparel_variants_org_barcode": ("Another variant already has this barcode", "duplicate_barcode"),
    "uq_apparel_variants_org_sku": ("Another variant already has this SKU", "duplicate_sku"),
    "uq_apparel_variants_product_size_colour": ("This style already has that size and colour", "duplicate_variant"),
    "uq_apparel_products_org_style": ("Another product already has this style code", "duplicate_style_code"),
    "uq_apparel_categories_org_name": ("A category with this name already exists", "duplicate_category"),
}


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


# --- settings ----------------------------------------------------------------------------------------------------------------


async def load_settings(db: AsyncSession, org_id: uuid.UUID, *, for_update: bool = False) -> ApparelSettings:
    """The organization's settings row, made with the defaults the first time it is asked for."""
    await db.execute(pg_insert(ApparelSettings).values(organization_id=org_id)
                     .on_conflict_do_nothing(index_elements=[ApparelSettings.organization_id]))
    stmt = select(ApparelSettings).where(ApparelSettings.organization_id == org_id).execution_options(populate_existing=True)
    if for_update:
        stmt = stmt.with_for_update()
    settings = await db.scalar(stmt)
    assert settings is not None
    return settings


async def read_settings(db: AsyncSession, user: CurrentUser) -> ApparelSettings:
    settings = await load_settings(db, user.organization_id)
    await commit(db)
    return settings


async def update_settings(db: AsyncSession, user: CurrentUser, payload: ApparelSettingsUpdate) -> ApparelSettings:
    settings = await load_settings(db, user.organization_id, for_update=True)
    for field, value in payload.model_dump(exclude_unset=True).items():
        if value is None:
            raise ValidationFailed(f"{field} cannot be empty", code="invalid_setting")
        setattr(settings, field, value)
    await commit(db)
    return settings


# --- categories --------------------------------------------------------------------------------------------------------------


async def list_categories(db: AsyncSession, user: CurrentUser, include_inactive: bool) -> List[ApparelCategory]:
    stmt = select(ApparelCategory).where(ApparelCategory.organization_id == user.organization_id)
    if not include_inactive:
        stmt = stmt.where(ApparelCategory.is_active.is_(True))
    return list((await db.scalars(stmt.order_by(ApparelCategory.sort_order, ApparelCategory.name))).all())


async def _category(db: AsyncSession, org_id: uuid.UUID, category_id: uuid.UUID, *, active: bool = True) -> ApparelCategory:
    category = await db.scalar(select(ApparelCategory).where(ApparelCategory.id == category_id,
                                                             ApparelCategory.organization_id == org_id))
    if category is None or (active and not category.is_active):
        raise NotFound("Category not found", code="category_not_found")
    return category


async def create_category(db: AsyncSession, user: CurrentUser, payload: ApparelCategoryCreate) -> ApparelCategory:
    category = ApparelCategory(organization_id=user.organization_id, name=payload.name, sort_order=payload.sort_order,
                               is_active=True)
    db.add(category)
    await flush_or_conflict(db)
    await commit(db)
    return category


async def update_category(db: AsyncSession, user: CurrentUser, category_id: uuid.UUID,
                          payload: ApparelCategoryUpdate) -> ApparelCategory:
    category = await _category(db, user.organization_id, category_id, active=False)
    for field, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(category, field, value)
    await flush_or_conflict(db)
    await commit(db)
    return category


# --- barcodes ----------------------------------------------------------------------------------------------------------------


def in_store_code(number: int) -> str:
    body = f"{IN_STORE_PREFIX}{number:010d}"
    return body + ean13_check_digit(body)


async def allocate_barcodes(db: AsyncSession, org_id: uuid.UUID, count: int, reserved: Iterable[str] = ()) -> List[str]:
    """
    `count` new in-store barcodes from the organization's counter (row locked, so two saves cannot hand out the same one).
    Codes already on a variant, or about to be (`reserved`: typed in the same request), are skipped.
    """
    if count <= 0:
        return []
    settings = await load_settings(db, org_id, for_update=True)
    existing = (await db.scalars(select(ApparelVariant.barcode).where(
        ApparelVariant.organization_id == org_id, ApparelVariant.barcode.like(f"{IN_STORE_PREFIX}%")))).all()
    taken: Set[str] = set(reserved) | {code for code in existing if code}
    codes: List[str] = []
    number = settings.next_barcode
    while len(codes) < count:
        if number >= 10_000_000_000:
            raise Conflict("No in-store barcodes are left", code="barcodes_exhausted")
        code = in_store_code(number)
        number += 1
        if code not in taken:
            codes.append(code)
    settings.next_barcode = number
    return codes


# --- products ----------------------------------------------------------------------------------------------------------------


PRODUCT_LOADERS = (selectinload(ApparelProduct.variants),)


async def _product(db: AsyncSession, org_id: uuid.UUID, product_id: uuid.UUID, *, for_update: bool = False) -> ApparelProduct:
    stmt = (select(ApparelProduct).where(ApparelProduct.id == product_id, ApparelProduct.organization_id == org_id)
            .options(*PRODUCT_LOADERS).execution_options(populate_existing=True))
    if for_update:
        stmt = stmt.with_for_update(of=ApparelProduct)
    product = await db.scalar(stmt)
    if product is None:
        raise NotFound("Product not found", code="product_not_found")
    return product


def _variant_row(variant: ApparelVariant, on_hand: Optional[int]) -> Dict[str, Any]:
    return {c: getattr(variant, c) for c in ("id", "product_id", "size", "colour", "sku", "barcode", "mrp", "price",
                                             "sort_order", "is_active")} | {"on_hand": on_hand}


async def product_rows(
    db: AsyncSession, products: Sequence[ApparelProduct], *, branch_id: Optional[uuid.UUID] = None,
    active_variants_only: bool = False,
) -> List[Dict[str, Any]]:
    """Products as response rows, each with its variants (and, given a branch, the stock of each)."""
    if not products:
        return []
    category_ids = {p.category_id for p in products}
    names = dict((await db.execute(select(ApparelCategory.id, ApparelCategory.name)
                                   .where(ApparelCategory.id.in_(category_ids)))).all())
    stock: Dict[uuid.UUID, int] = {}
    if branch_id is not None:
        stock = await stockview.on_hand(db, branch_id, [v.id for p in products for v in p.variants])
    rows = []
    for p in products:
        variants = [v for v in p.variants if v.is_active or not active_variants_only]
        rows.append({
            **{c: getattr(p, c) for c in ("id", "category_id", "name", "brand", "style_code", "hsn_code", "tax_mode",
                                          "tax_rate", "description", "is_active", "created_at")},
            "category_name": names.get(p.category_id),
            "variants": [_variant_row(v, stock.get(v.id, 0) if branch_id is not None else None) for v in variants],
        })
    return rows


async def list_products(
    db: AsyncSession, user: CurrentUser, params: PageParams, *, search: Optional[str], category_id: Optional[uuid.UUID],
    include_inactive: bool,
) -> Tuple[List[Dict[str, Any]], int]:
    stmt = (select(ApparelProduct).where(ApparelProduct.organization_id == user.organization_id)
            .options(*PRODUCT_LOADERS).order_by(ApparelProduct.name, ApparelProduct.id))
    if not include_inactive:
        stmt = stmt.where(ApparelProduct.is_active.is_(True))
    if category_id is not None:
        stmt = stmt.where(ApparelProduct.category_id == category_id)
    if search and search.strip():
        stmt = stmt.where(_matches(user.organization_id, search.strip()))
    products, total = await paginate(db, stmt, params)
    return await product_rows(db, products), total


def _matches(org_id: uuid.UUID, term: str) -> Any:
    like = f"%{term.replace('%', '').replace('_', '')}%"
    by_code = select(ApparelVariant.product_id).where(ApparelVariant.organization_id == org_id,
                                                      or_(ApparelVariant.sku == term, ApparelVariant.barcode == term))
    return or_(ApparelProduct.name.ilike(like), ApparelProduct.brand.ilike(like), ApparelProduct.style_code.ilike(like),
               ApparelProduct.id.in_(by_code))


async def get_product(db: AsyncSession, user: CurrentUser, product_id: uuid.UUID,
                      branch_id: Optional[uuid.UUID] = None) -> Dict[str, Any]:
    product = await _product(db, user.organization_id, product_id)
    return (await product_rows(db, [product], branch_id=branch_id))[0]


def _check_tax(tax_mode: str, tax_rate: Any) -> None:
    if tax_mode == ApparelTaxMode.FIXED.value and tax_rate is None:
        raise ValidationFailed("A fixed-rate product needs its GST rate", code="tax_rate_required")


SIZE_SLOTS = 1000


class GridOrder:
    """
    Keeps a style's sizes and colours in the order they were first typed: a variant's `sort_order` is
    colour position x 1000 + size position, so sorting by it lists colour by colour, each in size order (S, M, L, XL).
    """

    def __init__(self, variants: Iterable[ApparelVariant] = ()) -> None:
        self.sizes: Dict[str, int] = {}
        self.colours: Dict[str, int] = {}
        for v in variants:
            self.sizes.setdefault(v.size, v.sort_order % SIZE_SLOTS)
            self.colours.setdefault(v.colour, v.sort_order // SIZE_SLOTS)

    def __call__(self, size: str, colour: str) -> int:
        if size not in self.sizes:
            self.sizes[size] = min(max(self.sizes.values(), default=-1) + 1, SIZE_SLOTS - 1)
        if colour not in self.colours:
            self.colours[colour] = max(self.colours.values(), default=-1) + 1
        return self.colours[colour] * SIZE_SLOTS + self.sizes[size]


async def _add_variants(db: AsyncSession, product: ApparelProduct, variants: Sequence[VariantIn]) -> List[ApparelVariant]:
    """New variants on a (locked) product, keeping sizes and colours in the order typed; barcodes are generated if missing."""
    order = GridOrder(product.variants)
    typed = [v.barcode for v in variants if v.barcode]
    codes = iter(await allocate_barcodes(db, product.organization_id, sum(1 for v in variants if not v.barcode), typed))
    added = []
    for v in variants:
        variant = ApparelVariant(
            organization_id=product.organization_id, product_id=product.id, size=v.size, colour=v.colour, sku=v.sku,
            barcode=v.barcode or next(codes), mrp=v.mrp, price=v.price, sort_order=order(v.size, v.colour), is_active=True,
        )
        product.variants.append(variant)
        added.append(variant)
    return added


async def create_product(db: AsyncSession, user: CurrentUser, payload: ProductCreate) -> Dict[str, Any]:
    await _category(db, user.organization_id, payload.category_id)
    _check_tax(payload.tax_mode.value, payload.tax_rate)
    product = ApparelProduct(
        id=uuid.uuid4(), organization_id=user.organization_id, category_id=payload.category_id, name=payload.name,
        brand=payload.brand, style_code=payload.style_code, hsn_code=payload.hsn_code, tax_mode=payload.tax_mode.value,
        tax_rate=payload.tax_rate if payload.tax_mode == ApparelTaxMode.FIXED else None, description=payload.description,
        is_active=True,
    )
    product.variants = []
    db.add(product)
    await _add_variants(db, product, payload.expand())
    await flush_or_conflict(db)
    await announce(db, user.organization_id, "apparel.catalog.changed", {"product_id": str(product.id)})
    await commit(db)
    return await get_product(db, user, product.id)


async def update_product(db: AsyncSession, user: CurrentUser, product_id: uuid.UUID, payload: ProductUpdate) -> Dict[str, Any]:
    product = await _product(db, user.organization_id, product_id, for_update=True)
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("category_id") is not None:
        await _category(db, user.organization_id, changes["category_id"])
    for field in ("category_id", "name", "tax_mode", "is_active"):
        if field in changes and changes[field] is None:
            raise ValidationFailed(f"{field} cannot be empty", code="invalid_field")
    for field, value in changes.items():
        setattr(product, field, value.value if isinstance(value, ApparelTaxMode) else value)
    if product.tax_mode == ApparelTaxMode.SLAB.value:
        product.tax_rate = None
    _check_tax(product.tax_mode, product.tax_rate)
    await flush_or_conflict(db)
    await announce(db, user.organization_id, "apparel.catalog.changed", {"product_id": str(product.id)})
    await commit(db)
    return await get_product(db, user, product.id)


async def add_variants(db: AsyncSession, user: CurrentUser, product_id: uuid.UUID, payload: VariantGrid) -> Dict[str, Any]:
    """
    Add sizes or colours later. Sent as a grid, combinations the style already has are skipped; sent as a list, each must be
    new (409 `duplicate_variant`).
    """
    product = await _product(db, user.organization_id, product_id, for_update=True)
    wanted = payload.expand()
    if not wanted:
        raise ValidationFailed("Nothing to add", code="no_variants")
    if not payload.variants:
        have = {(v.size.lower(), v.colour.lower()) for v in product.variants}
        wanted = [v for v in wanted if (v.size.lower(), v.colour.lower()) not in have]
    await _add_variants(db, product, wanted)
    await flush_or_conflict(db)
    await announce(db, user.organization_id, "apparel.catalog.changed", {"product_id": str(product.id)})
    await commit(db)
    return await get_product(db, user, product.id)


async def _variant(db: AsyncSession, org_id: uuid.UUID, variant_id: uuid.UUID, *, for_update: bool = False) -> ApparelVariant:
    stmt = select(ApparelVariant).where(ApparelVariant.id == variant_id, ApparelVariant.organization_id == org_id)
    if for_update:
        stmt = stmt.with_for_update()
    variant = await db.scalar(stmt.execution_options(populate_existing=True))
    if variant is None:
        raise NotFound("Variant not found", code="variant_not_found")
    return variant


async def update_variant(db: AsyncSession, user: CurrentUser, variant_id: uuid.UUID, payload: VariantUpdate) -> Dict[str, Any]:
    variant = await _variant(db, user.organization_id, variant_id, for_update=True)
    changes = payload.model_dump(exclude_unset=True)
    for field in ("size", "colour", "price", "sort_order", "is_active"):
        if field in changes and changes[field] is None:
            raise ValidationFailed(f"{field} cannot be empty", code="invalid_field")
    for field, value in changes.items():
        setattr(variant, field, value)
    if variant.mrp is not None and variant.price > variant.mrp:
        raise ValidationFailed("The selling price may not exceed the MRP", code="price_above_mrp")
    await flush_or_conflict(db)
    await announce(db, user.organization_id, "apparel.catalog.changed", {"product_id": str(variant.product_id)})
    await commit(db)
    return await get_product(db, user, variant.product_id)


# --- the counter: scanner lookup and search ----------------------------------------------------------------------------------


async def lookup(db: AsyncSession, user: CurrentUser, code: str, branch_id: Optional[uuid.UUID]) -> Dict[str, Any]:
    """The active variant whose barcode, else SKU, is exactly `code`."""
    code = code.strip()
    variant = await db.scalar(
        select(ApparelVariant).join(ApparelProduct, ApparelProduct.id == ApparelVariant.product_id)
        .where(ApparelVariant.organization_id == user.organization_id, ApparelVariant.is_active.is_(True),
               ApparelProduct.is_active.is_(True), or_(ApparelVariant.barcode == code, ApparelVariant.sku == code))
        .order_by((ApparelVariant.barcode == code).desc()).limit(1)
    )
    if variant is None:
        raise NotFound("No item has this barcode", code="barcode_not_found", details={"code": code})
    product = await _product(db, user.organization_id, variant.product_id)
    row = (await product_rows(db, [product], branch_id=branch_id, active_variants_only=True))[0]
    return {"product": row, "variant": next(v for v in row["variants"] if v["id"] == variant.id)}


async def search(
    db: AsyncSession, user: CurrentUser, q: Optional[str], category_id: Optional[uuid.UUID], branch_id: Optional[uuid.UUID],
    limit: int,
) -> List[Dict[str, Any]]:
    """Active products for the counter, matching name, brand, style code (or a variant's exact SKU or barcode)."""
    stmt = (select(ApparelProduct).where(ApparelProduct.organization_id == user.organization_id,
                                         ApparelProduct.is_active.is_(True))
            .options(*PRODUCT_LOADERS).order_by(ApparelProduct.name, ApparelProduct.id).limit(min(limit, SEARCH_LIMIT)))
    if category_id is not None:
        stmt = stmt.where(ApparelProduct.category_id == category_id)
    if q and q.strip():
        stmt = stmt.where(_matches(user.organization_id, q.strip()))
    products = (await db.scalars(stmt)).all()
    rows = await product_rows(db, products, branch_id=branch_id, active_variants_only=True)
    return [r for r in rows if r["variants"]]
