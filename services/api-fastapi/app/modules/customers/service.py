"""
Customers: the people and businesses a shop sells to.

A customer belongs to the business (not a branch) and is found by phone number or name. Putting a customer on an order is what
links them to its bill: the bill prints their name, phone and GSTIN, and their visits and spend are the paid bills of their
orders. Nothing here changes money; a sale never needs a customer.
"""

import re
import uuid
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser
from app.core.enums import BillStatus
from app.core.errors import Conflict, Forbidden, NotFound
from app.core.pagination import PageParams, paginate
from app.models.billing import Bill
from app.models.customers import Customer
from app.models.orders import Order
from app.schemas.customers import CustomerCreate, CustomerUpdate

FIELDS = ("id", "name", "phone", "email", "gstin", "address", "notes", "is_active", "created_at", "updated_at")


def _stats():
    """Paid bills per customer: how often they came, what they spent and when they were last in."""
    return (
        select(
            Order.customer_id.label("customer_id"),
            func.count(Bill.id).label("visits"),
            func.coalesce(func.sum(Bill.grand_total), 0).label("total_spent"),
            func.max(Bill.created_at).label("last_visit_at"),
        )
        .join(Bill, Bill.order_id == Order.id)
        .where(Order.customer_id.is_not(None), Bill.status == BillStatus.PAID.value)
        .group_by(Order.customer_id)
        .subquery()
    )


def _row(customer: Customer, visits: Optional[int], spent: Optional[Decimal], last_visit: Any) -> Dict[str, Any]:
    return {
        **{f: getattr(customer, f) for f in FIELDS},
        "visits": visits or 0, "total_spent": spent or Decimal("0.00"), "last_visit_at": last_visit,
    }


def _like(term: str) -> str:
    return "%" + re.sub(r"([\\%_])", r"\\\1", term) + "%"


async def find(db: AsyncSession, user: CurrentUser, customer_id: uuid.UUID) -> Customer:
    """A customer of the caller's own business, or 404 (another business's customer does not exist as far as they can tell)."""
    customer = await db.scalar(
        select(Customer).where(Customer.id == customer_id, Customer.organization_id == user.organization_id)
    )
    if customer is None:
        raise NotFound("Customer not found")
    return customer


async def read(db: AsyncSession, user: CurrentUser, customer_id: uuid.UUID) -> Dict[str, Any]:
    stats = _stats()
    row = (await db.execute(
        select(Customer, stats.c.visits, stats.c.total_spent, stats.c.last_visit_at)
        .outerjoin(stats, stats.c.customer_id == Customer.id)
        .where(Customer.id == customer_id, Customer.organization_id == user.organization_id)
    )).first()
    if row is None:
        raise NotFound("Customer not found")
    return _row(*row)


async def list_customers(
    db: AsyncSession, user: CurrentUser, params: PageParams, search: Optional[str], include_inactive: bool,
) -> Tuple[List[Dict[str, Any]], int]:
    """Customers by name. `search` matches part of the name, or part of the phone number when it is typed as digits."""
    where = [Customer.organization_id == user.organization_id]
    if not include_inactive:
        where.append(Customer.is_active.is_(True))
    term = (search or "").strip()
    if term:
        digits = re.sub(r"[\s+().-]", "", term)
        if digits.isdigit():
            where.append(or_(Customer.phone.like(_like(digits[-10:] if len(digits) > 10 else digits), escape="\\"),
                             Customer.name.ilike(_like(term), escape="\\")))
        else:
            where.append(or_(Customer.name.ilike(_like(term), escape="\\"), Customer.gstin == term.upper()))

    total = await db.scalar(select(func.count()).select_from(Customer).where(*where))
    stats = _stats()
    rows = (await db.execute(
        select(Customer, stats.c.visits, stats.c.total_spent, stats.c.last_visit_at)
        .outerjoin(stats, stats.c.customer_id == Customer.id)
        .where(*where).order_by(func.lower(Customer.name), Customer.created_at)
        .limit(params.limit).offset(params.offset)
    )).all()
    return [_row(*r) for r in rows], int(total or 0)


async def _refuse_duplicate_phone(db: AsyncSession, org_id: uuid.UUID, phone: Optional[str], except_id: Optional[uuid.UUID] = None) -> None:
    if not phone:
        return
    stmt = select(Customer).where(Customer.organization_id == org_id, Customer.phone == phone)
    if except_id is not None:
        stmt = stmt.where(Customer.id != except_id)
    other = await db.scalar(stmt)
    if other is not None:
        raise Conflict("A customer with this phone number already exists", code="customer_phone_taken",
                       details={"customer_id": str(other.id), "name": other.name, "is_active": other.is_active})


async def create(db: AsyncSession, user: CurrentUser, payload: CustomerCreate) -> Dict[str, Any]:
    await _refuse_duplicate_phone(db, user.organization_id, payload.phone)
    customer = Customer(organization_id=user.organization_id, **payload.model_dump())
    db.add(customer)
    await db.commit()
    return await read(db, user, customer.id)


async def update(db: AsyncSession, user: CurrentUser, customer_id: uuid.UUID, payload: CustomerUpdate) -> Dict[str, Any]:
    customer = await find(db, user, customer_id)
    changes = payload.model_dump(exclude_unset=True)
    if "name" in changes and changes["name"] is None:
        changes.pop("name")
    if "is_active" in changes:
        if changes["is_active"] is None:
            changes.pop("is_active")
        elif changes["is_active"] != customer.is_active and not user.is_manager:
            raise Forbidden("Only an owner or manager can hide or restore a customer")
    if changes.get("phone") and changes["phone"] != customer.phone:
        await _refuse_duplicate_phone(db, user.organization_id, changes["phone"], except_id=customer.id)
    for field, value in changes.items():
        setattr(customer, field, value)
    await db.commit()
    return await read(db, user, customer.id)


async def list_bills(db: AsyncSession, user: CurrentUser, customer_id: uuid.UUID, params: PageParams) -> Tuple[List[Bill], int]:
    """The customer's bills, newest first, across every branch of the business."""
    customer = await find(db, user, customer_id)
    stmt = (
        select(Bill).join(Order, Order.id == Bill.order_id)
        .where(Order.customer_id == customer.id, Bill.status != BillStatus.VOIDED.value)
        .order_by(Bill.created_at.desc())
    )
    return await paginate(db, stmt, params)
