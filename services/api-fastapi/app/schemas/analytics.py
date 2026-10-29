import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict

from app.core.enums import AuditAction


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
    quantity: int
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
