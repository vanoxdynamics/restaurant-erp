import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, get_current_user, require_roles
from app.core.database import get_db
from app.core.enums import Role
from app.core.pagination import PageParams, page_params
from app.modules.customers import service
from app.schemas.common import Page
from app.schemas.customers import CustomerBillRead, CustomerCreate, CustomerRead, CustomerUpdate

router = APIRouter(prefix="/customers", tags=["Customers"], dependencies=[Depends(get_current_user)])

# Whoever takes orders or bills may look a customer up and add one; the kitchen has no use for them.
counter_roles = require_roles(Role.OWNER, Role.MANAGER, Role.CAPTAIN, Role.WAITER, Role.CASHIER)
managers = require_roles(Role.OWNER, Role.MANAGER)


@router.get("", response_model=Page[CustomerRead])
async def list_customers(
    search: Optional[str] = Query(default=None, max_length=100),
    include_inactive: bool = False,
    params: PageParams = Depends(page_params),
    user: CurrentUser = Depends(counter_roles),
    db: AsyncSession = Depends(get_db),
):
    """
    The business's customers by name, with their paid visits and spend. `search` matches part of a name, or part of a phone
    number when it is digits (this is the counter's "type the phone number" lookup).
    """
    items, total = await service.list_customers(db, user, params, search, include_inactive)
    return Page(items=items, total=total, limit=params.limit, offset=params.offset)


@router.post("", response_model=CustomerRead, status_code=status.HTTP_201_CREATED)
async def create_customer(payload: CustomerCreate, user: CurrentUser = Depends(counter_roles), db: AsyncSession = Depends(get_db)):
    """
    Add a customer. A phone number belongs to one customer per business: a second one with the same number is refused with
    `customer_phone_taken`, whose details name the existing customer so the app can offer them instead.
    """
    return await service.create(db, user, payload)


@router.get("/{customer_id}", response_model=CustomerRead)
async def get_customer(customer_id: uuid.UUID, user: CurrentUser = Depends(counter_roles), db: AsyncSession = Depends(get_db)):
    return await service.read(db, user, customer_id)


@router.patch("/{customer_id}", response_model=CustomerRead)
async def update_customer(
    customer_id: uuid.UUID, payload: CustomerUpdate, user: CurrentUser = Depends(counter_roles), db: AsyncSession = Depends(get_db),
):
    """Change a customer's details. Hiding or restoring one (`is_active`) is for owners and managers."""
    return await service.update(db, user, customer_id, payload)


@router.get("/{customer_id}/bills", response_model=Page[CustomerBillRead])
async def list_customer_bills(
    customer_id: uuid.UUID,
    params: PageParams = Depends(page_params),
    user: CurrentUser = Depends(managers),
    db: AsyncSession = Depends(get_db),
):
    """The customer's bills, newest first, across every branch."""
    items, total = await service.list_bills(db, user, customer_id, params)
    return Page(items=items, total=total, limit=params.limit, offset=params.offset)
