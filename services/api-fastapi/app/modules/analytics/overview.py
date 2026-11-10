"""
The owner's business overview: sales over a period compared with the period before it, and where the money came from (days,
hours, weekdays, categories, items, staff, order types and payment methods). Counts paid bills by their business date, like the
Z-report, so the numbers agree with it.
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Dict, List, Optional, Sequence

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import business_date
from app.core.enums import AuditAction, BillStatus, OrderItemStatus, PaymentMode, PaymentStatus
from app.core.lookups import get_or_404
from app.core.money import ZERO, q
from app.models.audit import AuditLog
from app.models.billing import Bill, Payment
from app.models.catalog import Category, MenuItem
from app.models.floor import Branch
from app.models.orders import Order, OrderItem
from app.models.staff import StaffUser
from app.schemas.analytics import (
    NamedAmount,
    OverviewResponse,
    OverviewTotals,
    PaymentSplit,
    SeriesPoint,
    TopItem,
)


class Period(StrEnum):
    TODAY = "today"
    YESTERDAY = "yesterday"
    LAST_7 = "7d"
    LAST_30 = "30d"
    LAST_90 = "90d"


_DAYS = {Period.TODAY: 1, Period.YESTERDAY: 1, Period.LAST_7: 7, Period.LAST_30: 30, Period.LAST_90: 90}
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_LOSS_ACTIONS = (AuditAction.KOT_VOID.value, AuditAction.ORDER_ITEM_CANCEL.value, AuditAction.ORDER_CANCEL.value,
                 AuditAction.BILL_VOID.value)
_ORDER_TYPE_LABELS = {"dine_in": "Dine-in", "takeaway": "Takeaway", "delivery": "Delivery", "counter_sale": "Counter sale"}


def window(period: Period, today: date) -> tuple[date, date]:
    end = today - timedelta(days=1) if period == Period.YESTERDAY else today
    return end - timedelta(days=_DAYS[period] - 1), end


async def _totals(db: AsyncSession, branch_ids: Sequence[uuid.UUID], start: date, end: date) -> OverviewTotals:
    paid = and_(Bill.branch_id.in_(branch_ids), Bill.business_date.between(start, end), Bill.status == BillStatus.PAID.value)
    net, gross, discount, cgst, sgst, bills = (await db.execute(
        select(func.coalesce(func.sum(Bill.grand_total), 0), func.coalesce(func.sum(Bill.subtotal), 0),
               func.coalesce(func.sum(Bill.discount_amount), 0), func.coalesce(func.sum(Bill.tax_cgst), 0),
               func.coalesce(func.sum(Bill.tax_sgst), 0), func.count(Bill.id)).where(paid)
    )).one()
    guests = await db.scalar(
        select(func.coalesce(func.sum(Order.guest_count), 0)).join(Bill, Bill.order_id == Order.id)
        .where(paid, Order.order_type == "dine_in")
    ) or 0
    losses, loss_count = (await db.execute(
        select(func.coalesce(func.sum(AuditLog.amount), 0), func.count(AuditLog.id)).where(
            AuditLog.branch_id.in_(branch_ids), AuditLog.action_type.in_(_LOSS_ACTIONS),
            func.date(AuditLog.created_at).between(start, end))
    )).one()
    net = q(Decimal(net))
    return OverviewTotals(
        net_sales=net, gross_sales=q(Decimal(gross)), discounts=q(Decimal(discount)), tax=q(Decimal(cgst) + Decimal(sgst)),
        bills=bills, average_bill=q(net / bills) if bills else ZERO, guests=int(guests),
        cancellations=loss_count, cancelled_amount=q(Decimal(losses)),
    )


def _change(now: Decimal, before: Decimal) -> Optional[float]:
    """Percent change, or None when there is nothing to compare with."""
    if before == 0:
        return None
    return round(float((now - before) / before * 100), 1)


async def overview(db: AsyncSession, branch_ids: List[uuid.UUID], period: Period) -> OverviewResponse:
    branch = await get_or_404(db, Branch, branch_ids[0])
    tz = branch.timezone
    start, end = window(period, business_date(tz))
    days = (end - start).days + 1
    prev_start, prev_end = start - timedelta(days=days), start - timedelta(days=1)

    totals = await _totals(db, branch_ids, start, end)
    previous = await _totals(db, branch_ids, prev_start, prev_end)
    changes = {
        "net_sales": _change(totals.net_sales, previous.net_sales),
        "bills": _change(Decimal(totals.bills), Decimal(previous.bills)),
        "average_bill": _change(totals.average_bill, previous.average_bill),
    }

    paid = and_(Bill.branch_id.in_(branch_ids), Bill.business_date.between(start, end), Bill.status == BillStatus.PAID.value)
    local_hour = func.extract("hour", func.timezone(tz, Bill.settled_at))

    # The trend: hour by hour for one day, else day by day (every day shown, quiet days as zero).
    if days == 1:
        rows = dict(((int(h), (s, n)) for h, s, n in (await db.execute(
            select(local_hour, func.sum(Bill.grand_total), func.count(Bill.id)).where(paid).group_by(local_hour))).all()))
        trend = [SeriesPoint(label=f"{h:02d}:00", sales=q(Decimal(rows[h][0])), bills=rows[h][1]) for h in sorted(rows)]
    else:
        rows = {d: (s, n) for d, s, n in (await db.execute(
            select(Bill.business_date, func.sum(Bill.grand_total), func.count(Bill.id)).where(paid)
            .group_by(Bill.business_date))).all()}
        trend = []
        for i in range(days):
            d = start + timedelta(days=i)
            s, n = rows.get(d, (0, 0))
            trend.append(SeriesPoint(label=d.isoformat(), sales=q(Decimal(s)), bills=n))

    # When the money comes in: by hour of day over the whole period, and by weekday for a week or more.
    hour_rows = dict(((int(h), (s, n)) for h, s, n in (await db.execute(
        select(local_hour, func.sum(Bill.grand_total), func.count(Bill.id)).where(paid).group_by(local_hour))).all()))
    by_hour = [SeriesPoint(label=f"{h:02d}:00", sales=q(Decimal(hour_rows[h][0])), bills=hour_rows[h][1]) for h in sorted(hour_rows)]
    by_weekday: List[SeriesPoint] = []
    if days >= 7:
        weekday = func.extract("isodow", Bill.business_date)
        wd_rows = dict(((int(w), (s, n)) for w, s, n in (await db.execute(
            select(weekday, func.sum(Bill.grand_total), func.count(Bill.id)).where(paid).group_by(weekday))).all()))
        by_weekday = [SeriesPoint(label=_WEEKDAYS[w - 1], sales=q(Decimal(wd_rows.get(w, (0, 0))[0])), bills=wd_rows.get(w, (0, 0))[1])
                      for w in range(1, 8)]

    paid_orders = select(Bill.order_id).where(paid)
    live_lines = and_(OrderItem.order_id.in_(paid_orders), OrderItem.status != OrderItemStatus.CANCELLED.value)

    categories = [NamedAmount(name=name or "Other", amount=q(Decimal(total)), count=int(qty or 0)) for name, total, qty in (await db.execute(
        select(Category.name, func.sum(OrderItem.line_total), func.count(OrderItem.id))
        .join(MenuItem, OrderItem.item_id == MenuItem.id).join(Category, MenuItem.category_id == Category.id)
        .where(live_lines).group_by(Category.name).order_by(func.sum(OrderItem.line_total).desc()).limit(12)
    )).all()]

    top_items = [TopItem(name=name, category=category, quantity=Decimal(qty), total=q(Decimal(total))) for name, category, qty, total in (await db.execute(
        select(OrderItem.name_snapshot, Category.name, func.sum(OrderItem.quantity), func.sum(OrderItem.line_total))
        .join(MenuItem, OrderItem.item_id == MenuItem.id).join(Category, MenuItem.category_id == Category.id)
        .where(live_lines).group_by(OrderItem.name_snapshot, Category.name)
        .order_by(func.sum(OrderItem.line_total).desc()).limit(10)
    )).all()]

    staff = [NamedAmount(name=name or "Unassigned", amount=q(Decimal(total)), count=n) for name, total, n in (await db.execute(
        select(StaffUser.full_name, func.sum(Bill.grand_total), func.count(Bill.id))
        .select_from(Bill).join(Order, Bill.order_id == Order.id).outerjoin(StaffUser, StaffUser.id == Order.waiter_id)
        .where(paid).group_by(StaffUser.full_name).order_by(func.sum(Bill.grand_total).desc()).limit(10)
    )).all()]

    order_types = [NamedAmount(name=_ORDER_TYPE_LABELS.get(t, t), amount=q(Decimal(total)), count=n) for t, total, n in (await db.execute(
        select(Order.order_type, func.sum(Bill.grand_total), func.count(Bill.id))
        .select_from(Bill).join(Order, Bill.order_id == Order.id).where(paid)
        .group_by(Order.order_type).order_by(func.sum(Bill.grand_total).desc())
    )).all()]

    tenders: Dict[str, Decimal] = dict((await db.execute(
        select(Payment.payment_mode, func.coalesce(func.sum(Payment.amount), 0)).join(Bill, Payment.bill_id == Bill.id)
        .where(paid, Payment.status == PaymentStatus.SUCCESS.value).group_by(Payment.payment_mode)
    )).all())
    split = {mode.value: q(Decimal(tenders.get(mode.value, 0))) for mode in PaymentMode}

    return OverviewResponse(
        period=period.value, start=start, end=end, previous_start=prev_start, previous_end=prev_end,
        branches=len(branch_ids), totals=totals, previous=previous, changes=changes, trend=trend, by_hour=by_hour,
        by_weekday=by_weekday, categories=categories, top_items=top_items, staff=staff, order_types=order_types,
        payments=PaymentSplit(**split, total=q(sum(split.values(), ZERO))),
    )
