import uuid
from decimal import Decimal
from typing import List, Optional
from datetime import datetime, date
from pydantic import BaseModel, ConfigDict


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
    total_bills_count: int
    generated_at: datetime


class FraudAuditLogRead(BaseModel):
    id: uuid.UUID
    action_type: str  # kot_void, bill_void, high_discount, transfer
    actor_id: uuid.UUID
    actor_name: str
    details: str
    amount: Optional[Decimal] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)
