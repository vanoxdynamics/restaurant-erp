import uuid
from typing import List

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, get_current_user, require_roles
from app.core.database import get_db
from app.core.enums import Role
from app.modules.floor import service
from app.schemas.floor import (
    BranchCreate,
    BranchRead,
    DiningTableCreate,
    DiningTableRead,
    FloorCreate,
    FloorRead,
    TableMergeRequest,
    TableTransferRequest,
)

router = APIRouter(
    prefix="/floor",
    tags=["Floor & Table Operations"],
    dependencies=[Depends(get_current_user)],
)

owners = require_roles(Role.OWNER)
managers = require_roles(Role.OWNER, Role.MANAGER)
transfer_roles = require_roles(Role.OWNER, Role.MANAGER, Role.CAPTAIN, Role.WAITER)
merge_roles = require_roles(Role.OWNER, Role.MANAGER, Role.CAPTAIN)


@router.get("/branches", response_model=List[BranchRead])
async def list_branches(user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """List restaurant branches visible to the caller."""
    return await service.list_branches(db, user)


@router.post("/branches", response_model=BranchRead, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(owners)])
async def create_branch(
    payload: BranchCreate,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new branch in the caller's organization."""
    return await service.create_branch(db, user, payload)


@router.get("/branches/{branch_id}/floors", response_model=List[FloorRead])
async def list_floors_and_tables(
    branch_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all floors and dining tables for a given branch."""
    return await service.list_floors_and_tables(db, user, branch_id)


@router.post("/branches/{branch_id}/floors", response_model=FloorRead, status_code=status.HTTP_201_CREATED)
async def create_floor(
    branch_id: uuid.UUID,
    payload: FloorCreate,
    user: CurrentUser = Depends(managers),
    db: AsyncSession = Depends(get_db),
):
    """Add a floor to a branch."""
    return await service.create_floor(db, user, branch_id, payload)


@router.post("/floors/{floor_id}/tables", response_model=DiningTableRead, status_code=status.HTTP_201_CREATED)
async def create_table(
    floor_id: uuid.UUID,
    payload: DiningTableCreate,
    user: CurrentUser = Depends(managers),
    db: AsyncSession = Depends(get_db),
):
    """Add a dining table to a floor."""
    return await service.create_table(db, user, floor_id, payload)


@router.post("/tables/transfer", response_model=DiningTableRead)
async def transfer_table(
    payload: TableTransferRequest,
    user: CurrentUser = Depends(transfer_roles),
    db: AsyncSession = Depends(get_db),
):
    """Transfer orders and occupancy from one table to another."""
    return await service.transfer_table(db, user, payload)


@router.post("/tables/merge", response_model=DiningTableRead)
async def merge_tables(
    payload: TableMergeRequest,
    user: CurrentUser = Depends(merge_roles),
    db: AsyncSession = Depends(get_db),
):
    """Merge multiple dining tables for large party guest seating."""
    return await service.merge_tables(db, user, payload)


@router.post("/tables/{table_id}/unmerge", response_model=DiningTableRead)
async def unmerge_table(
    table_id: uuid.UUID,
    user: CurrentUser = Depends(merge_roles),
    db: AsyncSession = Depends(get_db),
):
    """Release tables previously merged into this one."""
    return await service.unmerge_table(db, user, table_id)
