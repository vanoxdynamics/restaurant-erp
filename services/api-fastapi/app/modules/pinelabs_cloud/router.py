"""
Card/UPI payments through Pine Labs' cloud, for restaurant bills (/billing) and shop sales (/apparel). The payment is started
with the module's usual terminal-payment call (saved as pending); these calls send it to the terminal, check it and cancel it.
"""

import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, get_current_user
from app.core.database import get_db
from app.modules.apparel import sales as apparel_sales
from app.modules.apparel.router import cashiers as apparel_cashiers, _result as apparel_result
from app.modules.billing.router import _result as bill_result, cashier_roles
from app.modules.billing import service as billing_service
from app.modules.pinelabs_cloud import service
from app.schemas.apparel import ApparelPaymentResult
from app.schemas.billing import PaymentResult, TerminalPaymentComplete

router = APIRouter(tags=["Pine Labs cloud"], dependencies=[Depends(get_current_user)])


class CloudConfigRead(BaseModel):
    enabled: bool


class CloudBillPaymentResult(PaymentResult):
    state: str  # pending | approved | declined | unknown
    message: str


class CloudApparelPaymentResult(ApparelPaymentResult):
    state: str
    message: str


@router.get("/billing/terminal-cloud", response_model=CloudConfigRead)
async def cloud_config():
    """Whether this server can send card/UPI payments to terminals through Pine Labs' cloud."""
    return CloudConfigRead(enabled=service.is_enabled())


# --- restaurant bills ---------------------------------------------------------------------------------------------------


def _bill_adapter(db: AsyncSession, user: CurrentUser, payment_id: uuid.UUID, background=None) -> service.Adapter:
    async def load():
        return await billing_service._load_payment_and_bill(db, user, payment_id)

    async def complete(outcome: str, code: Optional[str], details: Dict[str, Any], message: str):
        payload = TerminalPaymentComplete(outcome=outcome, approval_code=code, message=message[:255], source="terminal",
                                          details=details)
        return await billing_service.complete_terminal_payment(db, user, payment_id, payload, background)

    return service.Adapter(load, complete)


def _bill_result(outcome) -> CloudBillPaymentResult:
    payment, bill, state, message = outcome
    base = bill_result(payment, bill)
    return CloudBillPaymentResult(payment=base.payment, bill=base.bill, state=state, message=message)


@router.post("/billing/payments/{payment_id}/cloud/send", response_model=CloudBillPaymentResult)
async def send_bill_payment(payment_id: uuid.UUID, user: CurrentUser = Depends(cashier_roles), db: AsyncSession = Depends(get_db)):
    """Send a pending card/UPI payment to the terminal. Sending again only checks it."""
    return _bill_result(await service.send(db, _bill_adapter(db, user, payment_id)))


@router.post("/billing/payments/{payment_id}/cloud/check", response_model=CloudBillPaymentResult)
async def check_bill_payment(payment_id: uuid.UUID, user: CurrentUser = Depends(cashier_roles), db: AsyncSession = Depends(get_db)):
    """Ask Pine Labs how the payment went; an approved or failed answer is recorded."""
    return _bill_result(await service.check(db, _bill_adapter(db, user, payment_id)))


@router.post("/billing/payments/{payment_id}/cloud/cancel", response_model=CloudBillPaymentResult)
async def cancel_bill_payment(payment_id: uuid.UUID, user: CurrentUser = Depends(cashier_roles), db: AsyncSession = Depends(get_db)):
    """Cancel the payment on the terminal (if the customer already paid, it is recorded as approved)."""
    return _bill_result(await service.cancel(db, _bill_adapter(db, user, payment_id)))


# --- shop sales ---------------------------------------------------------------------------------------------------------


def _sale_adapter(db: AsyncSession, user: CurrentUser, payment_id: uuid.UUID) -> service.Adapter:
    tenders = apparel_sales.tenders

    async def load():
        return await tenders._payment_and_sale(db, user, payment_id)

    async def complete(outcome: str, code: Optional[str], details: Dict[str, Any], message: str):
        return await tenders.complete_terminal(db, user, payment_id, outcome=outcome, approval_code=code, details=details,
                                               source="terminal", message=message[:255])

    return service.Adapter(load, complete)


def _sale_result(outcome) -> CloudApparelPaymentResult:
    payment, sale, state, message = outcome
    base = apparel_result(payment, sale)
    return CloudApparelPaymentResult(payment=base.payment, sale=base.sale, state=state, message=message)


@router.post("/apparel/payments/{payment_id}/cloud/send", response_model=CloudApparelPaymentResult)
async def send_sale_payment(payment_id: uuid.UUID, user: CurrentUser = Depends(apparel_cashiers), db: AsyncSession = Depends(get_db)):
    return _sale_result(await service.send(db, _sale_adapter(db, user, payment_id)))


@router.post("/apparel/payments/{payment_id}/cloud/check", response_model=CloudApparelPaymentResult)
async def check_sale_payment(payment_id: uuid.UUID, user: CurrentUser = Depends(apparel_cashiers), db: AsyncSession = Depends(get_db)):
    return _sale_result(await service.check(db, _sale_adapter(db, user, payment_id)))


@router.post("/apparel/payments/{payment_id}/cloud/cancel", response_model=CloudApparelPaymentResult)
async def cancel_sale_payment(payment_id: uuid.UUID, user: CurrentUser = Depends(apparel_cashiers), db: AsyncSession = Depends(get_db)):
    return _sale_result(await service.cancel(db, _sale_adapter(db, user, payment_id)))
