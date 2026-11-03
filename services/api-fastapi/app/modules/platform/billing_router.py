"""Platform API: invoices, recorded payments and the billing sweep."""

import uuid
from datetime import datetime, timezone
from typing import List, Optional

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.enums import InvoiceStatus, PlatformRole
from app.core.idempotency import IdempotencyGuard
from app.core.redis import get_redis
from app.modules.platform import billing_service
from app.modules.platform.deps import PlatformPrincipal, request_meta, require_platform_roles, require_step_up
from app.modules.platform.orgs_router import idempotent, run_once
from app.schemas.common import Page
from app.schemas.platform_billing import InvoiceRead, PaymentCreate, PaymentRead, PaymentResult, SweepSummary, VoidBody

SUPER, BILLING, SUPPORT, READ = PlatformRole.SUPER_ADMIN, PlatformRole.BILLING, PlatformRole.SUPPORT, PlatformRole.READ_ONLY
any_role = require_platform_roles(SUPER, BILLING, SUPPORT, READ)
money_roles = require_platform_roles(SUPER, BILLING)

router = APIRouter(prefix="/platform", tags=["Platform: Billing"])


@router.get("/invoices", response_model=Page[InvoiceRead], dependencies=[Depends(any_role)])
async def list_invoices(
    org_id: Optional[uuid.UUID] = None,
    invoice_status: Optional[InvoiceStatus] = Query(None, alias="status"),
    overdue: bool = False,
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), db: AsyncSession = Depends(get_db),
):
    items, total = await billing_service.list_invoices(
        db, org_id=org_id, status=invoice_status.value if invoice_status else None, overdue=overdue, limit=limit, offset=offset)
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/invoices/{invoice_id}", response_model=InvoiceRead, dependencies=[Depends(any_role)])
async def get_invoice(invoice_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    return await billing_service.get_invoice(db, invoice_id)


@router.get("/organizations/{org_id}/invoices", response_model=List[InvoiceRead], dependencies=[Depends(any_role)])
async def organization_invoices(org_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    items, _ = await billing_service.list_invoices(db, org_id=org_id, status=None, overdue=False, limit=200, offset=0)
    return items


@router.post("/organizations/{org_id}/invoices/renewal", response_model=InvoiceRead)
async def issue_renewal(org_id: uuid.UUID, request: Request, actor: PlatformPrincipal = Depends(money_roles),
                        db: AsyncSession = Depends(get_db), redis_client: aioredis.Redis = Depends(get_redis)):
    """Issue the next renewal invoice now instead of waiting for the sweep. Returns the existing one if already issued."""
    invoice, _ = await billing_service.issue_renewal_now(db, redis_client, actor_id=actor.admin.id, org_id=org_id, meta=request_meta(request))
    return await billing_service.get_invoice(db, invoice.id)


@router.post("/invoices/{invoice_id}/payments")
async def record_payment(invoice_id: uuid.UUID, body: PaymentCreate, request: Request, actor: PlatformPrincipal = Depends(money_roles),
                         guard: IdempotencyGuard = Depends(idempotent("payment")),
                         db: AsyncSession = Depends(get_db), redis_client: aioredis.Redis = Depends(get_redis)):
    """
    Record money received (bank transfer, UPI, cash, cheque...). Paying a renewal invoice in full extends the subscription
    and applies any scheduled term change. Overpayments become account credit.
    """
    async def work() -> PaymentResult:
        result = await billing_service.record_payment(
            db, redis_client, actor_id=actor.admin.id, invoice_id=invoice_id, amount=body.amount, method=body.method,
            reference=body.reference.strip() if body.reference else None, received_at=body.received_at or datetime.now(timezone.utc),
            note=body.note, meta=request_meta(request))
        return PaymentResult(
            invoice=InvoiceRead(**await billing_service.get_invoice(db, invoice_id)),
            payment=PaymentRead.model_validate(result["payment"]), settled=result["settled"], renewed=result["renewed"],
            credit_added=result["credit_added"])
    return await run_once(guard, {"invoice": str(invoice_id), **body.model_dump(mode="json")}, status.HTTP_201_CREATED, work)


@router.post("/invoices/{invoice_id}/void", response_model=InvoiceRead)
async def void_invoice(invoice_id: uuid.UUID, body: VoidBody, request: Request,
                       actor: PlatformPrincipal = Depends(require_step_up(SUPER, BILLING)),
                       db: AsyncSession = Depends(get_db), redis_client: aioredis.Redis = Depends(get_redis)):
    """Cancel an unpaid invoice (wrong amount, issued by mistake). Disable auto-renew too if billing should stop."""
    await billing_service.void_invoice(db, redis_client, actor_id=actor.admin.id, invoice_id=invoice_id, reason=body.reason, meta=request_meta(request))
    return await billing_service.get_invoice(db, invoice_id)


@router.post("/billing/run", response_model=SweepSummary)
async def run_sweep(_: PlatformPrincipal = Depends(require_step_up(SUPER)), redis_client: aioredis.Redis = Depends(get_redis)):
    """Run the renewal sweep now (it also runs on a schedule from the billing worker)."""
    return SweepSummary(result=await billing_service.run_billing_sweep(redis_client=redis_client))
