import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import BillStatus, PaymentMode, PaymentStatus
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_check


class Bill(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "bills"
    __table_args__ = (
        UniqueConstraint("branch_id", "bill_number", name="uq_bills_branch_number"),
        # One non-voided bill per order.
        Index("uq_bills_order_active", "order_id", unique=True, postgresql_where=text("status <> 'voided'")),
        enum_check("bills", "status", BillStatus),
        CheckConstraint(
            "subtotal >= 0 AND tax_cgst >= 0 AND tax_sgst >= 0 AND discount_amount >= 0 "
            "AND grand_total >= 0 AND paid_amount >= 0",
            name="ck_bills_non_negative",
        ),
        CheckConstraint("discount_amount <= subtotal", name="ck_bills_discount_le_subtotal"),
    )

    branch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False, index=True)
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False, index=True)
    bill_number: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    tax_cgst: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    tax_sgst: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    discount_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    discount_approved_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("staff_users.id"), nullable=True)
    grand_total: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    paid_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"), server_default="0")
    status: Mapped[str] = mapped_column(String(30), default=BillStatus.UNPAID.value, index=True)
    settled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    voided_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("staff_users.id"), nullable=True)
    void_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Payments are financial records: never cascade-delete them.
    payments: Mapped[List["Payment"]] = relationship(
        "Payment", back_populates="bill", cascade="save-update, merge", lazy="raise",
        order_by="Payment.created_at",
    )

    @property
    def balance_due(self) -> Decimal:
        return self.grand_total - self.paid_amount


class Payment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "payments"
    __table_args__ = (
        Index(
            "uq_payments_bill_idempotency", "bill_id", "idempotency_key", unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        enum_check("payments", "payment_mode", PaymentMode),
        enum_check("payments", "status", PaymentStatus),
        CheckConstraint("amount > 0", name="ck_payments_amount"),
    )

    bill_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("bills.id"), nullable=False, index=True)
    payment_mode: Mapped[str] = mapped_column(String(30), nullable=False)  # cash, card, upi
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    reference_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # UPI Transaction ID, Card auth code
    status: Mapped[str] = mapped_column(String(30), default=PaymentStatus.SUCCESS.value)
    received_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("staff_users.id"), nullable=True)
    shift_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("shifts.id"), nullable=True, index=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    bill: Mapped["Bill"] = relationship("Bill", back_populates="payments", lazy="raise")
