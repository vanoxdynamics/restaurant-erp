import uuid
from datetime import date
from typing import Optional

from enum import StrEnum

import redis.asyncio as aioredis
from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, get_current_user, require_roles, require_roles_by_module
from app.core.database import get_db
from app.core.errors import ValidationFailed
from app.core.enums import BillStatus, Role
from app.core.idempotency import IdempotencyGuard, idempotent
from app.core.modules import ModuleKey
from app.core.pagination import PageParams, page_params
from app.core.redis import get_redis
from app.modules.billing import receipt as receipts
from app.modules.billing import service
from app.modules.kots.slip import COLUMNS
from app.schemas.billing import (
    BillCreate,
    BillRead,
    BillVoidRequest,
    PaymentCreate,
    PaymentRead,
    PaymentResult,
    PaymentReverse,
    PrintRowRead,
    ReceiptRead,
    SplitPaymentRequest,
    TerminalPaymentComplete,
    TerminalPaymentStart,
    UPIQRResponse,
)
from app.schemas.common import Page

router = APIRouter(
    prefix="/billing",
    tags=["Billing & Payments"],
    dependencies=[Depends(get_current_user)],
)

# On a plan without the kitchen display there is no kitchen screen or separate counter: the waiter who took the order may
# also bill it, take payment and close it. Voiding stays with managers and cashiers on every plan.
cashier_roles = require_roles_by_module((Role.OWNER, Role.MANAGER, Role.CASHIER), module=ModuleKey.KDS, also_without=(Role.WAITER,))
qr_roles = require_roles_by_module((Role.OWNER, Role.MANAGER, Role.CASHIER, Role.CAPTAIN), module=ModuleKey.KDS, also_without=(Role.WAITER,))
void_roles = require_roles(Role.OWNER, Role.MANAGER, Role.CASHIER)


@router.get("/bills", response_model=Page[BillRead])
async def list_bills(
    branch_id: Optional[uuid.UUID] = None,
    status: Optional[BillStatus] = None,
    business_date: Optional[date] = None,
    order_id: Optional[uuid.UUID] = None,
    params: PageParams = Depends(page_params),
    user: CurrentUser = Depends(cashier_roles),
    db: AsyncSession = Depends(get_db),
):
    """List bills for a branch."""
    items, total = await service.list_bills(db, user, params, branch_id, status, business_date, order_id)
    return Page(items=items, total=total, limit=params.limit, offset=params.offset)


@router.post("/bills", response_model=BillRead, status_code=status.HTTP_201_CREATED)
async def generate_bill(
    payload: BillCreate,
    background: BackgroundTasks,
    user: CurrentUser = Depends(cashier_roles),
    guard: IdempotencyGuard = Depends(idempotent("bills")),
    db: AsyncSession = Depends(get_db),
    redis_client: aioredis.Redis = Depends(get_redis),
):
    """
    Generate the bill for an order with per-line GST split into CGST/SGST.
    Discounts above the configured threshold need manager approval. Requires X-Idempotency-Key.
    """
    cached = await guard.begin(payload)
    if cached:
        return cached.as_response()
    try:
        bill = await service.generate_bill(db, user, payload, background, redis_client)
    except Exception:
        await guard.release()
        raise
    return await guard.complete(status.HTTP_201_CREATED, BillRead.model_validate(bill))


@router.get("/bills/{bill_id}", response_model=BillRead)
async def get_bill(bill_id: uuid.UUID, user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Retrieve bill details by ID."""
    return await service.get_bill(db, user, bill_id)


@router.post("/bills/{bill_id}/payments", response_model=PaymentResult)
async def record_payment(
    bill_id: uuid.UUID,
    payload: PaymentCreate,
    background: BackgroundTasks,
    user: CurrentUser = Depends(cashier_roles),
    guard: IdempotencyGuard = Depends(idempotent("payments")),
    db: AsyncSession = Depends(get_db),
):
    """Record a tender payment (cash, card, UPI) towards bill settlement. Requires X-Idempotency-Key."""
    cached = await guard.begin({"bill_id": str(bill_id), **payload.model_dump(mode="json")})
    if cached:
        return cached.as_response()
    try:
        payment, bill, change = await service.record_payment(db, user, bill_id, payload, guard.key, background)
    except Exception:
        await guard.release()
        raise
    result = PaymentResult(payment=PaymentRead.model_validate(payment), bill=BillRead.model_validate(bill),
                           change_due=change)
    return await guard.complete(status.HTTP_200_OK, result)


@router.post("/bills/{bill_id}/split-payments", response_model=BillRead)
async def process_split_payment(
    bill_id: uuid.UUID,
    payload: SplitPaymentRequest,
    background: BackgroundTasks,
    user: CurrentUser = Depends(cashier_roles),
    guard: IdempotencyGuard = Depends(idempotent("payments")),
    db: AsyncSession = Depends(get_db),
):
    """Settle a bill across multiple split tenders atomically. Requires X-Idempotency-Key."""
    cached = await guard.begin({"bill_id": str(bill_id), **payload.model_dump(mode="json")})
    if cached:
        return cached.as_response()
    try:
        bill = await service.process_split_payment(db, user, bill_id, payload.payments, background)
    except Exception:
        await guard.release()
        raise
    return await guard.complete(status.HTTP_200_OK, BillRead.model_validate(bill))


@router.get("/bills/{bill_id}/upi-qr", response_model=UPIQRResponse)
async def generate_upi_qr(
    bill_id: uuid.UUID,
    user: CurrentUser = Depends(qr_roles),
    db: AsyncSession = Depends(get_db),
):
    """Generate a UPI intent string for the remaining balance (the client renders the QR)."""
    return await service.upi_payment_request(db, user, bill_id)


@router.post("/bills/{bill_id}/void", response_model=BillRead)
async def void_bill(
    bill_id: uuid.UUID,
    payload: BillVoidRequest,
    background: BackgroundTasks,
    user: CurrentUser = Depends(void_roles),
    db: AsyncSession = Depends(get_db),
    redis_client: aioredis.Redis = Depends(get_redis),
):
    """Void an unpaid bill (manager approval required). The bill number stays in the series."""
    return await service.void_bill(db, user, bill_id, payload, background, redis_client)


# --- card/UPI terminal payments ------------------------------------------------------------------------------------------------


def _result(payment, bill) -> PaymentResult:
    return PaymentResult(payment=PaymentRead.model_validate(payment), bill=BillRead.model_validate(bill))


@router.post("/bills/{bill_id}/terminal-payments", response_model=PaymentResult, status_code=status.HTTP_201_CREATED)
async def start_terminal_payment(
    bill_id: uuid.UUID,
    payload: TerminalPaymentStart,
    user: CurrentUser = Depends(cashier_roles),
    guard: IdempotencyGuard = Depends(idempotent("terminal-payments")),
    db: AsyncSession = Depends(get_db),
):
    """
    Start a card/UPI payment on a terminal (Pine Labs). The payment is saved as pending, with the `billing_ref_no` to give the
    terminal; it counts towards the bill once completed as approved. Requires X-Idempotency-Key.
    """
    cached = await guard.begin({"bill_id": str(bill_id), **payload.model_dump(mode="json")})
    if cached:
        return cached.as_response()
    try:
        payment, bill = await service.start_terminal_payment(db, user, bill_id, payload, guard.key)
    except Exception:
        await guard.release()
        raise
    return await guard.complete(status.HTTP_201_CREATED, _result(payment, bill))


@router.post("/payments/{payment_id}/complete", response_model=PaymentResult)
async def complete_terminal_payment(
    payment_id: uuid.UUID,
    payload: TerminalPaymentComplete,
    background: BackgroundTasks,
    user: CurrentUser = Depends(cashier_roles),
    db: AsyncSession = Depends(get_db),
):
    """Record the terminal's answer for a pending payment. Sending the same answer again is safe."""
    payment, bill = await service.complete_terminal_payment(db, user, payment_id, payload, background)
    return _result(payment, bill)


@router.post("/payments/{payment_id}/reverse", response_model=PaymentResult)
async def reverse_terminal_payment(
    payment_id: uuid.UUID,
    payload: PaymentReverse,
    user: CurrentUser = Depends(void_roles),
    db: AsyncSession = Depends(get_db),
    redis_client: aioredis.Redis = Depends(get_redis),
):
    """A terminal payment was voided on the device: stop counting it and reopen the bill. Manager approval; audited."""
    payment, bill = await service.reverse_terminal_payment(db, user, payment_id, payload, redis_client)
    return _result(payment, bill)


# --- printed customer bill ---------------------------------------------------------------------------------------------------


class ReceiptFormat(StrEnum):
    JSON = "json"
    TEXT = "text"
    HTML = "html"


@router.get("/bills/{bill_id}/receipt", response_model=ReceiptRead,
            responses={200: {"content": {"text/plain": {}, "text/html": {}}}})
async def get_receipt(
    bill_id: uuid.UUID,
    format: ReceiptFormat = ReceiptFormat.JSON,
    width: int = Query(80, description="Paper width in mm: 58 or 80"),
    user: CurrentUser = Depends(cashier_roles),
    db: AsyncSession = Depends(get_db),
):
    """The customer bill as print rows (line printers such as the Pine Labs device), plain text, or a printable HTML page."""
    if width not in COLUMNS:
        raise ValidationFailed("Paper width must be 58 or 80 mm", code="invalid_width")
    receipt = await service.build_receipt(db, user, bill_id)
    if format == ReceiptFormat.TEXT:
        return Response(receipts.render_text(receipt, width), media_type="text/plain; charset=utf-8", headers={"Cache-Control": "no-store"})
    if format == ReceiptFormat.HTML:
        return Response(receipts.render_html(receipt, width), media_type="text/html; charset=utf-8", headers={
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'self'",
            "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})
    return ReceiptRead(bill_number=receipt.bill_number, paper_mm=width,
                       rows=[PrintRowRead(style=st, align=al, text=tx) for st, al, tx in receipts.receipt_rows(receipt, width)],
                       text=receipts.render_text(receipt, width))
