"""HTTP routes of the hardware shop module (`/hardware`). Thin: every rule lives in the service files of this package."""

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, get_current_user, require_category, require_roles
from app.core.database import get_db
from app.core.enums import BusinessCategory, HardwareMovementKind, Role
from app.core.idempotency import IdempotencyGuard, idempotent
from app.core.pagination import PageParams, page_params
from app.modules.hardware import catalog, importer, stock
from app.schemas.common import Page
from app.schemas.hardware import (
    HardwareCategoryCreate,
    HardwareCategoryRead,
    HardwareCategoryUpdate,
    HardwareImportRequest,
    HardwareImportResult,
    HardwareSettingsRead,
    HardwareSettingsUpdate,
    HardwareProductCreate,
    HardwareLookupRead,
    HardwareMovementRead,
    HardwareProductRead,
    HardwareProductUpdate,
    HardwarePurchaseCreate,
    HardwarePurchaseRead,
    HardwareStockAdjust,
    HardwareStockRead,
    HardwareStockSettings,
    HardwareUnitsReplace,
)

router = APIRouter(
    prefix="/hardware",
    tags=["Hardware shops"],
    dependencies=[Depends(require_category(BusinessCategory.HARDWARE))],
)

managers = require_roles(Role.OWNER, Role.MANAGER)


# --- settings ----------------------------------------------------------------------------------------------------------------


@router.get("/settings", response_model=HardwareSettingsRead)
async def read_settings(user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """The shop's tax, rounding and estimate settings (made with the defaults on first read)."""
    return await catalog.read_settings(db, user)


@router.put("/settings", response_model=HardwareSettingsRead)
async def update_settings(payload: HardwareSettingsUpdate, user: CurrentUser = Depends(managers),
                          db: AsyncSession = Depends(get_db)):
    """Change the settings. `state_code` is the shop's GST state, used when a branch has no GSTIN."""
    return await catalog.update_settings(db, user, payload)


# --- categories --------------------------------------------------------------------------------------------------------------


@router.get("/categories", response_model=List[HardwareCategoryRead])
async def list_categories(include_inactive: bool = False, user: CurrentUser = Depends(get_current_user),
                          db: AsyncSession = Depends(get_db)):
    return await catalog.list_categories(db, user, include_inactive)


@router.post("/categories", response_model=HardwareCategoryRead, status_code=status.HTTP_201_CREATED)
async def create_category(payload: HardwareCategoryCreate, user: CurrentUser = Depends(managers),
                          db: AsyncSession = Depends(get_db)):
    return await catalog.create_category(db, user, payload)


@router.patch("/categories/{category_id}", response_model=HardwareCategoryRead)
async def update_category(category_id: uuid.UUID, payload: HardwareCategoryUpdate, user: CurrentUser = Depends(managers),
                          db: AsyncSession = Depends(get_db)):
    return await catalog.update_category(db, user, category_id, payload)


# --- products and units ------------------------------------------------------------------------------------------------------


@router.get("/products", response_model=Page[HardwareProductRead])
async def list_products(
    search: Optional[str] = Query(default=None, max_length=100), category_id: Optional[uuid.UUID] = None,
    include_inactive: bool = False, branch_id: Optional[uuid.UUID] = None, params: PageParams = Depends(page_params),
    user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """Products with their units and the price of each, for the admin app. Given `branch_id`, each shows `on_hand` there."""
    items, total = await catalog.list_products(db, user, params, search=search, category_id=category_id,
                                               include_inactive=include_inactive, branch_id=branch_id)
    return Page(items=items, total=total, limit=params.limit, offset=params.offset)


@router.post("/products", response_model=HardwareProductRead, status_code=status.HTTP_201_CREATED)
async def create_product(payload: HardwareProductCreate, user: CurrentUser = Depends(managers), db: AsyncSession = Depends(get_db)):
    """
    A product with its base unit, prices per base unit (up to 4 decimals) and any alternate units (unit, factor, and
    optionally its own prices and barcode). `tax_rate` defaults to the settings; `allow_fraction` to the unit's kind.
    """
    return await catalog.create_product(db, user, payload)


@router.get("/products/lookup", response_model=HardwareLookupRead)
async def lookup_product(
    code: str = Query(min_length=1, max_length=64, description="What the scanner read: a barcode or SKU"),
    branch_id: Optional[uuid.UUID] = None,
    user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """The active product with this code and the unit it sells (a box barcode sells a box). 404 `barcode_not_found`."""
    return await catalog.lookup(db, user, code, branch_id)


@router.get("/products/{product_id}", response_model=HardwareProductRead)
async def get_product(product_id: uuid.UUID, branch_id: Optional[uuid.UUID] = None,
                      user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await catalog.get_product(db, user, product_id, branch_id)


@router.patch("/products/{product_id}", response_model=HardwareProductRead)
async def update_product(product_id: uuid.UUID, payload: HardwareProductUpdate, user: CurrentUser = Depends(managers),
                         db: AsyncSession = Depends(get_db)):
    """Change a product. Products are never deleted (sales point at them): set `is_active: false`."""
    return await catalog.update_product(db, user, product_id, payload)


@router.put("/products/{product_id}/units", response_model=HardwareProductRead)
async def replace_units(product_id: uuid.UUID, payload: HardwareUnitsReplace, user: CurrentUser = Depends(managers),
                        db: AsyncSession = Depends(get_db)):
    """Replace the product's alternate units: listed units are added or updated, the others removed."""
    return await catalog.replace_units(db, user, product_id, payload)


# --- the counter -------------------------------------------------------------------------------------------------------------


@router.get("/catalog/search", response_model=List[HardwareProductRead])
async def search_catalog(
    q: Optional[str] = Query(default=None, max_length=100), category_id: Optional[uuid.UUID] = None,
    limit: int = Query(default=30, ge=1, le=catalog.SEARCH_LIMIT), branch_id: Optional[uuid.UUID] = None,
    user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """
    Active products where every word of `q` is in the name, brand, size or SKU, or `q` is an exact barcode. `on_hand` (base
    units) is filled when `branch_id` is given.
    """
    return await catalog.search(db, user, q, category_id, limit, branch_id)


# --- import ------------------------------------------------------------------------------------------------------------------


@router.post("/import", response_model=HardwareImportResult)
async def import_catalogue(payload: HardwareImportRequest, user: CurrentUser = Depends(managers),
                           db: AsyncSession = Depends(get_db)):
    """
    CSV with one row per product (columns: name, category, brand, size, price, wholesale_price, min_price, mrp, gst_rate, hsn,
    unit, allow_fraction, barcode, sku, alt_unit, alt_factor, alt_price, alt_barcode). Good rows are saved together; bad rows
    are reported by line. `dry_run` saves nothing.
    """
    return await importer.import_catalogue(db, user, payload.csv, payload.dry_run)


# --- stock -------------------------------------------------------------------------------------------------------------------


@router.get("/stock", response_model=List[HardwareStockRead])
async def list_stock(
    branch_id: Optional[uuid.UUID] = None, search: Optional[str] = Query(default=None, max_length=100), low_only: bool = False,
    category_id: Optional[uuid.UUID] = None, product_id: Optional[uuid.UUID] = None,
    user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """
    Base units on hand of every active product in the branch (never stocked reads as 0). `low_only` keeps products at or below
    their low-stock level or with nothing left. Anyone may read it; cost and value go to owners and managers only.
    """
    return await stock.list_stock(db, user, branch_id, search=search, low_only=low_only, category_id=category_id,
                                  product_id=product_id)


@router.put("/stock/{product_id}", response_model=HardwareStockRead)
async def update_stock_settings(product_id: uuid.UUID, payload: HardwareStockSettings, user: CurrentUser = Depends(managers),
                                db: AsyncSession = Depends(get_db)):
    """A product's low-stock level and cost price (per base unit) in a branch."""
    return await stock.update_settings(db, user, product_id, payload)


@router.post("/purchases", response_model=HardwarePurchaseRead, status_code=status.HTTP_201_CREATED)
async def receive_purchase(
    payload: HardwarePurchaseCreate, user: CurrentUser = Depends(managers),
    guard: IdempotencyGuard = Depends(idempotent("hardware_purchases")), db: AsyncSession = Depends(get_db),
):
    """
    A delivery. A line may be in any unit of its product with the cost of one of that unit (2 coils at 3,000.00 adds 180 m);
    stock gains base units and the cost per base unit is worked out. Requires X-Idempotency-Key so a retry cannot receive it twice.
    """
    cached = await guard.begin(payload)
    if cached:
        return cached.as_response()
    try:
        purchase = await stock.receive_purchase(db, user, payload)
    except Exception:
        await guard.release()
        raise
    return await guard.complete(status.HTTP_201_CREATED, HardwarePurchaseRead.model_validate(purchase))


@router.get("/purchases", response_model=Page[HardwarePurchaseRead])
async def list_purchases(branch_id: Optional[uuid.UUID] = None, params: PageParams = Depends(page_params),
                         user: CurrentUser = Depends(managers), db: AsyncSession = Depends(get_db)):
    items, total = await stock.list_purchases(db, user, params, branch_id)
    return Page(items=items, total=total, limit=params.limit, offset=params.offset)


@router.get("/purchases/{purchase_id}", response_model=HardwarePurchaseRead)
async def get_purchase(purchase_id: uuid.UUID, user: CurrentUser = Depends(managers), db: AsyncSession = Depends(get_db)):
    return await stock.get_purchase(db, user, purchase_id)


@router.post("/adjustments", response_model=HardwareStockRead)
async def adjust_stock(
    payload: HardwareStockAdjust, user: CurrentUser = Depends(managers),
    guard: IdempotencyGuard = Depends(idempotent("hardware_adjustments")), db: AsyncSession = Depends(get_db),
):
    """Add or remove base units (`delta`) or record a physical count (`counted`), with a reason. Requires X-Idempotency-Key."""
    cached = await guard.begin(payload)
    if cached:
        return cached.as_response()
    try:
        row = await stock.adjust(db, user, payload)
    except Exception:
        await guard.release()
        raise
    return await guard.complete(status.HTTP_200_OK, HardwareStockRead.model_validate(row))


@router.get("/movements", response_model=Page[HardwareMovementRead])
async def list_movements(
    branch_id: Optional[uuid.UUID] = None, product_id: Optional[uuid.UUID] = None, kind: Optional[HardwareMovementKind] = None,
    params: PageParams = Depends(page_params), user: CurrentUser = Depends(managers), db: AsyncSession = Depends(get_db),
):
    """The stock ledger, newest first."""
    items, total = await stock.list_movements(db, user, params, branch_id, product_id, kind)
    return Page(items=items, total=total, limit=params.limit, offset=params.offset)
