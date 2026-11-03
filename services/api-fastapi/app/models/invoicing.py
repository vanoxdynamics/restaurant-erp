import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Numeric, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import BillingTerm, InvoiceKind, InvoiceStatus, PaymentMethod
from app.models.base import Base, UUIDPrimaryKeyMixin, enum_check


class Invoice(Base, UUIDPrimaryKeyMixin):
    """What a subscriber owes the platform. Payments are recorded against it (offline today, gateway later)."""
    __tablename__ = "invoices"
    __table_args__ = (
        enum_check("invoices", "kind", InvoiceKind),
        enum_check("invoices", "status", InvoiceStatus),
        enum_check("invoices", "billing_term", BillingTerm),
        CheckConstraint("amount >= 0 AND credit_applied >= 0 AND amount_due >= 0 AND amount_paid >= 0", name="ck_invoices_amounts"),
        # The sweep may run twice (or on two workers): one live renewal invoice per subscription period.
        Index("uq_invoices_renewal_period", "subscription_id", "period_start", unique=True,
              postgresql_where=text("kind = 'renewal' AND status <> 'void'")),
    )

    number: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), index=True, nullable=False)
    subscription_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("subscriptions.id"), index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(10), default=InvoiceStatus.OPEN.value, nullable=False, index=True)
    billing_term: Mapped[str] = mapped_column(String(20), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)  # before account credit
    credit_applied: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), server_default="0", nullable=False)
    amount_due: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)  # amount - credit_applied
    amount_paid: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), server_default="0", nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    voided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    void_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_by_admin: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)  # NULL = issued by the system


class InvoicePayment(Base, UUIDPrimaryKeyMixin):
    """A payment received against an invoice. Rows are never edited; mistakes are corrected by voiding the invoice."""
    __tablename__ = "invoice_payments"
    __table_args__ = (
        enum_check("invoice_payments", "method", PaymentMethod),
        CheckConstraint("amount > 0", name="ck_invoice_payments_amount"),
        # A retried request with the same reference cannot record the same money twice.
        Index("uq_invoice_payments_reference", "invoice_id", "reference", unique=True, postgresql_where=text("reference IS NOT NULL")),
    )

    invoice_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("invoices.id"), index=True, nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), index=True, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    method: Mapped[str] = mapped_column(String(20), nullable=False)
    reference: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # bank ref, UPI txn id, cheque no.
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    recorded_by_admin: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BillingNotification(Base, UUIDPrimaryKeyMixin):
    """Outbox of billing messages (invoice issued, renewal reminders, overdue). A mailer sends them and sets sent_at."""
    __tablename__ = "billing_notifications"

    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)  # one message per event, however often the sweep runs
    to_email: Mapped[Optional[str]] = mapped_column(String(254), nullable=True)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
