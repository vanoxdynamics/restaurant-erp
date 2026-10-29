# 04 — Pydantic schemas & validation

Fixes: F08 (schema side), F09, F19 (schema parts). Depends on: 03 (`app/core/enums.py`).

Goal: invalid input is rejected with 422 at the edge, nothing a client sends can set server-owned
fields, and every numeric value fits its DB column.

## 1. Shared types — `app/schemas/common.py` (new)

```python
Money = Annotated[Decimal, Field(ge=0, max_digits=10, decimal_places=2)]
PositiveMoney = Annotated[Decimal, Field(gt=0, max_digits=10, decimal_places=2)]
TaxRate = Annotated[Decimal, Field(ge=0, le=28, max_digits=5, decimal_places=2)]
Quantity = Annotated[int, Field(ge=1, le=999)]
Pin = Annotated[str, Field(pattern=r"^\d{4,6}$")]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Notes = Annotated[Optional[str], Field(default=None, max_length=500)]

class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

class Page(BaseModel, Generic[T]):
    items: list[T]; total: int; limit: int; offset: int
```

Pagination dependency `PageParams(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0))`
in `app/core/pagination.py`. Use it on every list endpoint that can grow (orders, KOTs, bills, staff, audit log).

Match string lengths to model column lengths (e.g. menu item `name` max 150, `table_number` max 20,
`username` max 50 and `^[a-z0-9_.]+$`).

## 2. Per-module changes

Use the enums from `app/core/enums.py` everywhere a field is currently a free `str`.

### `catalog.py`
- `MenuItemBase`: `base_price: Money`, `tax_rate: TaxRate`, `station: Station = Station.KITCHEN`,
  `image_url: Optional[HttpUrl]`, `name: Annotated[str, max_length=150]`.
- `MenuItemCreate`: add `variants: list[ItemVariantCreate] = []`, `modifier_group_ids: list[UUID] = []`.
- `MenuItemUpdate`: all optional, same constraints; keep (it gets a PATCH route in file 07).
- `MenuItemRead`: add `modifier_groups: list[ModifierGroupRead]`.
- `ModifierGroupBase`: `model_validator` ensures `max_selection >= min_selection`.
- `CategoryRead`: **drop** nested `items` (list categories should be cheap). Add a separate
  `MenuRead` (categories with items) for a `GET /catalog/menu` endpoint (file 07).

### `floor.py`
- `DiningTableCreate`: remove `status` from create input (server sets `vacant`).
- `DiningTableRead`: `status: TableStatus`.
- `TableMergeRequest.secondary_table_ids`: `min_length=1`, `model_validator` rejects duplicates and
  `primary_table_id in secondary_table_ids`.
- `TableTransferRequest`: validator `source != target`.
- `BranchCreate`: `code` pattern `^[A-Z0-9]{2,8}$` (used in document numbers); add `timezone`
  (validate with `zoneinfo.ZoneInfo`), `gstin` pattern, `upi_vpa`, `upi_payee_name`.
- `BranchRead`: **drop** nested `floors`; floors come from `/branches/{id}/floors`.

### `orders.py`
- `OrderItemCreate`: `quantity: Quantity`, `notes: Notes`, add `modifier_ids: list[UUID] = []`.
- `OrderCreate`: `order_type: OrderType`; `guest_count` `ge=1, le=100`; validator: `table_id` required
  iff `order_type == dine_in`. **Remove `branch_id` from the body** — it comes from the authenticated
  user (file 05). (If owners operate across branches, keep it but authorize it in the service.)
- `OrderUpdate`: keep `version`; allow only `guest_count` and `notes`. **Remove `status` and `table_id`**
  — status changes go through dedicated actions (cancel, bill, complete); table changes go through
  `/floor/tables/transfer`.
- Add `OrderCancelRequest(version: int, reason: ShortText)`.
- Add `OrderItemsAppend(version: int, items: list[OrderItemCreate] = Field(min_length=1))` — body for
  `POST /orders/{id}/items` (replaces the bare list so the optimistic lock applies; file 08).
- Add `OrderItemCancelRequest(reason: ShortText, approver_pin: Optional[Pin])`.
- `OrderItemRead`: add `variant_name_snapshot`, `station_snapshot`, `line_total`, `modifiers`.
- `OrderRead`: `status: OrderStatus`, `order_type: OrderType`, add `business_date`.

### `kots.py`
- `KOTCreate`: replace with `KOTFireRequest(order_id: UUID, order_item_ids: Optional[list[UUID]] = None)`.
  `None` = fire all `ordered` items. The server derives `item_name`, quantity, notes and station from
  the order items — the client never sends them.
- Delete `KOTItemCreate` (no longer an input).
- `KOTStatusUpdate.status: KOTStatus` (validator: not `voided` — voids use the void endpoint).
- `VoidKOTRequest`: `void_reason: ShortText` (min_length 3), `approver_pin: Optional[Pin]` (required
  unless the caller is manager+; enforced in service).

### `billing.py`
- `PaymentCreate`: `payment_mode: PaymentMode`, `amount: PositiveMoney`, `reference_id` max 100;
  validator: `reference_id` required for `upi` and `card`.
- `BillCreate`: `discount_amount: Money = 0`, `discount_reason` required when `discount_amount > 0`,
  `approver_pin: Optional[Pin]`.
- `SplitPaymentRequest.payments`: `min_length=2, max_length=10`.
- `BillRead`: add `paid_amount`, `balance_due` (computed field), `status: BillStatus`.
- Add `BillVoidRequest(reason: ShortText, approver_pin: Optional[Pin])`.

### `staff.py`
- `PINAuthRequest`: `pin: Pin`, `branch_id: UUID` (required — PIN is only unique per branch), optional `device_id: str`.
- `StaffUserCreate`: `role: Role`, `pin: Pin`, `password: Optional[SecretStr]` with `min_length=10`;
  validator: password required when role in {owner, manager}.
- Add `StaffUserUpdate` (full_name, role, branch_id, is_active — all optional) and `PinResetRequest(pin: Pin)`.
- `PasswordLoginRequest.password: SecretStr`.
- `ShiftOpenRequest`: remove `branch_id` (from user); `opening_cash: Money`.
- `ShiftCloseRequest`: `blind_drop_cash: Money`. Delete the duplicate `BlindDropRequest` and its export.
- `ShiftRead`: `expected_cash` and `cash_variance` are returned **only** to manager+ — create
  `ShiftReadManager(ShiftRead)` with those fields; the cashier gets `ShiftRead` (keeps the drop blind).
- `TokenResponse`: add `expires_in: int`, `branch_id`.

### `analytics.py`
- `FraudAuditLogRead` → rename `AuditLogRead`, fields match `AuditLog` model (file 03): `details: dict`,
  `approver_id`, `entity_type`, `entity_id`. Keep `actor_name` (joined in service).

## 3. `app/schemas/__init__.py`

Update exports for renamed/removed classes. Run `ruff check --select F401` to catch dead imports.

## Tests (`tests/schemas/`) — pure unit tests, no DB

Table-driven (`pytest.mark.parametrize`) for each schema: valid sample passes; each constraint fails
with a 422-style `ValidationError` (negative price, 3-digit PIN, `"12a4"` PIN, `Decimal("12345678901.00")`,
quantity 0, duplicate merge ids, dine-in without table, discount without reason, owner without password,
UPI payment without reference).

## Done when

All schema tests pass; OpenAPI regenerates (`pnpm generate:api` at repo root) and the TS client compiles —
fix any frontend type breaks or list them in the PR description for the app owners.
