import re
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator

from app.core.enums import BillingTerm, BusinessCategory
from app.core.modules import ModuleKey
from app.schemas.common import Pin, Reason

Slug = Annotated[str, Field(min_length=3, max_length=60)]
Price = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]
Effective = Literal["now", "next_renewal"]
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,58}[a-z0-9]$")


# --- plans -----------------------------------------------------------------------------------------


class PlanBase(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    max_branches: Optional[int] = Field(default=None, ge=1, le=10000)
    max_staff: Optional[int] = Field(default=None, ge=1, le=100000)
    currency: str = Field(default="INR", pattern=r"^[A-Z]{3}$")
    price_monthly: Optional[Price] = None
    price_yearly: Optional[Price] = None


class PlanCreate(PlanBase):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{2,39}$")
    module_keys: List[ModuleKey] = Field(default_factory=lambda: [ModuleKey.POS])


class PlanUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=100)
    is_active: Optional[bool] = None
    max_branches: Optional[int] = Field(default=None, ge=1, le=10000)
    max_staff: Optional[int] = Field(default=None, ge=1, le=100000)
    price_monthly: Optional[Price] = None
    price_yearly: Optional[Price] = None
    module_keys: Optional[List[ModuleKey]] = None
    reason: Reason


class PlanRead(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    is_active: bool
    max_branches: Optional[int]
    max_staff: Optional[int]
    currency: str
    price_monthly: Optional[Decimal]
    price_yearly: Optional[Decimal]
    module_keys: List[str]
    subscribers: int = 0


# --- organizations ---------------------------------------------------------------------------------


class OwnerSeed(BaseModel):
    """The first owner account and branch created with the subscriber. Hand the credentials over securely."""
    username: str = Field(min_length=3, max_length=50, pattern=r"^[a-z0-9._-]+$")
    full_name: str = Field(min_length=2, max_length=100)
    password: SecretStr = Field(min_length=10, max_length=128)
    pin: Pin
    branch_code: str = Field(pattern=r"^[A-Z0-9]{2,6}$")
    branch_name: str = Field(default="Main Branch", min_length=2, max_length=100)


class BusinessSettings(BaseModel):
    """Workflow settings overridden for one business. Leave a key out (null) to follow the category's preset."""
    model_config = ConfigDict(extra="forbid")

    table_service: Optional[bool] = None
    kitchen_tickets: Optional[bool] = None
    quick_sale: Optional[bool] = None
    barcode: Optional[bool] = None
    weighed_items: Optional[bool] = None
    tokens: Optional[bool] = None

    def overrides(self) -> Dict[str, bool]:
        return {k: v for k, v in self.model_dump().items() if v is not None}


class BusinessCategoryRead(BaseModel):
    key: str
    name: str
    settings: Dict[str, bool]


class BusinessCategories(BaseModel):
    categories: List[BusinessCategoryRead]
    setting_labels: Dict[str, str]


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    slug: Slug
    category: BusinessCategory = BusinessCategory.RESTAURANT
    settings: Optional[BusinessSettings] = None
    legal_name: Optional[str] = Field(default=None, max_length=200)
    billing_email: Optional[EmailStr] = None
    country: Optional[str] = Field(default=None, pattern=r"^[A-Z]{2}$")
    plan_id: uuid.UUID
    billing_term: BillingTerm
    start: Literal["trial", "active"] = "trial"
    trial_days: Optional[int] = Field(default=None, ge=1, le=90)
    price_amount: Optional[Price] = None  # custom price for the chosen term; defaults to the plan price
    owner: OwnerSeed

    @field_validator("slug")
    @classmethod
    def valid_slug(cls, v: str) -> str:
        if not _SLUG.match(v):
            raise ValueError("slug must be lowercase letters, digits and dashes")
        return v


class OrganizationUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=150)
    category: Optional[BusinessCategory] = None
    settings: Optional[BusinessSettings] = None  # replaces all overrides; {} goes back to the category's preset
    legal_name: Optional[str] = Field(default=None, max_length=200)
    billing_email: Optional[EmailStr] = None
    country: Optional[str] = Field(default=None, pattern=r"^[A-Z]{2}$")
    reason: Reason


class SubscriptionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    plan_id: uuid.UUID
    plan_code: Optional[str] = None
    billing_term: str
    status: str
    effective_status: Optional[str] = None
    price_amount: Decimal
    currency: str
    started_at: datetime
    current_period_start: datetime
    current_period_end: Optional[datetime]
    trial_ends_at: Optional[datetime]
    grace_until: Optional[datetime]
    auto_renew: bool
    cancel_at_period_end: bool
    pending_billing_term: Optional[str]
    pending_price_amount: Optional[Decimal]
    credit_balance: Decimal = Decimal("0.00")
    max_branches_override: Optional[int]
    max_staff_override: Optional[int]
    version: int


class OrganizationSummary(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    status: str
    category: str = "restaurant"
    billing_email: Optional[str]
    created_at: datetime
    plan_code: Optional[str]
    billing_term: Optional[str]
    subscription_status: Optional[str]
    current_period_end: Optional[datetime]


class ModuleState(BaseModel):
    key: str
    name: str
    is_core: bool
    enabled: bool  # effective result
    source: str  # core | plan | override | platform_disabled | off
    override: Optional[Dict[str, Any]] = None


class OrganizationDetail(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    legal_name: Optional[str]
    billing_email: Optional[str]
    country: Optional[str]
    status: str
    category: str = "restaurant"
    settings: Dict[str, bool] = {}  # effective (preset + overrides)
    settings_overrides: Dict[str, bool] = {}
    suspended_at: Optional[datetime]
    suspension_reason: Optional[str]
    created_at: datetime
    subscription: Optional[SubscriptionRead]
    access: str
    modules: List[ModuleState]
    branches: int
    active_staff: int
    recent_events: List[Dict[str, Any]]


class OrganizationCreated(BaseModel):
    organization: OrganizationDetail
    amount_due: Optional[str] = None
    invoice_number: Optional[str] = None


# --- lifecycle -------------------------------------------------------------------------------------


class ReasonBody(BaseModel):
    reason: Reason


class CancelBody(BaseModel):
    reason: Reason
    at_period_end: bool = True


class ActivateBody(BaseModel):
    reason: Reason
    price_amount: Optional[Price] = None


class ExtendBody(BaseModel):
    days: int = Field(ge=1, le=366)
    reason: Reason
    version: int


class SubscriptionChange(BaseModel):
    """Change plan, term or price. `effective` decides whether it starts now (with proration) or at renewal."""
    plan_id: Optional[uuid.UUID] = None
    billing_term: Optional[BillingTerm] = None
    price_amount: Optional[Price] = None
    effective: Effective = "now"


class SubscriptionUpdate(SubscriptionChange):
    auto_renew: Optional[bool] = None
    max_branches_override: Optional[int] = Field(default=None, ge=1, le=10000)
    max_staff_override: Optional[int] = Field(default=None, ge=1, le=100000)
    clear_limit_overrides: bool = False
    reason: Reason
    version: int


class ChangePreviewRead(BaseModel):
    effective: str
    effective_at: datetime
    current_term: str
    new_term: str
    current_price: Decimal
    new_price: Decimal
    credit_for_unused_time: Decimal
    amount_due_now: Decimal
    account_credit_remaining: Decimal
    new_period_start: datetime
    new_period_end: datetime


class ImpersonateBody(BaseModel):
    reason: Reason
    minutes: int = Field(default=15, ge=1, le=30)
    staff_id: Optional[uuid.UUID] = None  # default: the organization's first active owner


class ImpersonationToken(BaseModel):
    access_token: str
    expires_in: int
    staff_id: uuid.UUID
    username: str
    read_only: bool = True


# --- modules ---------------------------------------------------------------------------------------


class ModuleOverrideBody(BaseModel):
    enabled: bool
    reason: Reason
    expires_at: Optional[datetime] = None


class GlobalModuleBody(BaseModel):
    enabled: bool
    reason: Reason


class GlobalModuleRead(BaseModel):
    key: str
    name: str
    description: str
    is_core: bool
    depends_on: List[str]
    globally_enabled: bool
    reason: Optional[str]
    organizations_with_access: int


class BulkModuleBody(BaseModel):
    module_key: ModuleKey
    enabled: bool
    reason: Reason
    organization_ids: Optional[List[uuid.UUID]] = Field(default=None, max_length=500)
    plan_id: Optional[uuid.UUID] = None
    expires_at: Optional[datetime] = None
    dry_run: bool = True


class BulkModuleResult(BaseModel):
    dry_run: bool
    changed: List[Dict[str, Any]]
    unchanged: int
    blocked: List[Dict[str, Any]]


# --- dashboard -------------------------------------------------------------------------------------


class Dashboard(BaseModel):
    organizations_by_status: Dict[str, int]
    subscriptions_by_status: Dict[str, int]
    subscriptions_by_term: Dict[str, int]
    mrr: Dict[str, str]  # currency -> monthly recurring revenue
    arr: Dict[str, str]
    renewals_next_30_days: List[Dict[str, Any]]
    trials_ending_next_7_days: List[Dict[str, Any]]
    past_due: int
    outstanding: Dict[str, str] = {}  # unpaid invoice balance per currency
    open_invoices: int = 0
    overdue_invoices: int = 0


class SubscriptionUpdated(BaseModel):
    subscription: SubscriptionRead
    change: Optional[ChangePreviewRead] = None
    invoice_number: Optional[str] = None


class ActivateResult(BaseModel):
    amount_due: str  # after any account credit
    currency: str
    invoice_number: str
    credit_applied: str = "0.00"


# --- approvals and alerts (Phase 7) ------------------------------------------------------------------------------------


class ApprovalRead(BaseModel):
    id: uuid.UUID
    action: str
    summary: str
    payload: Dict[str, Any]
    reason: str
    status: str
    requested_by: str
    requested_at: datetime
    expires_at: datetime
    decided_by: Optional[str]
    decided_at: Optional[datetime]
    decision_note: Optional[str]
    result: Optional[Dict[str, Any]]


class ApprovalDecision(BaseModel):
    note: Optional[str] = Field(default=None, max_length=500)


class ApprovalRejection(BaseModel):
    note: Reason


class AlertRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    severity: str
    message: str
    data: Dict[str, Any]
    created_at: datetime
    notified_at: Optional[datetime]
    acknowledged_at: Optional[datetime]
    acknowledged_by: Optional[uuid.UUID]
