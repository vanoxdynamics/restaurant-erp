"""HTTP routes of the dress shop module (`/apparel`). Thin: every rule lives in the service files of this package."""

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, get_current_user, require_branch_access, require_category, require_roles
from app.core.database import get_db
from app.core.enums import ApparelMovementKind, BusinessCategory, Role
from app.core.idempotency import IdempotencyGuard, idempotent
from app.core.pagination import PageParams, page_params
from app.modules.apparel import catalog, importer, stock
from app.schemas.apparel import (
    ApparelCategoryCreate,
    ApparelCategoryRead,
    ApparelCategoryUpdate,
    ApparelImportRequest,
    ApparelImportResult,
    ApparelMovementRead,
    ApparelPurchaseCreate,
    ApparelPurchaseRead,
    ApparelSettingsRead,
    ApparelSettingsUpdate,
    ApparelStockAdjust,
    ApparelStockRead,
    ApparelStockSettings,
    ProductCreate,
    ProductRead,
    ProductUpdate,
    VariantGrid,
    VariantLookupRead,
    VariantUpdate,
)
from app.schemas.common import Page

router = APIRouter(
    prefix="/apparel",
    tags=["Dress shops"],
    dependencies=[Depends(require_category(BusinessCategory.APPAREL))],
)

managers = require_roles(Role.OWNER, Role.MANAGER)


def _branch(user: CurrentUser, branch_id: Optional[uuid.UUID]) -> Optional[uuid.UUID]:
    if branch_id is not None:
        require_branch_access(user, branch_id)
    return branch_id


# --- settings ----------------------------------------------------------------------------------------------------------------


@router.get("/settings", response_model=ApparelSettingsRead)
async def read_settings(user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """The shop's tax and return settings (made with the defaults on first read)."""
    return await catalog.read_settings(db, user)


@router.put("/settings", response_model=ApparelSettingsRead)
async def update_settings(payload: ApparelSettingsUpdate, user: CurrentUser = Depends(managers), db: AsyncSession = Depends(get_db)):
    """Change the settings. The GST rates and slab are the owner's to confirm with an accountant."""
    return await catalog.update_settings(db, user, payload)


# --- categories --------------------------------------------------------------------------------------------------------------


@router.get("/categories", response_model=List[ApparelCategoryRead])
async def list_categories(include_inactive: bool = False, user: CurrentUser = Depends(get_current_user),
                          db: AsyncSession = Depends(get_db)):
    return await catalog.list_categories(db, user, include_inactive)


@router.post("/categories", response_model=ApparelCategoryRead, status_code=status.HTTP_201_CREATED)
async def create_category(payload: ApparelCategoryCreate, user: CurrentUser = Depends(managers), db: AsyncSession = Depends(get_db)):
    return await catalog.create_category(db, user, payload)


@router.patch("/categories/{category_id}", response_model=ApparelCategoryRead)
async def update_category(category_id: uuid.UUID, payload: ApparelCategoryUpdate, user: CurrentUser = Depends(managers),
                          db: AsyncSession = Depends(get_db)):
    return await catalog.update_category(db, user, category_id, payload)


# --- products and variants ---------------------------------------------------------------------------------------------------


@router.get("/products", response_model=Page[ProductRead])
async def list_products(
    search: Optional[str] = Query(default=None, max_length=100), category_id: Optional[uuid.UUID] = None,
    include_inactive: bool = False, params: PageParams = Depends(page_params),
    user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """Products with their variants, for the admin app."""
    items, total = await catalog.list_products(db, user, params, search=search, category_id=category_id,
                                               include_inactive=include_inactive)
    return Page(items=items, total=total, limit=params.limit, offset=params.offset)


@router.post("/products", response_model=ProductRead, status_code=status.HTTP_201_CREATED)
async def create_product(payload: ProductCreate, user: CurrentUser = Depends(managers), db: AsyncSession = Depends(get_db)):
    """
    A style with its variants: `sizes` x `colours` at one `price` (and `mrp`), or an explicit `variants` list. Variants saved
    without a barcode get an in-store EAN-13 (prefix 29).
    """
    return await catalog.create_product(db, user, payload)


@router.get("/products/{product_id}", response_model=ProductRead)
async def get_product(product_id: uuid.UUID, branch_id: Optional[uuid.UUID] = None,
                      user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """A product with all its variants; given `branch_id`, each variant's stock there (`on_hand`)."""
    return await catalog.get_product(db, user, product_id, _branch(user, branch_id))


@router.patch("/products/{product_id}", response_model=ProductRead)
async def update_product(product_id: uuid.UUID, payload: ProductUpdate, user: CurrentUser = Depends(managers),
                         db: AsyncSession = Depends(get_db)):
    """Change a product. Products are never deleted (sales point at them): set `is_active: false`."""
    return await catalog.update_product(db, user, product_id, payload)


@router.post("/products/{product_id}/variants", response_model=ProductRead, status_code=status.HTTP_201_CREATED)
async def add_variants(product_id: uuid.UUID, payload: VariantGrid, user: CurrentUser = Depends(managers),
                       db: AsyncSession = Depends(get_db)):
    """Add sizes or colours to a style (a grid skips combinations it already has)."""
    return await catalog.add_variants(db, user, product_id, payload)


@router.patch("/variants/{variant_id}", response_model=ProductRead)
async def update_variant(variant_id: uuid.UUID, payload: VariantUpdate, user: CurrentUser = Depends(managers),
                         db: AsyncSession = Depends(get_db)):
    """Change a variant's price, MRP, codes, size or colour, or switch it off. Returns its product."""
    return await catalog.update_variant(db, user, variant_id, payload)


# --- the counter -------------------------------------------------------------------------------------------------------------


@router.get("/variants/lookup", response_model=VariantLookupRead)
async def lookup_variant(
    code: str = Query(min_length=1, max_length=64, description="What the scanner read: a barcode or SKU"),
    branch_id: Optional[uuid.UUID] = None, user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """The active variant with this barcode (a barcode match wins over a SKU match). 404 `barcode_not_found`."""
    return await catalog.lookup(db, user, code, _branch(user, branch_id))


@router.get("/catalog/search", response_model=List[ProductRead])
async def search_catalog(
    q: Optional[str] = Query(default=None, max_length=100), category_id: Optional[uuid.UUID] = None,
    branch_id: Optional[uuid.UUID] = None, limit: int = Query(default=30, ge=1, le=catalog.SEARCH_LIMIT),
    user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """Active products matching name, brand or style code (or a variant's exact SKU or barcode), with stock in `branch_id`."""
    return await catalog.search(db, user, q, category_id, _branch(user, branch_id), limit)


# --- import ------------------------------------------------------------------------------------------------------------------


@router.post("/import", response_model=ApparelImportResult)
async def import_catalogue(payload: ApparelImportRequest, user: CurrentUser = Depends(managers), db: AsyncSession = Depends(get_db)):
    """
    CSV with one row per variant (columns: style_code, name, category, brand, hsn, size, colour, mrp, price, barcode, sku,
    gst_rate, opening_stock). Good rows are saved together; bad rows are reported by line. `dry_run` saves nothing.
    """
    return await importer.import_catalogue(db, user, payload.csv, payload.dry_run, payload.branch_id)


# --- stock -------------------------------------------------------------------------------------------------------------------


@router.get("/stock", response_model=List[ApparelStockRead])
async def list_stock(
    branch_id: Optional[uuid.UUID] = None, search: Optional[str] = Query(default=None, max_length=100), low_only: bool = False,
    category_id: Optional[uuid.UUID] = None, product_id: Optional[uuid.UUID] = None,
    user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """
    Pieces on hand of every active variant in the branch (never stocked reads as 0). `low_only` keeps variants at or below
    their low-stock level or with nothing left. Anyone may read it; cost and value go to owners and managers only.
    """
    return await stock.list_stock(db, user, branch_id, search=search, low_only=low_only, category_id=category_id,
                                  product_id=product_id)


@router.put("/stock/{variant_id}", response_model=ApparelStockRead)
async def update_stock_settings(variant_id: uuid.UUID, payload: ApparelStockSettings, user: CurrentUser = Depends(managers),
                                db: AsyncSession = Depends(get_db)):
    """A variant's low-stock level and cost price in a branch."""
    return await stock.update_settings(db, user, variant_id, payload)


@router.post("/purchases", response_model=ApparelPurchaseRead, status_code=status.HTTP_201_CREATED)
async def receive_purchase(
    payload: ApparelPurchaseCreate, user: CurrentUser = Depends(managers),
    guard: IdempotencyGuard = Depends(idempotent("apparel_purchases")), db: AsyncSession = Depends(get_db),
):
    """A delivery: every line adds pieces. Requires X-Idempotency-Key so a retry cannot receive it twice."""
    cached = await guard.begin(payload)
    if cached:
        return cached.as_response()
    try:
        purchase = await stock.receive_purchase(db, user, payload)
    except Exception:
        await guard.release()
        raise
    return await guard.complete(status.HTTP_201_CREATED, ApparelPurchaseRead.model_validate(purchase))


@router.get("/purchases", response_model=Page[ApparelPurchaseRead])
async def list_purchases(branch_id: Optional[uuid.UUID] = None, params: PageParams = Depends(page_params),
                         user: CurrentUser = Depends(managers), db: AsyncSession = Depends(get_db)):
    items, total = await stock.list_purchases(db, user, params, branch_id)
    return Page(items=items, total=total, limit=params.limit, offset=params.offset)


@router.get("/purchases/{purchase_id}", response_model=ApparelPurchaseRead)
async def get_purchase(purchase_id: uuid.UUID, user: CurrentUser = Depends(managers), db: AsyncSession = Depends(get_db)):
    return await stock.get_purchase(db, user, purchase_id)


@router.post("/adjustments", response_model=ApparelStockRead)
async def adjust_stock(
    payload: ApparelStockAdjust, user: CurrentUser = Depends(managers),
    guard: IdempotencyGuard = Depends(idempotent("apparel_adjustments")), db: AsyncSession = Depends(get_db),
):
    """Add or remove pieces (`delta`) or record a physical count (`counted`), with a reason. Requires X-Idempotency-Key."""
    cached = await guard.begin(payload)
    if cached:
        return cached.as_response()
    try:
        row = await stock.adjust(db, user, payload)
    except Exception:
        await guard.release()
        raise
    return await guard.complete(status.HTTP_200_OK, ApparelStockRead.model_validate(row))


@router.get("/movements", response_model=Page[ApparelMovementRead])
async def list_movements(
    branch_id: Optional[uuid.UUID] = None, variant_id: Optional[uuid.UUID] = None, kind: Optional[ApparelMovementKind] = None,
    params: PageParams = Depends(page_params), user: CurrentUser = Depends(managers), db: AsyncSession = Depends(get_db),
):
    """The stock ledger, newest first."""
    items, total = await stock.list_movements(db, user, params, branch_id, variant_id, kind)
    return Page(items=items, total=total, limit=params.limit, offset=params.offset)
