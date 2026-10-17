import uuid
from typing import List
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
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

router = APIRouter(prefix="/floor", tags=["Floor & Table Operations"])


@router.get("/branches", response_model=List[BranchRead])
async def list_branches(db: AsyncSession = Depends(get_db)):
    """List restaurant branches."""
    return []


@router.post("/branches", response_model=BranchRead, status_code=status.HTTP_201_CREATED)
async def create_branch(payload: BranchCreate, db: AsyncSession = Depends(get_db)):
    """Create a new branch."""
    raise NotImplementedError("Feature implementation pending")


@router.get("/branches/{branch_id}/floors", response_model=List[FloorRead])
async def list_floors_and_tables(branch_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """List all floors and dining tables for a given branch."""
    return []


@router.post("/tables/transfer", response_model=DiningTableRead)
async def transfer_table(payload: TableTransferRequest, db: AsyncSession = Depends(get_db)):
    """Transfer orders and occupancy from one table to another."""
    raise NotImplementedError("Feature implementation pending")


@router.post("/tables/merge", response_model=DiningTableRead)
async def merge_tables(payload: TableMergeRequest, db: AsyncSession = Depends(get_db)):
    """Merge multiple dining tables for large party guest seating."""
    raise NotImplementedError("Feature implementation pending")
