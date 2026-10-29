import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, Optional

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Index, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import AuditAction
from app.models.base import Base, UUIDPrimaryKeyMixin, enum_check


class AuditLog(Base, UUIDPrimaryKeyMixin):
    """Immutable record of high-risk actions (voids, discounts, transfers) for fraud review."""
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_branch_created", "branch_id", "created_at"),
        enum_check("audit_logs", "action_type", AuditAction),
    )

    branch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False)
    action_type: Mapped[str] = mapped_column(String(40), nullable=False)
    actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("staff_users.id"), nullable=True)
    approver_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("staff_users.id"), nullable=True)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    details: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DocumentCounter(Base):
    """Transactional per-branch counters for order / KOT / bill numbering."""
    __tablename__ = "document_counters"

    branch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id"), primary_key=True)
    doc_type: Mapped[str] = mapped_column(String(20), primary_key=True)
    period: Mapped[str] = mapped_column(String(10), primary_key=True)
    value: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")


class DailyReport(Base, UUIDPrimaryKeyMixin):
    """Archived end-of-day Z-report snapshot."""
    __tablename__ = "daily_reports"
    __table_args__ = (Index("uq_daily_reports_branch_date", "branch_id", "report_date", unique=True),)

    branch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False)
    report_date: Mapped[date] = mapped_column(Date, nullable=False)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
