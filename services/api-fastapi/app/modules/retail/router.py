from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, get_current_user, require_roles
from app.core.database import get_db
from app.core.enums import Role
from app.modules.retail import service
from app.schemas.catalog import MenuItemRead
from app.schemas.retail import ItemImportRequest, ItemImportResult

router = APIRouter(prefix="/retail", tags=["Retail (shops and cafés)"], dependencies=[Depends(get_current_user)])

managers = require_roles(Role.OWNER, Role.MANAGER)


@router.get("/items/lookup", response_model=MenuItemRead)
async def lookup_item(
    code: str = Query(min_length=1, max_length=64, description="What the scanner read: a barcode or SKU"),
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Find the active item with this barcode (or SKU). 404 `barcode_not_found` when none has it."""
    return await service.lookup_item(db, user.organization_id, code)


@router.post("/items/import", response_model=ItemImportResult, dependencies=[Depends(managers)])
async def import_items(payload: ItemImportRequest, user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """
    Create or update items from CSV text (columns: name, category, price, gst_rate, hsn, unit, barcode, sku, sold_by_weight).
    Good rows are imported; each bad row is reported with its line number and reasons. `dry_run` checks without saving.
    """
    return await service.import_items(db, user.organization_id, payload.csv, payload.dry_run)
