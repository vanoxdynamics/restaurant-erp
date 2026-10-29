import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import redis.asyncio as aioredis
from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.clock import business_date, utcnow
from app.core.enums import (
    AuditAction,
    BillStatus,
    KOTStatus,
    OrderItemStatus,
    OrderStatus,
    PaymentMode,
    PaymentStatus,
    TableStatus,
)
from app.core.errors import ValidationFailed
from app.core.lookups import get_or_404
from app.core.money import ZERO, q
from app.core.pagination import PageParams
from app.models.audit import AuditLog, DailyReport
from app.models.billing import Bill, Payment
from app.models.floor import Branch, DiningTable, Floor
from app.models.catalog import Category, MenuItem
from app.models.kots import KOT
from app.models.orders import Order, OrderItem
from app.models.staff import StaffUser
from app.schemas.analytics import (
    HourlySales,
    LiveKPIsResponse,
    PaymentSplit,
    SalesBreakdownResponse,
    TopItem,
    ZReportResponse,
)

KPI_CACHE_SECONDS = 10


def _count(stmt: Any) -> Any:
    return select(func.count()).select_from(stmt.subquery()).scalar_subquery()


async def live_kpis(db: AsyncSession, redis_client: Optional[aioredis.Redis], branch_id: uuid.UUID) -> LiveKPIsResponse:
    cache_key = f"kpi:{branch_id}"
    if redis_client is not None:
        cached = await redis_client.get(cache_key)
        if cached:
            return LiveKPIsResponse.model_validate_json(cached)

    branch = await get_or_404(db, Branch, branch_id)
    today = business_date(branch.timezone)
    tables = (
        select(DiningTable.id, DiningTable.status)
        .join(Floor, DiningTable.floor_id == Floor.id)
        .where(Floor.branch_id == branch_id, DiningTable.is_active.is_(True), Floor.is_active.is_(True))
    )
    paid_today = and_(Bill.branch_id == branch_id, Bill.business_date == today, Bill.status == BillStatus.PAID.value)

    row = (await db.execute(select(
        _count(tables.where(DiningTable.status.in_([TableStatus.OCCUPIED.value, TableStatus.BILLED.value]))),
        _count(tables.where(DiningTable.status == TableStatus.VACANT.value)),
        _count(select(Order.id).where(
            Order.branch_id == branch_id,
            Order.status.in_([OrderStatus.ACTIVE.value, OrderStatus.BILLED.value]),
        )),
        # Only tickets for orders still being served; a paid order's unserved tickets aren't kitchen work.
        _count(select(KOT.id).join(Order, KOT.order_id == Order.id).where(
            KOT.branch_id == branch_id,
            KOT.status.in_([KOTStatus.QUEUED.value, KOTStatus.PREPARING.value]),
            Order.status.in_([OrderStatus.ACTIVE.value, OrderStatus.BILLED.value]),
        )),
        select(func.coalesce(func.sum(Bill.grand_total), 0)).where(paid_today).scalar_subquery(),
        _count(select(Bill.id).where(paid_today)),
    ))).one()

    active, vacant, open_orders, pending_kots, sales, paid_count = row
    sales = Decimal(sales)
    result = LiveKPIsResponse(
        branch_id=branch_id,
        active_tables_count=active,
        vacant_tables_count=vacant,
        open_orders_count=open_orders,
        pending_kots_count=pending_kots,
        total_sales_today=q(sales),
        average_ticket_size=q(sales / paid_count) if paid_count else ZERO,
        timestamp=utcnow(),
    )
    if redis_client is not None:
        await redis_client.set(cache_key, result.model_dump_json(), ex=KPI_CACHE_SECONDS)
    return result


async def z_report(db: AsyncSession, branch_id: uuid.UUID, report_date: Optional[date]) -> ZReportResponse:
    branch = await get_or_404(db, Branch, branch_id)
    today = business_date(branch.timezone)
    report_date = report_date or today
    if report_date > today:
        raise ValidationFailed("Report date is in the future", code="future_date")

    if report_date < today:
        archived = await db.scalar(
            select(DailyReport.payload).where(DailyReport.branch_id == branch_id, DailyReport.report_date == report_date)
        )
        if archived:
            return ZReportResponse.model_validate(archived)

    paid = and_(Bill.branch_id == branch_id, Bill.business_date == report_date, Bill.status == BillStatus.PAID.value)
    totals = (await db.execute(
        select(
            func.coalesce(func.sum(Bill.subtotal), 0),
            func.coalesce(func.sum(Bill.discount_amount), 0),
            func.coalesce(func.sum(Bill.tax_cgst), 0),
            func.coalesce(func.sum(Bill.tax_sgst), 0),
            func.count(Bill.id),
        ).where(paid)
    )).one()
    gross, discounts, cgst, sgst, bill_count = (Decimal(totals[0]), Decimal(totals[1]), Decimal(totals[2]),
                                                Decimal(totals[3]), totals[4])

    tenders = dict((await db.execute(
        select(Payment.payment_mode, func.coalesce(func.sum(Payment.amount), 0))
        .join(Bill, Payment.bill_id == Bill.id)
        .where(paid, Payment.status == PaymentStatus.SUCCESS.value)
        .group_by(Payment.payment_mode)
    )).all())

    voided_orders = await db.scalar(select(func.count(Order.id)).where(
        Order.branch_id == branch_id, Order.business_date == report_date, Order.status == OrderStatus.CANCELLED.value,
    ))
    voided_bills = await db.scalar(select(func.count(Bill.id)).where(
        Bill.branch_id == branch_id, Bill.business_date == report_date, Bill.status == BillStatus.VOIDED.value,
    ))

    return ZReportResponse(
        branch_id=branch_id,
        report_date=report_date,
        gross_sales=q(gross),
        net_sales=q(gross - discounts),
        total_tax_cgst=q(cgst),
        total_tax_sgst=q(sgst),
        total_discounts=q(discounts),
        cash_collected=q(Decimal(tenders.get(PaymentMode.CASH.value, 0))),
        card_collected=q(Decimal(tenders.get(PaymentMode.CARD.value, 0))),
        upi_collected=q(Decimal(tenders.get(PaymentMode.UPI.value, 0))),
        voided_orders_count=voided_orders or 0,
        voided_bills_count=voided_bills or 0,
        total_bills_count=bill_count,
        generated_at=utcnow(),
    )


async def archive_z_report(db: AsyncSession, branch_id: uuid.UUID, report_date: date) -> ZReportResponse:
    """Compute and persist the Z-report (upsert, safe to rerun)."""
    report = await z_report(db, branch_id, report_date)
    payload = json.loads(report.model_dump_json())
    stmt = insert(DailyReport).values(branch_id=branch_id, report_date=report_date, payload=payload)
    stmt = stmt.on_conflict_do_update(
        index_elements=[DailyReport.branch_id, DailyReport.report_date],
        set_={"payload": payload, "generated_at": func.now()},
    )
    await db.execute(stmt)
    await db.commit()
    return report


async def list_audit_logs(
    db: AsyncSession,
    branch_id: uuid.UUID,
    params: PageParams,
    action_type: Optional[AuditAction],
    since: Optional[datetime],
    until: Optional[datetime],
) -> Tuple[List[Dict[str, Any]], int]:
    actor = aliased(StaffUser)
    approver = aliased(StaffUser)
    conditions = [AuditLog.branch_id == branch_id]
    if action_type is not None:
        conditions.append(AuditLog.action_type == action_type.value)
    if since is not None:
        conditions.append(AuditLog.created_at >= since)
    if until is not None:
        conditions.append(AuditLog.created_at < until)

    total = await db.scalar(select(func.count(AuditLog.id)).where(*conditions)) or 0
    rows = (await db.execute(
        select(AuditLog, actor.full_name, approver.full_name)
        .outerjoin(actor, AuditLog.actor_id == actor.id)
        .outerjoin(approver, AuditLog.approver_id == approver.id)
        .where(*conditions)
        .order_by(AuditLog.created_at.desc())
        .limit(params.limit)
        .offset(params.offset)
    )).all()
    items = [
        {**{c: getattr(log, c) for c in ("id", "branch_id", "action_type", "actor_id", "approver_id", "entity_type",
                                          "entity_id", "amount", "details", "created_at")},
         "actor_name": actor_name, "approver_name": approver_name}
        for log, actor_name, approver_name in rows
    ]
    return items, total


async def sales_breakdown(db: AsyncSession, branch_id: uuid.UUID) -> SalesBreakdownResponse:
    """Today's paid sales by hour (branch-local), top items by revenue, and payment methods."""
    branch = await get_or_404(db, Branch, branch_id)
    today = business_date(branch.timezone)
    paid = and_(Bill.branch_id == branch_id, Bill.business_date == today, Bill.status == BillStatus.PAID.value)

    local_hour = func.extract("hour", func.timezone(branch.timezone, Bill.settled_at)).label("hour")
    hourly_rows = (await db.execute(
        select(local_hour, func.coalesce(func.sum(Bill.grand_total), 0), func.count(Bill.id))
        .where(paid)
        .group_by(local_hour)
        .order_by(local_hour)
    )).all()
    hourly = [
        HourlySales(hour=f"{int(hour):02d}:00", sales=q(Decimal(total)), bills=count)
        for hour, total, count in hourly_rows
    ]

    paid_orders = select(Bill.order_id).where(paid)
    top_rows = (await db.execute(
        select(
            OrderItem.name_snapshot,
            Category.name,
            func.sum(OrderItem.quantity),
            func.sum(OrderItem.line_total),
        )
        .join(MenuItem, OrderItem.item_id == MenuItem.id)
        .join(Category, MenuItem.category_id == Category.id)
        .where(OrderItem.order_id.in_(paid_orders), OrderItem.status != OrderItemStatus.CANCELLED.value)
        .group_by(OrderItem.name_snapshot, Category.name)
        .order_by(func.sum(OrderItem.line_total).desc())
        .limit(5)
    )).all()
    top_items = [
        TopItem(name=name, category=category, quantity=int(qty), total=q(Decimal(total)))
        for name, category, qty, total in top_rows
    ]

    tenders = dict((await db.execute(
        select(Payment.payment_mode, func.coalesce(func.sum(Payment.amount), 0))
        .join(Bill, Payment.bill_id == Bill.id)
        .where(paid, Payment.status == PaymentStatus.SUCCESS.value)
        .group_by(Payment.payment_mode)
    )).all())
    split = {mode.value: q(Decimal(tenders.get(mode.value, 0))) for mode in PaymentMode}
    payments = PaymentSplit(**split, total=q(sum(split.values(), ZERO)))

    return SalesBreakdownResponse(
        branch_id=branch_id, business_date=today, hourly=hourly, top_items=top_items, payments=payments,
    )
