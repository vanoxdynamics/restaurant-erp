import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict

from app.core.enums import AuditAction
from app.schemas.common import QuantityOut


class LiveKPIsResponse(BaseModel):
    branch_id: uuid.UUID
    active_tables_count: int
    vacant_tables_count: int
    open_orders_count: int
    pending_kots_count: int
    total_sales_today: Decimal
    average_ticket_size: Decimal
    timestamp: datetime


class ZReportResponse(BaseModel):
    branch_id: uuid.UUID
    report_date: date
    gross_sales: Decimal
    net_sales: Decimal
    total_tax_cgst: Decimal
    total_tax_sgst: Decimal
    total_discounts: Decimal
    cash_collected: Decimal
    card_collected: Decimal
    upi_collected: Decimal
    voided_orders_count: int
    voided_bills_count: int
    total_bills_count: int
    generated_at: datetime


class AuditLogRead(BaseModel):
    id: uuid.UUID
    branch_id: uuid.UUID
    action_type: AuditAction
    actor_id: Optional[uuid.UUID] = None
    actor_name: Optional[str] = None
    approver_id: Optional[uuid.UUID] = None
    approver_name: Optional[str] = None
    entity_type: str
    entity_id: Optional[uuid.UUID] = None
    amount: Optional[Decimal] = None
    details: Dict[str, Any]
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class HourlySales(BaseModel):
    hour: str  # "13:00", branch-local
    sales: Decimal
    bills: int


class TopItem(BaseModel):
    name: str
    category: Optional[str] = None
    quantity: QuantityOut
    total: Decimal


class PaymentSplit(BaseModel):
    cash: Decimal
    card: Decimal
    upi: Decimal
    total: Decimal


class SalesBreakdownResponse(BaseModel):
    branch_id: uuid.UUID
    business_date: date
    hourly: List[HourlySales]
    top_items: List[TopItem]
    payments: PaymentSplit


class SeriesPoint(BaseModel):
    label: str  # an ISO date, "13:00", or a weekday
    sales: Decimal
    bills: int


class NamedAmount(BaseModel):
    name: str
    amount: Decimal
    count: int  # bills, or item lines for categories


class OverviewTotals(BaseModel):
    net_sales: Decimal  # paid bills, after discount, with tax
    gross_sales: Decimal  # before discount and tax
    discounts: Decimal
    tax: Decimal
    bills: int
    average_bill: Decimal
    guests: int  # dine-in guests
    cancellations: int  # voided KOTs, cancelled items and orders, voided bills
    cancelled_amount: Decimal


class OverviewResponse(BaseModel):
    """A period of business with the same-length period before it for comparison."""
    period: str
    start: date
    end: date
    previous_start: date
    previous_end: date
    branches: int
    totals: OverviewTotals
    previous: OverviewTotals
    changes: Dict[str, Optional[float]]  # percent change per headline figure; null when there was nothing before
    trend: List[SeriesPoint]  # by hour for a single day, else by day
    by_hour: List[SeriesPoint]
    by_weekday: List[SeriesPoint]
    categories: List[NamedAmount]
    top_items: List[TopItem]
    staff: List[NamedAmount]
    order_types: List[NamedAmount]
    payments: PaymentSplit
