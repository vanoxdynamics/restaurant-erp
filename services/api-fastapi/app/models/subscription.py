import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import BillingTerm, SubscriptionStatus
from app.core.modules import ModuleKey
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_check

_MODULE_KEYS = ", ".join(f"'{m.value}'" for m in ModuleKey)


class Plan(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A sellable bundle of modules and limits. NULL limits mean unlimited."""
    __tablename__ = "plans"

    code: Mapped[str] = mapped_column(String(40), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    max_branches: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_staff: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="INR", server_default="INR", nullable=False)
    price_monthly: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    price_yearly: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)


class PlanModule(Base):
    __tablename__ = "plan_modules"
    __table_args__ = (CheckConstraint(f"module_key IN ({_MODULE_KEYS})", name="ck_plan_modules_module_key"),)

    plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("plans.id", ondelete="CASCADE"), primary_key=True)
    module_key: Mapped[str] = mapped_column(String(40), primary_key=True)


class Subscription(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """An organization's current subscription (one per organization)."""
    __tablename__ = "subscriptions"
    __table_args__ = (
        enum_check("subscriptions", "billing_term", BillingTerm),
        enum_check("subscriptions", "status", SubscriptionStatus),
        CheckConstraint("price_amount >= 0", name="ck_subscriptions_price_amount"),
        CheckConstraint("credit_balance >= 0", name="ck_subscriptions_credit_balance"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), unique=True, nullable=False
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("plans.id"), nullable=False, index=True)
    billing_term: Mapped[str] = mapped_column(String(20), default=BillingTerm.MONTHLY.value, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=SubscriptionStatus.ACTIVE.value, nullable=False)
    price_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), server_default="0", nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR", server_default="INR", nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    current_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    current_period_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)  # NULL = no expiry
    trial_ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    grace_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)  # overrides default grace
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    max_branches_override: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_staff_override: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # A term change scheduled for the next renewal (applied by the renewal worker).
    pending_billing_term: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    pending_price_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    # Prepaid money left over from a downgrade or overpayment; applied to the next invoice.
    credit_balance: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), server_default="0", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)  # optimistic locking


class SubscriptionEvent(Base, UUIDPrimaryKeyMixin):
    """Append-only history of everything that happened to a subscription (term changes, extensions, ...)."""
    __tablename__ = "subscription_events"

    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), index=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    actor_admin_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PlatformModule(Base, TimestampMixin):
    """Platform-wide kill switch for a module. A row with enabled=false turns it off for every subscriber."""
    __tablename__ = "platform_modules"
    __table_args__ = (CheckConstraint(f"module_key IN ({_MODULE_KEYS})", name="ck_platform_modules_module_key"),)

    module_key: Mapped[str] = mapped_column(String(40), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    set_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)


class OrganizationModule(Base, TimestampMixin):
    """Per-organization module override: force a module on or off regardless of the plan."""
    __tablename__ = "organization_modules"
    __table_args__ = (CheckConstraint(f"module_key IN ({_MODULE_KEYS})", name="ck_organization_modules_module_key"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True)
    module_key: Mapped[str] = mapped_column(String(40), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    set_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)  # platform admin (Phase 3+)
