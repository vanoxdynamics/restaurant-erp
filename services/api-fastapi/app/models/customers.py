import uuid
from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Customer(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    Someone a business sells to. Customers belong to the business, not a branch, so every branch finds the same person by phone.
    Orders point at a customer; bills, visits and spend are read through the order.
    """

    __tablename__ = "customers"
    __table_args__ = (
        # The phone number is how the counter finds a customer, so it identifies one customer within a business.
        Index("uq_customers_org_phone", "organization_id", "phone", unique=True, postgresql_where=text("phone IS NOT NULL")),
        Index("ix_customers_org_name", "organization_id", "name"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    phone: Mapped[Optional[str]] = mapped_column(String(15), nullable=True)  # digits only; Indian mobiles as 10 digits
    email: Mapped[Optional[str]] = mapped_column(String(254), nullable=True)
    gstin: Mapped[Optional[str]] = mapped_column(String(15), nullable=True)  # printed on the bill for a business buyer
    address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Customers are referenced by order history: hidden with is_active, never deleted.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
