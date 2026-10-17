import uuid
from typing import List
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.schemas.staff import (
    BlindDropRequest,
    PINAuthRequest,
    PasswordLoginRequest,
    ShiftCloseRequest,
    ShiftOpenRequest,
    ShiftRead,
    StaffUserCreate,
    StaffUserRead,
    TokenResponse,
)

router = APIRouter(prefix="/staff", tags=["Staff, RBAC & Shifts"])


@router.post("/auth/pin", response_model=TokenResponse)
async def login_with_pin(payload: PINAuthRequest, db: AsyncSession = Depends(get_db)):
    """Fast PIN-based authentication for captain handhelds and POS terminals."""
    raise NotImplementedError("Feature implementation pending")


@router.post("/auth/login", response_model=TokenResponse)
async def login_with_password(payload: PasswordLoginRequest, db: AsyncSession = Depends(get_db)):
    """Standard username/password authentication for managers and owners."""
    raise NotImplementedError("Feature implementation pending")


@router.get("", response_model=List[StaffUserRead])
async def list_staff(db: AsyncSession = Depends(get_db)):
    """List staff members and their RBAC roles."""
    return []


@router.post("", response_model=StaffUserRead, status_code=status.HTTP_201_CREATED)
async def create_staff(payload: StaffUserCreate, db: AsyncSession = Depends(get_db)):
    """Register a new staff member."""
    raise NotImplementedError("Feature implementation pending")


@router.post("/shifts/open", response_model=ShiftRead, status_code=status.HTTP_201_CREATED)
async def open_shift(payload: ShiftOpenRequest, db: AsyncSession = Depends(get_db)):
    """Open a new cashier/waiter cash register shift."""
    raise NotImplementedError("Feature implementation pending")


@router.post("/shifts/close", response_model=ShiftRead)
async def close_shift(payload: ShiftCloseRequest, db: AsyncSession = Depends(get_db)):
    """Close shift with blind drop cash reconciliation."""
    raise NotImplementedError("Feature implementation pending")
