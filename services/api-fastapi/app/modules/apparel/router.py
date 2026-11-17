"""HTTP routes of the dress shop module (`/apparel`). Thin: every rule lives in the service files of this package."""

import uuid
from datetime import date
from enum import StrEnum
from typing import List, Optional

import redis.asyncio as aioredis
from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, get_current_user, require_branch_access, require_category, require_roles
from app.core.database import get_db
from app.core.enums import ApparelMovementKind, ApparelPaymentMode, BillStatus, BusinessCategory, Role
from app.core.errors import ValidationFailed
from app.core.idempotency import IdempotencyGuard, idempotent
from app.core.pagination import PageParams, page_params
from app.core.redis import get_redis
from app.models.apparel import ApparelStoreCredit
from app.modules.apparel import catalog, importer, returns, sales, stock
from app.modules.kots.slip import COLUMNS
from app.modules.retailkit import receipt as receipts
from app.schemas.apparel import (
    ApparelCategoryCreate,
    ApparelCategoryRead,
    ApparelCategoryUpdate,
    ApparelImportRequest,
    ApparelImportResult,
    ApparelMovementRead,
    ApparelPaymentCreate,
    ApparelPaymentRead,
    ApparelPaymentResult,
    ApparelPaymentReverse,
    ApparelReceiptRead,
    ApparelReturnCreate,
    ApparelReturnRead,
    ApparelStoreCreditRead,
    ApparelSaleCreate,
    ApparelSaleRead,
    ApparelSplitPayment,
    ApparelTerminalComplete,
    ApparelTerminalStart,
    ApparelVoid,
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
from app.schemas.billing import PrintRowRead
from app.schemas.common import Page

router = APIRouter(
    prefix="/apparel",
    tags=["Dress shops"],
    dependencies=[Depends(require_category(BusinessCategory.APPAREL))],
)

managers = require_roles(Role.OWNER, Role.MANAGER)
cashiers = require_roles(Role.OWNER, Role.MANAGER, Role.CASHIER)


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


# --- sales and payments ------------------------------------------------------------------------------------------------------


def _result(payment, sale, change=None) -> ApparelPaymentResult:
    result = ApparelPaymentResult(payment=ApparelPaymentRead.model_validate(payment), sale=ApparelSaleRead.model_validate(sale))
    if change is not None:
        result.change_due = change
    return result


@router.post("/sales", response_model=ApparelSaleRead, status_code=status.HTTP_201_CREATED)
async def create_sale(
    payload: ApparelSaleCreate, background: BackgroundTasks, user: CurrentUser = Depends(cashiers),
    guard: IdempotencyGuard = Depends(idempotent("apparel_sales")), db: AsyncSession = Depends(get_db),
    redis_client: aioredis.Redis = Depends(get_redis),
):
    """Make the invoice and take its pieces out of stock. Requires X-Idempotency-Key; `client_sale_id` also dedupes."""
    cached = await guard.begin(payload)
    if cached:
        return cached.as_response()
    try:
        sale = await sales.create_sale(db, user, payload, redis_client=redis_client, background=background)
    except Exception:
        await guard.release()
        raise
    return await guard.complete(status.HTTP_201_CREATED, ApparelSaleRead.model_validate(sale))


@router.get("/sales", response_model=Page[ApparelSaleRead])
async def list_sales(
    branch_id: Optional[uuid.UUID] = None, on_date: Optional[date] = None, status: Optional[BillStatus] = None,
    customer_id: Optional[uuid.UUID] = None, search: Optional[str] = Query(None, max_length=50),
    phone: Optional[str] = Query(None, max_length=20), params: PageParams = Depends(page_params), user: CurrentUser = Depends(cashiers), db: AsyncSession = Depends(get_db),
):
    items, total = await sales.list_sales(db, user, params, branch_id, on_date=on_date, status=status,
                                          customer_id=customer_id, search=search, phone=phone)
    return Page(items=items, total=total, limit=params.limit, offset=params.offset)


@router.get("/sales/{sale_id}", response_model=ApparelSaleRead)
async def get_sale(sale_id: uuid.UUID, user: CurrentUser = Depends(cashiers), db: AsyncSession = Depends(get_db)):
    return await sales.get_sale(db, user, sale_id)


@router.post("/sales/{sale_id}/payments", response_model=ApparelPaymentResult)
async def record_payment(
    sale_id: uuid.UUID, payload: ApparelPaymentCreate, user: CurrentUser = Depends(cashiers),
    guard: IdempotencyGuard = Depends(idempotent("apparel_payments")), db: AsyncSession = Depends(get_db),
):
    """One tender (cash may exceed the balance and gives change). Requires X-Idempotency-Key."""
    cached = await guard.begin({"sale_id": str(sale_id), **payload.model_dump(mode="json")})
    if cached:
        return cached.as_response()
    try:
        if payload.payment_mode == ApparelPaymentMode.CREDIT_NOTE:
            payment, sale = await returns.pay_with_credit(db, user, sale_id, code=payload.credit_note_code or "",
                                                          amount=payload.amount, idempotency_key=guard.key)
            return await guard.complete(status.HTTP_200_OK, _result(payment, sale))
        payment, sale, change = await sales.tenders.record(
            db, user, sale_id, mode=payload.payment_mode.value, amount=payload.amount, reference_id=payload.reference_id,
            idempotency_key=guard.key)
    except Exception:
        await guard.release()
        raise
    return await guard.complete(status.HTTP_200_OK, _result(payment, sale, change))


@router.post("/sales/{sale_id}/payments/split", response_model=ApparelSaleRead)
async def split_payment(
    sale_id: uuid.UUID, payload: ApparelSplitPayment, user: CurrentUser = Depends(cashiers),
    guard: IdempotencyGuard = Depends(idempotent("apparel_payments")), db: AsyncSession = Depends(get_db),
):
    """Settle the whole balance across several tenders at once. Requires X-Idempotency-Key."""
    if any(p.payment_mode == ApparelPaymentMode.CREDIT_NOTE for p in payload.payments):
        raise ValidationFailed("Pay store credit as its own payment, then split the rest", code="credit_note_not_in_split")
    cached = await guard.begin({"sale_id": str(sale_id), **payload.model_dump(mode="json")})
    if cached:
        return cached.as_response()
    try:
        sale = await sales.tenders.split(
            db, user, sale_id, [(p.payment_mode.value, p.amount, p.reference_id) for p in payload.payments])
    except Exception:
        await guard.release()
        raise
    return await guard.complete(status.HTTP_200_OK, ApparelSaleRead.model_validate(sale))


@router.post("/sales/{sale_id}/payments/terminal", response_model=ApparelPaymentResult, status_code=status.HTTP_201_CREATED)
async def start_terminal_payment(
    sale_id: uuid.UUID, payload: ApparelTerminalStart, user: CurrentUser = Depends(cashiers),
    guard: IdempotencyGuard = Depends(idempotent("apparel_terminal_payments")), db: AsyncSession = Depends(get_db),
):
    """Start a card/UPI payment on the terminal; it is saved as pending. Requires X-Idempotency-Key."""
    cached = await guard.begin({"sale_id": str(sale_id), **payload.model_dump(mode="json")})
    if cached:
        return cached.as_response()
    try:
        payment, sale = await sales.tenders.start_terminal(
            db, user, sale_id, mode=payload.payment_mode.value, amount=payload.amount, provider=payload.provider,
            idempotency_key=guard.key)
    except Exception:
        await guard.release()
        raise
    return await guard.complete(status.HTTP_201_CREATED, _result(payment, sale))


@router.post("/payments/{payment_id}/terminal/complete", response_model=ApparelPaymentResult)
async def complete_terminal_payment(
    payment_id: uuid.UUID, payload: ApparelTerminalComplete, user: CurrentUser = Depends(cashiers),
    db: AsyncSession = Depends(get_db),
):
    """Record the terminal's answer for a pending payment. Sending the same answer again is safe."""
    payment, sale = await sales.tenders.complete_terminal(
        db, user, payment_id, outcome=payload.outcome, approval_code=payload.approval_code, details=payload.details,
        source=payload.source, message=payload.message)
    return _result(payment, sale)


@router.post("/payments/{payment_id}/reverse", response_model=ApparelPaymentResult)
async def reverse_terminal_payment(
    payment_id: uuid.UUID, payload: ApparelPaymentReverse, user: CurrentUser = Depends(cashiers),
    db: AsyncSession = Depends(get_db), redis_client: aioredis.Redis = Depends(get_redis),
):
    """A terminal payment was voided on the device: it stops counting and the sale reopens. Manager approval; audited."""
    payment, sale = await sales.tenders.reverse_terminal(
        db, user, payment_id, reason=payload.reason, details=payload.details, approver_pin=payload.approver_pin,
        check_only=payload.check_only, redis_client=redis_client)
    return _result(payment, sale)


@router.post("/sales/{sale_id}/void", response_model=ApparelSaleRead)
async def void_sale(
    sale_id: uuid.UUID, payload: ApparelVoid, background: BackgroundTasks, user: CurrentUser = Depends(cashiers),
    db: AsyncSession = Depends(get_db), redis_client: aioredis.Redis = Depends(get_redis),
):
    """Void an unpaid sale with a manager's PIN; its pieces go back into stock."""
    return await sales.void_sale(db, user, sale_id, payload, redis_client=redis_client, background=background)


class ReceiptFormat(StrEnum):
    ROWS = "rows"
    TEXT = "text"
    HTML = "html"


@router.get("/sales/{sale_id}/receipt", response_model=ApparelReceiptRead,
            responses={200: {"content": {"text/plain": {}, "text/html": {}}}})
async def get_receipt(
    sale_id: uuid.UUID, format: ReceiptFormat = ReceiptFormat.ROWS, paper_mm: int = Query(80, description="58 or 80"),
    user: CurrentUser = Depends(cashiers), db: AsyncSession = Depends(get_db),
):
    """The printed invoice as print rows, plain text, or a printable HTML page."""
    if paper_mm not in COLUMNS:
        raise ValidationFailed("Paper width must be 58 or 80 mm", code="invalid_width")
    receipt = await sales.build_receipt(db, user, sale_id)
    if format == ReceiptFormat.TEXT:
        return Response(receipts.render_text(receipt, paper_mm), media_type="text/plain; charset=utf-8",
                        headers={"Cache-Control": "no-store"})
    if format == ReceiptFormat.HTML:
        return Response(receipts.render_html(receipt, paper_mm), media_type="text/html; charset=utf-8", headers={
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'self'",
            "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})
    return ApparelReceiptRead(
        sale_number=receipt.number, paper_mm=paper_mm, logo_data_url=receipt.logo_data_url,
        rows=[PrintRowRead(style=st, align=al, text=tx) for st, al, tx in receipts.rows(receipt, paper_mm)],
        text=receipts.render_text(receipt, paper_mm))


# --- returns and store credit ------------------------------------------------------------------------------------------------


async def _return_read(db: AsyncSession, found) -> ApparelReturnRead:
    out = ApparelReturnRead.model_validate(found)
    if found.refund_mode == "store_credit":
        out.store_credit_code = await db.scalar(select(ApparelStoreCredit.code).where(ApparelStoreCredit.return_id == found.id))
    return out


@router.post("/returns", response_model=ApparelReturnRead, status_code=status.HTTP_201_CREATED)
async def create_return(
    payload: ApparelReturnCreate, background: BackgroundTasks, user: CurrentUser = Depends(cashiers),
    guard: IdempotencyGuard = Depends(idempotent("apparel_returns")), db: AsyncSession = Depends(get_db),
    redis_client: aioredis.Redis = Depends(get_redis),
):
    """Goods back against a paid sale: a credit note, a cash refund or store credit, and the pieces back on the shelf."""
    cached = await guard.begin(payload)
    if cached:
        return cached.as_response()
    try:
        found = await returns.create_return(db, user, payload, redis_client=redis_client, background=background)
        body = await _return_read(db, found)
    except Exception:
        await guard.release()
        raise
    return await guard.complete(status.HTTP_201_CREATED, body)


@router.get("/returns", response_model=Page[ApparelReturnRead])
async def list_returns(
    branch_id: Optional[uuid.UUID] = None, sale_id: Optional[uuid.UUID] = None, on_date: Optional[date] = None,
    params: PageParams = Depends(page_params), user: CurrentUser = Depends(cashiers), db: AsyncSession = Depends(get_db),
):
    items, total = await returns.list_returns(db, user, params, branch_id, sale_id=sale_id, on_date=on_date)
    return Page(items=[await _return_read(db, r) for r in items], total=total, limit=params.limit, offset=params.offset)


@router.get("/returns/{return_id}", response_model=ApparelReturnRead)
async def get_return(return_id: uuid.UUID, user: CurrentUser = Depends(cashiers), db: AsyncSession = Depends(get_db)):
    return await _return_read(db, await returns.get_return(db, user, return_id))


@router.get("/returns/{return_id}/receipt", response_model=ApparelReceiptRead,
            responses={200: {"content": {"text/plain": {}, "text/html": {}}}})
async def get_return_receipt(
    return_id: uuid.UUID, format: ReceiptFormat = ReceiptFormat.ROWS, paper_mm: int = Query(80, description="58 or 80"),
    user: CurrentUser = Depends(cashiers), db: AsyncSession = Depends(get_db),
):
    """The printed credit note, in the same shapes as the invoice."""
    if paper_mm not in COLUMNS:
        raise ValidationFailed("Paper width must be 58 or 80 mm", code="invalid_width")
    receipt = await returns.build_receipt(db, user, return_id)
    if format == ReceiptFormat.TEXT:
        return Response(receipts.render_text(receipt, paper_mm), media_type="text/plain; charset=utf-8",
                        headers={"Cache-Control": "no-store"})
    if format == ReceiptFormat.HTML:
        return Response(receipts.render_html(receipt, paper_mm), media_type="text/html; charset=utf-8", headers={
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'self'",
            "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})
    return ApparelReceiptRead(
        sale_number=receipt.number, paper_mm=paper_mm, logo_data_url=receipt.logo_data_url,
        rows=[PrintRowRead(style=st, align=al, text=tx) for st, al, tx in receipts.rows(receipt, paper_mm)],
        text=receipts.render_text(receipt, paper_mm))


@router.get("/store-credits/{code:path}", response_model=ApparelStoreCreditRead)
async def get_store_credit(code: str, user: CurrentUser = Depends(cashiers), db: AsyncSession = Depends(get_db)):
    """The balance of a credit note, at the counter. Codes contain slashes (the credit note number)."""
    return await returns.get_store_credit(db, user, code)
