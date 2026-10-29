# 03 — Data model fixes + migration

Fixes: F03 (DB side), F10, F12 (DB side), F18. Depends on: 02.

Goal: the schema can actually support the features the routers promise, and the database enforces
the invariants instead of trusting the API layer. Ship **one new Alembic revision**
(`alembic revision --autogenerate -m "integrity and feature gaps"`, then hand-edit). Do not edit `e54e65c309dc`.

## 1. Status enums — `app/core/enums.py` (new)

`StrEnum`s shared by models and schemas (file 04):

```python
class OrderStatus(StrEnum): ACTIVE="active"; BILLED="billed"; COMPLETED="completed"; CANCELLED="cancelled"
class OrderType(StrEnum): DINE_IN="dine_in"; TAKEAWAY="takeaway"; DELIVERY="delivery"
class OrderItemStatus(StrEnum): ORDERED="ordered"; FIRED="fired"; PREPARING="preparing"; READY="ready"; SERVED="served"; CANCELLED="cancelled"
class KOTStatus(StrEnum): QUEUED="queued"; PREPARING="preparing"; READY="ready"; SERVED="served"; VOIDED="voided"
class Station(StrEnum): KITCHEN="kitchen"; BAR="bar"; GRILL="grill"; DESSERT="dessert"
class TableStatus(StrEnum): VACANT="vacant"; OCCUPIED="occupied"; BILLED="billed"; RESERVED="reserved"
class BillStatus(StrEnum): UNPAID="unpaid"; PARTIALLY_PAID="partially_paid"; PAID="paid"; VOIDED="voided"; REFUNDED="refunded"
class PaymentMode(StrEnum): CASH="cash"; CARD="card"; UPI="upi"
class PaymentStatus(StrEnum): PENDING="pending"; SUCCESS="success"; FAILED="failed"
class Role(StrEnum): OWNER="owner"; MANAGER="manager"; CAPTAIN="captain"; WAITER="waiter"; CASHIER="cashier"; KITCHEN="kitchen"
class ShiftStatus(StrEnum): OPEN="open"; CLOSED="closed"
class AuditAction(StrEnum): KOT_VOID="kot_void"; BILL_VOID="bill_void"; HIGH_DISCOUNT="high_discount"; TABLE_TRANSFER="table_transfer"; TABLE_MERGE="table_merge"; LOGIN_LOCKOUT="login_lockout"
```

Remove `split` from payment modes: a split is several `Payment` rows, not a mode.

Keep columns as `String(30)` (easier migrations than PG enums) but add a **CHECK constraint** per
column listing the allowed values. Write a helper `enum_check(column: str, enum: type[StrEnum]) -> CheckConstraint`
in `app/models/base.py` and use it in each model's `__table_args__`.

Also add transition maps (used by services in 08/09):

```python
KOT_TRANSITIONS = {QUEUED: {PREPARING, VOIDED}, PREPARING: {READY, VOIDED}, READY: {SERVED}, SERVED: set(), VOIDED: set()}
```
…and equivalents for `OrderStatus`, `BillStatus`, `TableStatus`.

## 2. Relationship loading

On every `relationship(...)` set `lazy="raise"`. Services must `selectinload` explicitly; accidental
lazy loads fail in tests instead of raising `MissingGreenlet` in production (F08).

Change `Category.items` cascade from `"all, delete-orphan"` to `"save-update, merge"` and never hard-delete
categories/menu items — use `is_active=False`. Same for `Branch.floors` and `Floor.tables`. Keep
`delete-orphan` only on true children with no external references: `MenuItem.variants` is **not** one
(order items reference variants) → also soft-delete.

## 3. Model changes

### `floor.py`
- `Branch`: add `timezone: str = "Asia/Kolkata"` (used for business date), `gstin: Optional[str](15)`,
  `upi_vpa: Optional[str](100)` and `upi_payee_name: Optional[str](100)` (file 09).
- `DiningTable`: `merged_into_table_id` → `ForeignKey("dining_tables.id", ondelete="SET NULL")`;
  add `is_active: bool`; `UniqueConstraint("floor_id", "table_number")`; status CHECK.

### `catalog.py`
- `MenuItem`: add `station: str = "kitchen"` (CHECK via `Station`), `sort_order: int = 0`.
  CHECK `base_price >= 0`, `tax_rate BETWEEN 0 AND 28`.
- `ItemVariant`: add `is_active`. CHECK `price >= 0`.
- New association table `menu_item_modifier_groups(menu_item_id FK, modifier_group_id FK, sort_order)`,
  composite PK. Relationship `MenuItem.modifier_groups` (secondary, `lazy="raise"`).
- `ModifierGroup`: CHECK `min_selection >= 0 AND max_selection >= min_selection`.

### `orders.py`
- `Order.waiter_id` → `ForeignKey("staff_users.id")`.
- Use SQLAlchemy optimistic locking (F18): `__mapper_args__ = {"version_id_col": version}`. Remove the
  `default=1` hand-management; SQLAlchemy sets and bumps it, and raises `StaleDataError` on mismatch.
- Add `opened_by_shift_id: Optional[FK shifts.id]`, `cancel_reason: Optional[Text]`,
  `business_date: Date` (indexed; set at creation from branch timezone — makes Z-report queries trivial).
- `order_number`: drop the global unique index; add `UniqueConstraint("branch_id", "order_number")`.
- `OrderItem.variant_id` → `ForeignKey("item_variants.id")`. Add `variant_name_snapshot: Optional[str]`,
  `station_snapshot: str`, `line_total: Numeric(10,2)` (unit+modifiers × qty). Index `item_id`.
  CHECK `quantity > 0`.
- New `OrderItemModifier(order_item_id FK, modifier_item_id FK, name_snapshot, price_snapshot)`.

### `kots.py`
- `KOT`: add `branch_id FK` (denormalized for station-board queries), `fired_by_id FK staff_users`,
  `voided_by_id`, `voided_approved_by_id` (nullable FKs). Replace global unique on `kot_number` with
  `UniqueConstraint("branch_id", "kot_number")`. Composite index `(branch_id, station, status)`.
- `KOTItem`: index `order_item_id`; CHECK `quantity > 0`.

### `billing.py`
- `Bill`: add `branch_id FK`, `business_date`, `paid_amount Numeric(10,2) default 0`,
  `discount_approved_by_id Optional FK`, `voided_by_id`, `void_reason`. CHECKs: all money columns `>= 0`,
  `discount_amount <= subtotal`. Replace global unique on `bill_number` with `UniqueConstraint("branch_id","bill_number")`.
  **Partial unique index**: one non-voided bill per order —
  `Index("uq_bills_order_active", "order_id", unique=True, postgresql_where=text("status <> 'voided'"))`.
- `Payment`: add `received_by_id FK`, `shift_id Optional FK`, `idempotency_key Optional String(100)`.
  CHECK `amount > 0`. Change `Bill.payments` cascade to `"save-update, merge"` (payments are financial records).

### `staff.py`
- `StaffUser`: add `pin_lookup: String(64)` (HMAC from `compute_pin_lookup`, file 01). Partial unique
  index `(branch_id, pin_lookup) WHERE is_active`. `role` CHECK. Add `token_version: int = 0`
  (bump to revoke all tokens for a user — file 05).
- `Shift`: add `expected_cash Optional`, `cash_variance Optional`. Partial unique index
  `(user_id) WHERE status = 'open'` (one open shift per user).

### New `app/models/audit.py`
```python
class AuditLog(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "audit_logs"
    branch_id: FK branches (indexed)
    action_type: String(40)  # AuditAction CHECK
    actor_id: FK staff_users
    approver_id: Optional FK staff_users
    entity_type: String(40); entity_id: UUID
    amount: Optional Numeric(10,2)
    details: JSONB
    created_at: DateTime(tz) server_default now()  # immutable: no updated_at
```
Index `(branch_id, created_at DESC)`.

### New `app/models/counters.py` (F03)
```python
class DocumentCounter(Base):
    __tablename__ = "document_counters"
    branch_id: UUID FK, PK
    doc_type: String(20), PK          # "order" | "kot" | "bill"
    period: String(10), PK            # "20261028" for daily, "2627" for FY
    value: BigInteger, not null, default 0
```
Used by `next_document_number` in file 05 with `INSERT ... ON CONFLICT DO UPDATE ... RETURNING` —
transactional, survives Redis loss, and rolls back with the business transaction.

Register new models in `app/models/__init__.py`.

## 4. Migration notes

- Autogenerate, then **review by hand**: autogenerate misses CHECK constraints and partial indexes,
  and gets server defaults wrong. Add `server_default` for every new NOT NULL column so the migration
  runs on a populated DB.
- `pin_lookup` can't be backfilled (PINs are hashed). Add it nullable, and document that existing staff
  must reset their PIN; tighten to NOT NULL in a later revision. Dev DBs can just be recreated.
- `business_date` backfill: `created_at AT TIME ZONE 'Asia/Kolkata'`::date.
- Write a correct `downgrade()`.

## Tests (`tests/models/`)

- Each CHECK rejects a bad value (insert raw row → `IntegrityError`).
- Two active staff in the same branch with same `pin_lookup` → `IntegrityError`; different branches OK.
- Second open shift for a user → `IntegrityError`.
- Second non-voided bill for an order → `IntegrityError`; after voiding the first, a new one is allowed.
- Same `order_number` in two branches → OK; same branch → error.
- Updating an `Order` loaded at version 1 after another session bumped it → `StaleDataError`.
- Touching `order.items` without `selectinload` → `InvalidRequestError` (lazy="raise").
- `alembic upgrade head` / `downgrade -1` / `upgrade head` round-trip.

## Done when

Migration applies cleanly on an empty DB and on a DB at `e54e65c309dc` with sample rows; all tests pass.
