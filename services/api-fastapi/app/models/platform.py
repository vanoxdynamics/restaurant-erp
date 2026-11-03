import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Identity, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import PlatformRole
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_check


class PlatformAdmin(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A platform operator. Entirely separate from tenant StaffUser: different table, keys and tokens."""
    __tablename__ = "platform_admins"
    __table_args__ = (enum_check("platform_admins", "role", PlatformRole),)

    email: Mapped[str] = mapped_column(String(254), unique=True, index=True, nullable=False)  # stored lowercase
    full_name: Mapped[str] = mapped_column(String(100), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    totp_secret_enc: Mapped[str] = mapped_column(String(255), nullable=False)  # Fernet-encrypted base32 secret
    totp_last_step: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)  # replay guard
    role: Mapped[str] = mapped_column(String(30), default=PlatformRole.READ_ONLY.value, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    password_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PlatformRecoveryCode(Base, UUIDPrimaryKeyMixin):
    """One-time 2FA recovery code (stored as a keyed hash)."""
    __tablename__ = "platform_recovery_codes"

    admin_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("platform_admins.id", ondelete="CASCADE"), index=True, nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class PlatformSession(Base, UUIDPrimaryKeyMixin):
    """A signed-in console session. The refresh token rotates on every use; replaying an old one kills the session."""
    __tablename__ = "platform_sessions"

    admin_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("platform_admins.id", ondelete="CASCADE"), index=True, nullable=False)
    refresh_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    prev_refresh_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)  # absolute lifetime
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    step_up_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class PlatformAuditLog(Base):
    """
    Append-only, hash-chained record of every platform action. Each row's hash covers its content and the
    previous row's hash, so any edit, deletion or reordering is detectable (see verify_chain).
    A database trigger rejects UPDATE and DELETE.
    """
    __tablename__ = "platform_audit_logs"
    __table_args__ = (Index("ix_platform_audit_logs_target", "target_type", "target_id"),)

    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    admin_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)  # no FK: survives admin deletion
    action: Mapped[str] = mapped_column(String(60), index=True, nullable=False)
    target_type: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    target_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    before: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    after: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    request_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False)
