import uuid
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, get_current_user, require_roles, resolve_branch_id
from app.core.database import get_db
from app.core.enums import Role, VoidRequestStatus
from app.core.pagination import PageParams, page_params
from app.modules.approvals import service
from app.schemas.approvals import VoidRequestCreate, VoidRequestRead, VoidRequestReview
from app.schemas.common import Page

router = APIRouter(tags=["Approvals"], dependencies=[Depends(get_current_user)])

requesters = require_roles(Role.OWNER, Role.MANAGER, Role.CAPTAIN, Role.WAITER, Role.CASHIER, Role.KITCHEN)
reviewers = require_roles(Role.OWNER, Role.MANAGER, password_session=True)


@router.post("/kots/{kot_id}/void-requests", response_model=VoidRequestRead, status_code=status.HTTP_201_CREATED)
async def create_void_request(
    kot_id: uuid.UUID,
    payload: VoidRequestCreate,
    background: BackgroundTasks,
    user: CurrentUser = Depends(requesters),
    db: AsyncSession = Depends(get_db),
):
    """Ask a manager to approve voiding this KOT remotely."""
    return await service.create_void_request(db, user, kot_id, payload.reason, background)


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
