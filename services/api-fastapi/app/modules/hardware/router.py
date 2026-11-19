"""HTTP routes of the hardware shop module (`/hardware`). Thin: every rule lives in the service files of this package."""

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, get_current_user, require_category, require_roles
from app.core.database import get_db
from app.core.enums import BusinessCategory, Role
from app.core.pagination import PageParams, page_params
from app.modules.hardware import catalog, importer
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
    HardwareProductRead,
    HardwareProductUpdate,
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
    include_inactive: bool = False, params: PageParams = Depends(page_params),
    user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """Products with their units and the price of each, for the admin app."""
    items, total = await catalog.list_products(db, user, params, search=search, category_id=category_id,
                                               include_inactive=include_inactive)
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
    user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """The active product with this code and the unit it sells (a box barcode sells a box). 404 `barcode_not_found`."""
    return await catalog.lookup(db, user, code)


@router.get("/products/{product_id}", response_model=HardwareProductRead)
async def get_product(product_id: uuid.UUID, user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await catalog.get_product(db, user, product_id)


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
    limit: int = Query(default=30, ge=1, le=catalog.SEARCH_LIMIT),
    user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """Active products where every word of `q` is in the name, brand, size or SKU, or `q` is an exact barcode."""
    return await catalog.search(db, user, q, category_id, limit)


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
