import uuid
from decimal import Decimal
from typing import List, Optional
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Bill(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "bills"

    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False, index=True)
    bill_number: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    tax_cgst: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    tax_sgst: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    discount_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    grand_total: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="unpaid", index=True)  # unpaid, paid, voided, refunded
    settled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    payments: Mapped[List["Payment"]] = relationship("Payment", back_populates="bill", cascade="all, delete-orphan")


class Payment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "payments"

    bill_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("bills.id"), nullable=False, index=True)
    payment_mode: Mapped[str] = mapped_column(String(30), nullable=False)  # cash, card, upi, split
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    reference_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # UPI Transaction ID, Card auth code
    status: Mapped[str] = mapped_column(String(30), default="success")  # pending, success, failed

    bill: Mapped["Bill"] = relationship("Bill", back_populates="payments")
