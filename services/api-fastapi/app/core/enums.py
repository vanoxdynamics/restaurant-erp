"""Domain status vocabularies shared by models (CHECK constraints), schemas and services."""

from enum import StrEnum
from typing import Dict, Set


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
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"


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

KOT_TO_ORDER_ITEM_STATUS: Dict[KOTStatus, OrderItemStatus] = {
    KOTStatus.PREPARING: OrderItemStatus.PREPARING,
    KOTStatus.READY: OrderItemStatus.READY,
    KOTStatus.SERVED: OrderItemStatus.SERVED,
    KOTStatus.VOIDED: OrderItemStatus.CANCELLED,
}
