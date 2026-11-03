import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import AlertSeverity, ApprovalStatus
from app.models.base import Base, UUIDPrimaryKeyMixin, enum_check


class PlatformAlert(Base, UUIDPrimaryKeyMixin):
    """A security-relevant event operators should see: lockouts, token theft signals, dangerous changes, tampering."""
    __tablename__ = "platform_alerts"
    __table_args__ = (
        enum_check("platform_alerts", "severity", AlertSeverity),
        Index("ix_platform_alerts_open", "created_at", postgresql_where="acknowledged_at IS NULL"),
    )

    kind: Mapped[str] = mapped_column(String(60), index=True, nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    data: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)  # webhook delivered
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)


class PlatformApproval(Base, UUIDPrimaryKeyMixin):
    """A dangerous action waiting for a second super admin. The stored payload is exactly what runs on approval."""
    __tablename__ = "platform_approvals"
    __table_args__ = (enum_check("platform_approvals", "status", ApprovalStatus),)

    action: Mapped[str] = mapped_column(String(60), index=True, nullable=False)
    summary: Mapped[str] = mapped_column(String(300), nullable=False)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(10), default=ApprovalStatus.PENDING.value, nullable=False, index=True)
    requested_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("platform_admins.id"), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("platform_admins.id"), nullable=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    result: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
