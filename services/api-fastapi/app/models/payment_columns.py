"""The columns every shop payment table shares (the restaurant `payments` table without `bill_id`). See retailkit/tenders.py."""

import uuid
from decimal import Decimal
from typing import Any, Dict, Optional

from sqlalchemy import ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from app.core.enums import PaymentStatus


class PaymentColumns:
    """The columns of `payments` without `bill_id`. A module's payment model adds `sale_id`, `branch_id` and its checks."""

    payment_mode: Mapped[str] = mapped_column(String(30), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    reference_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # UPI UTR, card approval code
    status: Mapped[str] = mapped_column(String(30), default=PaymentStatus.SUCCESS.value, nullable=False)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # "manual" (keyed in by the cashier) or a terminal ("pinelabs"), whose masked transaction details go in provider_meta.
    provider: Mapped[str] = mapped_column(String(30), default="manual", server_default="manual", nullable=False)
    provider_meta: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    @declared_attr
    def received_by_id(cls) -> Mapped[Optional[uuid.UUID]]:
        return mapped_column(UUID(as_uuid=True), ForeignKey("staff_users.id"), nullable=True)

    @declared_attr
    def shift_id(cls) -> Mapped[Optional[uuid.UUID]]:
        return mapped_column(UUID(as_uuid=True), ForeignKey("shifts.id"), nullable=True, index=True)
