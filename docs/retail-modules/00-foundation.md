# Retail modules: foundation (read this first)

Written 2026-11-16. This is the build plan for two new shop types, **dress shops** (`apparel`) and **hardware shops**
(`hardware`). It is written for the engineer or model that will implement it. Read this file fully, then
[01-apparel.md](01-apparel.md) and [02-hardware.md](02-hardware.md).

Build order: **stage F (this file) → apparel stages A1–A6 → hardware stages H1–H7.** Finish and commit one stage before
starting the next.

---

## 1. Decisions already made by the owner (do not reopen)

| # | Decision |
|---|---|
| 1 | Dress shops and hardware shops each get their **own backend module**: own package, own tables for products, stock, sales and payments, own endpoints. |
| 2 | Both live in the **same FastAPI service and the same Postgres database** as everything else. |
| 3 | They reuse only **platform pieces**: login, organizations, branches, staff, shifts and cash day, customers, audit log, idempotency, realtime events, the Pine Labs terminal helpers, and money/tax helper functions. |
| 4 | Restaurant, café, grocery and general retail **stay on the existing shared backend** (`orders`, `bills`, `menu_items`, `stock_levels`). Their behaviour must not change. |
| 5 | Only these two shop types for now. Do not add pharmacy, electronics or anything else. Do not build generic "feature flag" machinery for future types. |

**What "separate" means in practice:** `app/modules/apparel/` never imports from `app/modules/hardware/` and the reverse. Neither
writes to `orders`, `order_items`, `bills`, `payments`, `menu_items`, `categories`, `item_variants`, `stock_levels`,
`stock_purchases` or `stock_movements`. Both may import from `app/core/`, `app/models/` (platform models only), the modules
listed in section 4, and `app/modules/retailkit/` (section 5).

---

## 2. How the existing code is organised (follow these patterns exactly)

Paths are relative to `services/api-fastapi/`.

| Thing | Where | Pattern to copy |
|---|---|---|
| Module layout | `app/modules/inventory/` | `router.py` (thin, HTTP only) + `service.py` (all logic). Schemas in `app/schemas/<module>.py`, models in `app/models/<module>.py`. |
| Model base classes | `app/models/base.py` | `Base, UUIDPrimaryKeyMixin, TimestampMixin`; `enum_check(table, column, Enum)` for string enums. Register new models in `app/models/__init__.py`. |
| Enums | `app/core/enums.py` | `StrEnum`, lower-case values, stored as strings with a check constraint. |
| Money | `app/core/money.py` | `Decimal` only, `q()` rounds to paise half-up. Never use floats. |
| Auth | `app/core/auth.py` | `get_current_user`, `require_roles(Role.OWNER, Role.MANAGER)`, `require_branch_access`, `resolve_branch_id`, `resolve_approver` (manager PIN). |
| Tenancy | every query | Filter by `organization_id` or by a `branch_id` the user may access. Row-level security is the backstop, not the filter. |
| Row-level security | `alembic/versions/2026_11_12_0900-e5b9f7c32d84_inventory_module.py` | Every new table gets `ENABLE` + `FORCE ROW LEVEL SECURITY` and the `tenant_scope` policy. See section 7. |
| Idempotency | `app/modules/inventory/router.py` (`receive_purchase`) | `idempotent("scope")` guard on every POST that creates money or stock records. |
| Events | `app/core/events.py` | `queue_event(db, branch_id, Topic.X, "name", entity_id, payload)` then `await commit(db)` (never `db.commit()`). |
| Audit | `app/core/audit.py` | `record_audit(...)` for voids, high discounts, payment reversals, returns. |
| Numbering | `app/core/numbering.py` | `next_document_number(db, branch, "bill")` gives `{CODE}/{FY}/{000001}`. |
| Pagination | `app/core/pagination.py` | `PageParams`, `paginate`, `Page[...]` response. |
| Errors | `app/core/errors.py` | `NotFound`, `Conflict`, `ValidationFailed`, `InvalidTransition`, each with a stable `code=`. |
| Tests | `tests/modules/test_inventory.py` | Real Postgres and Redis, HTTP through the `client` fixture, helpers in `tests/factories.py` and `tests/flows.py`. |

Read these before writing any code: `app/modules/billing/service.py` (payments and terminal flow), `app/modules/billing/tax.py`,
`app/modules/billing/receipt.py`, `app/modules/inventory/service.py`, `app/modules/retail/service.py` (CSV import),
`app/core/business.py`, `tests/modules/test_business_categories.py`.

---

## 3. Things that are deliberately the same in both modules

Both modules follow the same shape so that anyone who knows one can read the other. They share the shape, not the code.

- **A sale is one document.** There is no separate order and bill. `POST /<module>/sales` takes the whole cart and creates a
  numbered invoice in one transaction. The cart lives on the device until then. Held or parked sales are out of scope.
- **Prices include GST by default.** Each module has a settings row per organization with `prices_include_tax` (default true).
- **Every sale line stores its own tax result:** `tax_rate`, `taxable_value`, `cgst`, `sgst`, `igst`, `line_total`. Reports and
  returns read these stored numbers and never recompute.
- **Stock may go negative.** A sale is never refused because the recorded count is behind the shelf (same rule as
  `StockLevel`).
- **Stock ledger is append-only** with `balance_after`, like `StockMovement`.
- **Invoice numbers** use `next_document_number(db, branch, "bill")`. An organization has exactly one category, so the series
  stays consecutive.
- **Sales carry `client_sale_id`** (nullable, unique per branch where not null), like `orders`, so offline billing can be added
  later. Offline billing itself is out of scope.

---

## 4. Platform pieces to reuse (import, do not copy or move)

| Need | Use |
|---|---|
| Customer on a sale | `app.models.customers.Customer`; look it up the way `orders/service.py::_customer_for_order` does. |
| Open shift for a payment | `app.modules.staff.service.get_open_shift` |
| Manager PIN approval | `app.core.auth.resolve_approver` |
| High-discount threshold | `settings.DISCOUNT_APPROVAL_THRESHOLD_PERCENT` |
| Terminal reference and safe details | `_terminal_ref`, `safe_terminal_details`, `mask_card_number` in `app/modules/billing/service.py`. Rename `_terminal_ref` to `terminal_ref` there (a pure rename) so it can be imported. |
| Print rows for thermal printers | `PrintRow`, `COLUMNS`, `clean`, `_pair`, `_wrap` from `app/modules/kots/slip.py`; `money` from `app/modules/billing/receipt.py`. |
| Business name and logo on a bill | `bill_name`, `load_with_logo`, `logo_data_url` in `app/modules/business/service.py` |
| Push alert to managers | `send_manager_push_alert` in `app/worker/tasks.py` |

---

## 5. Stage F: foundation work

This stage adds no shop features. It prepares the platform and the shared helper kit. Existing tests must pass unchanged at the
end of it.

### F1. New categories

- `app/core/enums.py`: add `APPAREL = "apparel"` and `HARDWARE = "hardware"` to `BusinessCategory`.
- `app/core/business.py`: add labels `"Dress / footwear shop"` and `"Hardware / electrical shop"`. Add presets for both with
  `table_service`, `kitchen_tickets`, `weighed_items`, `tokens` false and `quick_sale`, `barcode` true. These six settings are
  not read by the new modules; the presets exist only because the dictionaries must cover every category.
- Migration: recreate `ck_organizations_category` to include the two new values (see how
  `2026_11_07_0900-b8e4c2d61f35_business_categories.py` created it).
- `app/schemas/entitlements.py` comment and `packages/api-client/src/types.ts` `BusinessCategory` union: add both values.
- The platform console category picker is driven by `GET` in `app/modules/platform/orgs_router.py` (line ~174), so the new
  categories appear without console changes. Verify in the browser. For these two categories, hide the six setting overrides in
  `apps/platform-console/src/pages/SubscriberDetail.tsx`, since they have no effect.

### F2. Category guard

Add to `app/core/auth.py`:

```python
def require_category(*categories: BusinessCategory) -> Callable[..., Coroutine[Any, Any, CurrentUser]]:
    """The caller's organization must be one of these business categories (404 otherwise, so other shops cannot probe)."""
```

Implement it like `require_module`, reading `get_entitlements(...).category`. Raise `NotFound(code="wrong_business_category")`.
Each new router sets `dependencies=[Depends(require_category(BusinessCategory.APPAREL))]` (or `HARDWARE`).

Do not add category guards to the existing restaurant routers.

### F3. Cash-day sources

Cash day close in `app/modules/staff/service.py` sums `Payment` rows (`_close` near line 448, `close_cash_day` near line 578).
Dress and hardware sales write to their own payment tables, so their cash would be missing from the count.

- New file `app/core/cash.py`:

  ```python
  CashSource = Callable[..., Awaitable[Decimal]]
  # async def source(db, *, branch_id: UUID, since: datetime | None, shift_id: UUID | None) -> Decimal
  def register_cash_source(fn: CashSource) -> None: ...
  async def extra_cash(db, *, branch_id, since=None, shift_id=None) -> Decimal: ...  # sum of all registered sources
  ```

- In `staff/service.py`, add `await extra_cash(...)` to the cash total in both places. With `shift_id` for a personal shift,
  with `branch_id` and `since=shift.start_time` for the cash day.
- Each new module registers one source in its `__init__.py`. A source returns **net** cash: successful cash payments minus cash
  refunds.
- Test: with no sources registered the existing shift tests pass unchanged.

### F4. Numbering for credit notes and estimates

In `app/core/numbering.py`, extend `DocType` with `"credit_note"` and `"estimate"`. Both run per financial year like bills.
Formats: `CN/{CODE}/{FY}/{000001}` and `EST/{CODE}/{FY}/{000001}`. `document_counters.doc_type` is `String(20)` with no check
constraint, so no migration is needed.

### F5. Audit actions

Add to `AuditAction`: `SALE_RETURN = "sale_return"`, `PRICE_OVERRIDE = "price_override"` and
`CREDIT_LIMIT_OVERRIDE = "credit_limit_override"`. `audit_logs.action_type` has an enum check constraint, so the migration must
recreate it. Voids, high discounts and payment reversals reuse the existing
`BILL_VOID`, `HIGH_DISCOUNT` and `PAYMENT_REVERSE` with `entity_type` set to `"apparel_sale"` or `"hardware_sale"`.

### F6. The helper kit: `app/modules/retailkit/`

Rules for the kit: **no tables of its own, no routers, no knowledge of apparel or hardware.** It holds pure functions and
column mixins. If a helper needs an `if module == ...`, it does not belong in the kit.

| File | Contents |
|---|---|
| `tax.py` | Pure tax maths, below. |
| `tenders.py` | Payment column mixin and the tender state machine, below. |
| `barcode.py` | `ean13_check_digit(twelve: str) -> str`, `ean13_svg(code: str) -> str`, `code128_svg(text: str) -> str`. Pure, no dependencies. |
| `receipt.py` | `RetailReceipt` dataclass and `rows()`, `render_text()`, `render_html()` for 58 mm and 80 mm paper, built on the `PrintRow` helpers. Supports an optional detail line under each item (size and colour, or unit and rate), an HSN column, a tax summary by rate, IGST, "You saved" against MRP, customer name and GSTIN, and a document title (`TAX INVOICE`, `CREDIT NOTE`, `ESTIMATE`). |
| `csvimport.py` | Header normalising with aliases, row-by-row error collection, max-rows limit. Copy the approach from `app/modules/retail/service.py`; do not edit that file. |

**`tax.py`**

```python
@dataclass(frozen=True)
class LineIn:
    gross: Decimal          # quantity x unit price, before any discount, as entered (tax-inclusive or not)
    line_discount: Decimal  # discount on this line only
    tax_rate: Decimal       # percent

@dataclass(frozen=True)
class LineOut:
    discount: Decimal       # line discount + this line's share of the bill discount
    taxable_value: Decimal
    cgst: Decimal
    sgst: Decimal
    igst: Decimal
    total: Decimal          # taxable_value + taxes

@dataclass(frozen=True)
class InvoiceOut:
    lines: List[LineOut]
    subtotal: Decimal       # sum of gross
    discount: Decimal
    taxable_value: Decimal
    cgst: Decimal
    sgst: Decimal
    igst: Decimal
    round_off: Decimal
    grand_total: Decimal

def compute_invoice(lines, bill_discount, *, inclusive: bool, interstate: bool, round_to_rupee: bool) -> InvoiceOut
def allocate(amount: Decimal, weights: Sequence[Decimal]) -> List[Decimal]   # proportional, remainder to the largest
```

Rules:

1. Net of a line = `gross - line_discount`. The bill discount is allocated across lines in proportion to net (same method as
   `allocate_discount` in `billing/tax.py`).
2. `inclusive=True`: `taxable = q(net_after_discounts * 100 / (100 + rate))`, `tax = net_after_discounts - taxable`.
   `inclusive=False`: `taxable = net_after_discounts`, `tax = q(taxable * rate / 100)`.
3. `interstate=False`: `cgst = q(tax / 2)`, `sgst = tax - cgst`, `igst = 0`. `interstate=True`: `igst = tax`.
4. Tax is computed **per line** and summed, so stored line values always add up to the invoice totals.
5. `round_to_rupee`: `grand_total` is rounded to the nearest rupee and the difference is `round_off` (may be negative).
6. Validation: discounts are not negative and never exceed what they apply to; an empty invoice raises `ValidationFailed`.

Unit-test this file heavily (`tests/core/test_retailkit_tax.py`): inclusive and exclusive, mixed rates, discount remainders,
interstate, round-off, and the property "sum of lines equals totals".

**`tenders.py`**

- `PaymentColumns`: a declarative mixin with the same columns as `app/models/billing.py::Payment` except `bill_id`
  (`payment_mode`, `amount`, `reference_id`, `status`, `received_by_id`, `shift_id`, `idempotency_key`, `provider`,
  `provider_meta`). Use `@declared_attr` for the foreign keys. Each module's payment model adds its own `sale_id`, `branch_id`
  and check constraints.
- `Tenders`: a small class constructed with the module's sale model, payment model and two callbacks
  (`on_settled(db, sale)`, `on_reopened(db, sale)`), offering `record`, `split`, `start_terminal`, `complete_terminal` and
  `reverse_terminal`. It ports the logic of `record_payment`, `process_split_payment`, `start_terminal_payment`,
  `complete_terminal_payment` and `reverse_terminal_payment` from `billing/service.py` line for line: cash may exceed the
  balance and returns change, other modes may not; nothing else changes a sale while a terminal payment is pending; repeating
  the same terminal answer is harmless; reversal needs a manager PIN and is audited.
- The sale model must expose `id`, `branch_id`, `sale_number`, `grand_total`, `paid_amount`, `status`, `settled_at`, a
  `payments` relationship and a `balance_due` property. `Tenders` always reads `balance_due` and never computes it, because
  the hardware module also counts amounts charged to a customer's account. Sale statuses are the `BillStatus` values.
- The set of allowed payment modes belongs to each module (its own enum and check constraint). `Tenders` treats `cash` as the
  only mode that may exceed the balance and give change.

Do not refactor `billing/service.py` to use `Tenders`. The restaurant payment code stays as it is.

### F7. API client and app scaffolding

- `packages/api-client/src/client.ts`: change `private async request` to `async request` so module files can call it.
- Each module gets its own files: `packages/api-client/src/apparel.ts` and `hardware.ts` (types, adapter functions, client
  functions that take the shared `apiClient`, and TanStack Query hooks), exported from `index.ts`. Do not add to `client.ts`,
  `types.ts` or `hooks.ts` beyond the category union. Keep the existing convention: the API speaks rupee strings, the apps use
  integer paise, and conversion happens in the adapters.
- `apps/pos-app/src/retail/RetailApp.tsx`: after the restaurant check, route by category. `apparel` renders `<ApparelApp />`
  from `src/apparel/`, `hardware` renders `<HardwareApp />` from `src/hardware/`. In stage F both are placeholder screens that
  show the business name and "Coming soon".
- `apps/pos-app/src/retailkit/`: shared counter UI used by both module apps. In stage F create only `PayPanel.tsx`, modelled on
  the payment half of `src/screens/BillScreen.tsx` (cash with change, UPI, card via the Pine Labs terminal with the pending and
  resolve flow, split, reversal, print). It takes a `TenderApi` object (functions for record, split, start terminal, complete
  terminal, reverse, fetch receipt) instead of calling hooks itself, so each module passes its own endpoints. Do not change
  `BillScreen.tsx`.
- `apps/admin-app/src/screens/CatalogHub.tsx` and `App.tsx`: for the two new categories, show the module's own screens (added in
  later stages) instead of `MenuBuilder` and `StockScreen`. Placeholder in stage F.
- Seed scripts: `scripts/seed_apparel.py` and `scripts/seed_hardware.py`, modelled on `scripts/seed_shop.py`. They grow with
  each stage and are what you use to check screens in the browser.

### Stage F is done when

- The full existing test suite passes in normal mode and with `RLS_TEST_ROLE=rls_app`, with no existing test edited.
- New tests pass: category guard, cash source registry, numbering formats, `retailkit/tax.py`, `retailkit/barcode.py`.
- `ruff check` and `mypy app` are clean; `pnpm typecheck` passes.
- In the browser, a subscriber created as "Dress / footwear shop" opens the retail app on the placeholder screen, and a
  restaurant and a grocery subscriber behave exactly as before.

---

## 6. Rules for every stage

1. **Backend first.** Models, migration, schemas, service, router, tests. Only then the API client, then screens.
2. **One migration per stage**, named like the existing ones (`YYYY_MM_DD_HHMM-<rev>_<slug>.py`), with a working `downgrade`.
   `tests/test_migrations.py` must pass.
3. **Tests for every endpoint**, including: wrong category gets 404, another organization's ids get 404, a cashier cannot call
   manager endpoints, and repeating a request with the same idempotency key does not duplicate anything.
4. **Run both test modes** before calling a stage done: plain `pytest`, and `RLS_TEST_ROLE=rls_app pytest`.
5. **Verify screens in the browser** with the `api`, `pos-retail`, `admin-web` and `platform-console` entries in
   `.claude/launch.json`, using the seed script. Use the Pine Labs simulator (`EXPO_PUBLIC_PINELABS_SIMULATOR=1`) for card
   payments.
6. **Update `docs/api.md`** with a section per module as endpoints land.
7. **Commit per stage** with a message like the existing history (`feat(apparel): products, variants and barcodes (A1)`).
8. **Do not touch** restaurant, café or grocery behaviour. If a stage seems to need an edit to `orders`, `billing`, `catalog`,
   `inventory`, `kots` or `floor`, stop and re-read this file; the only allowed edits to existing modules are the ones listed
   in stage F.

## 7. Row-level security checklist for new tables

- **Every** new table, including line tables, has either `organization_id` or `branch_id` as a real column. Do not rely on a
  parent table's policy.
- In the migration, for each table:

  ```python
  op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
  op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
  op.execute(f'CREATE POLICY tenant_scope ON "{table}" USING ({SCOPE}) WITH CHECK ({SCOPE})')
  ```

  with `SCOPE = 'app_org_scope() IS NULL OR organization_id = app_org_scope()'` for organization tables, and the branch form
  from the inventory migration for branch tables.
- Add each new table to a coverage test in `tests/modules/test_rls.py` style: two organizations, a restricted role, and a count
  that proves each sees only its own rows.

## 8. Known gaps left on purpose

These are accepted for the first version. Do not build them unless the owner asks.

- **Owner analytics** (`LivePulse`, `InsightsScreen`, `ZReports` in the admin app) read `bills`, so they show nothing for these
  shops. Each module ships its own day report instead.
- **Customer visit and spend figures** in the shared customers screen read `orders`, so they show zero. Each module has its own
  "sales for this customer" list.
- **Offline billing**, **stock transfers between branches**, **supplier records and purchase orders**, **loyalty**,
  **e-invoicing and e-way bills**, **card refunds for returns** (returns refund in cash or credit only).
