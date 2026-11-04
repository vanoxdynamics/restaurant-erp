"""Domain status vocabularies shared by models (CHECK constraints), schemas and services."""

from enum import StrEnum
from typing import Dict, Set


class OrgStatus(StrEnum):
    TRIAL = "trial"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    SUSPENDED = "suspended"
    CANCELLED = "cancelled"


class PlatformRole(StrEnum):
    SUPER_ADMIN = "super_admin"
    BILLING = "billing"
    SUPPORT = "support"
    READ_ONLY = "read_only"


class AlertSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    EXECUTED = "executed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    FAILED = "failed"


class SubscriptionStatus(StrEnum):
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class InvoiceKind(StrEnum):
    RENEWAL = "renewal"  # next period; paying it extends the subscription
    ACTIVATION = "activation"  # first or restarted paid period (already in effect when issued)
    PLAN_CHANGE = "plan_change"  # prorated amount for a mid-period plan/term change (already in effect)
    MANUAL = "manual"


class InvoiceStatus(StrEnum):
    OPEN = "open"
    PAID = "paid"
    VOID = "void"


class PaymentMethod(StrEnum):
    BANK_TRANSFER = "bank_transfer"
    UPI = "upi"
    CARD_OFFLINE = "card_offline"
    CASH = "cash"
    CHEQUE = "cheque"
    OTHER = "other"


class BillingTerm(StrEnum):
    MONTHLY = "monthly"
    YEARLY = "yearly"


class OrderStatus(StrEnum):
    ACTIVE = "active"
    BILLED = "billed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class OrderType(StrEnum):
    DINE_IN = "dine_in"
    TAKEAWAY = "takeaway"
    DELIVERY = "delivery"


class OrderItemStatus(StrEnum):
    ORDERED = "ordered"
    FIRED = "fired"
    PREPARING = "preparing"
    READY = "ready"
    SERVED = "served"
    CANCELLED = "cancelled"


class KOTStatus(StrEnum):
    QUEUED = "queued"
    PREPARING = "preparing"
    READY = "ready"
    SERVED = "served"
    VOIDED = "voided"


class Fulfilment(StrEnum):
    """How a kitchen ticket reaches the kitchen: on the kitchen screen, or as a printed slip carried by staff."""
    SCREEN = "screen"
    SLIP = "slip"


class Station(StrEnum):
    KITCHEN = "kitchen"
    GRILL = "grill"
    DESSERT = "dessert"


class TableStatus(StrEnum):
    VACANT = "vacant"
    OCCUPIED = "occupied"
    BILLED = "billed"
    RESERVED = "reserved"


class BillStatus(StrEnum):
    UNPAID = "unpaid"
    PARTIALLY_PAID = "partially_paid"
    PAID = "paid"
    VOIDED = "voided"
    REFUNDED = "refunded"


class PaymentMode(StrEnum):
    CASH = "cash"
    CARD = "card"
    UPI = "upi"


class PaymentStatus(StrEnum):
    PENDING = "pending"  # a card/UPI terminal was asked to charge; the result is not known yet
    SUCCESS = "success"
    FAILED = "failed"
    REVERSED = "reversed"  # a terminal payment voided the same day; no longer counts towards the bill


class Role(StrEnum):
    OWNER = "owner"
    MANAGER = "manager"
    CAPTAIN = "captain"
    WAITER = "waiter"
    CASHIER = "cashier"
    KITCHEN = "kitchen"


class ShiftStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class AuditAction(StrEnum):
    KOT_VOID = "kot_void"
    ORDER_CANCEL = "order_cancel"
    ORDER_ITEM_CANCEL = "order_item_cancel"
    BILL_VOID = "bill_void"
    HIGH_DISCOUNT = "high_discount"
    TABLE_TRANSFER = "table_transfer"
    TABLE_MERGE = "table_merge"
    LOGIN_LOCKOUT = "login_lockout"
    CASH_VARIANCE = "cash_variance"
    KOT_REPRINT = "kot_reprint"  # a kitchen slip was printed again
    PAYMENT_REVERSE = "payment_reverse"  # a card/UPI terminal payment was voided


class VoidRequestStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class Topic(StrEnum):
    APPROVALS = "approvals"
    ORDERS = "orders"
    KOTS = "kots"
    TABLES = "tables"
    BILLING = "billing"
    CATALOG = "catalog"
    ENTITLEMENTS = "entitlements"  # subscription / module changes; every signed-in user of a branch may listen


ROLE_RANK: Dict[Role, int] = {
    Role.OWNER: 50,
    Role.MANAGER: 40,
    Role.CASHIER: 30,
    Role.CAPTAIN: 20,
    Role.WAITER: 10,
    Role.KITCHEN: 10,
}

MANAGER_ROLES: Set[Role] = {Role.OWNER, Role.MANAGER}

KOT_TRANSITIONS: Dict[KOTStatus, Set[KOTStatus]] = {
    KOTStatus.QUEUED: {KOTStatus.PREPARING, KOTStatus.VOIDED},
    KOTStatus.PREPARING: {KOTStatus.READY, KOTStatus.VOIDED},
    KOTStatus.READY: {KOTStatus.SERVED},
    KOTStatus.SERVED: set(),
    KOTStatus.VOIDED: set(),
}

# A printed slip has no kitchen stages: floor staff take it straight from queued to served.
SLIP_KOT_TRANSITIONS: Dict[KOTStatus, Set[KOTStatus]] = {
    KOTStatus.QUEUED: {KOTStatus.SERVED, KOTStatus.VOIDED},
    KOTStatus.PREPARING: {KOTStatus.SERVED, KOTStatus.VOIDED},
    KOTStatus.READY: {KOTStatus.SERVED, KOTStatus.VOIDED},
    KOTStatus.SERVED: set(),
    KOTStatus.VOIDED: set(),
}

KOT_TO_ORDER_ITEM_STATUS: Dict[KOTStatus, OrderItemStatus] = {
    KOTStatus.PREPARING: OrderItemStatus.PREPARING,
    KOTStatus.READY: OrderItemStatus.READY,
    KOTStatus.SERVED: OrderItemStatus.SERVED,
    KOTStatus.VOIDED: OrderItemStatus.CANCELLED,
}
