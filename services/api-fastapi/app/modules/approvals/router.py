import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, get_current_user, require_roles, resolve_branch_id
from app.core.errors import Forbidden
from app.core.database import get_db
from app.core.enums import Role, VoidRequestStatus
from app.core.pagination import PageParams, page_params
from app.modules.approvals import service
from app.schemas.approvals import VoidRequestCreate, VoidRequestRead, VoidRequestReview
from app.schemas.common import Page

router = APIRouter(tags=["Approvals"], dependencies=[Depends(get_current_user)])

requesters = require_roles(Role.OWNER, Role.MANAGER, Role.CAPTAIN, Role.WAITER, Role.CASHIER, Role.KITCHEN)


async def reviewers(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """
    Kitchen staff review cancellations from their PIN-signed-in device. Managers and owners can
    still step in, but only from a password session (the admin app).
    """
    if user.role == Role.KITCHEN:
        return user
    if user.role in (Role.OWNER, Role.MANAGER):
        if user.auth_method != "pwd":
            raise Forbidden("This action requires a password login", code="password_login_required")
        return user
    raise Forbidden("Your role is not allowed to perform this action")



@router.post("/kots/{kot_id}/void-requests", response_model=VoidRequestRead, status_code=status.HTTP_201_CREATED)
async def create_void_request(
    kot_id: uuid.UUID,
    payload: VoidRequestCreate,
    user: CurrentUser = Depends(requesters),
    db: AsyncSession = Depends(get_db),
):
    """Ask the kitchen to approve cancelling this KOT."""
    return await service.create_void_request(db, user, kot_id, payload.reason)


@router.get("/orders/{order_id}/void-requests", response_model=List[VoidRequestRead])
async def list_order_void_requests(
    order_id: uuid.UUID,
    user: CurrentUser = Depends(requesters),
    db: AsyncSession = Depends(get_db),
):
    """Cancellation requests raised on an order, newest first, so floor staff can see the kitchen's answer."""
    return await service.list_order_void_requests(db, user, order_id)


@router.get("/approvals/void-requests", response_model=Page[VoidRequestRead])
async def list_void_requests(
    branch_id: Optional[uuid.UUID] = None,
    status: Optional[VoidRequestStatus] = None,
    params: PageParams = Depends(page_params),
    user: CurrentUser = Depends(reviewers),
    db: AsyncSession = Depends(get_db),
):
    """Void requests for a branch. Pending requests are listed oldest first."""
    items, total = await service.list_void_requests(db, resolve_branch_id(user, branch_id), status, params)
    return Page(items=items, total=total, limit=params.limit, offset=params.offset)


@router.post("/approvals/void-requests/{request_id}/approve", response_model=VoidRequestRead)
async def approve_void_request(
    request_id: uuid.UUID,
    payload: VoidRequestReview,
    user: CurrentUser = Depends(reviewers),
    db: AsyncSession = Depends(get_db),
):
    """Approve the request and void the KOT."""
    return await service.approve_void_request(db, user, request_id, payload.note)


@router.post("/approvals/void-requests/{request_id}/reject", response_model=VoidRequestRead)
async def reject_void_request(
    request_id: uuid.UUID,
    payload: VoidRequestReview,
    user: CurrentUser = Depends(reviewers),
    db: AsyncSession = Depends(get_db),
):
    """Reject the request; the KOT stays on the order."""
    return await service.reject_void_request(db, user, request_id, payload.note)
