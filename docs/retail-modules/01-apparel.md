# Dress shop module (`apparel`)

Read [00-foundation.md](00-foundation.md) first; stage F must be finished. This file is the full plan for dress, garment and
footwear shops.

**What a dress shop needs that the existing retail app cannot do:** one style sold in many sizes and colours, each with its own
barcode and its own stock count; MRP with a lower selling price; GST that depends on the price of the piece; and returns and
exchanges.

- Backend package: `services/api-fastapi/app/modules/apparel/`
- Models: `app/models/apparel.py`, schemas: `app/schemas/apparel.py`
- Route prefix: `/api/v1/apparel`, guarded by `require_category(BusinessCategory.APPAREL)`
- Tables: all prefixed `apparel_`
- POS screens: `apps/pos-app/src/apparel/`, admin screens: `apps/admin-app/src/screens/apparel/`
- API client: `packages/api-client/src/apparel.ts`

---

## 1. Words used here

| Word | Meaning |
|---|---|
| **Product** | A style: "Men's Oxford Shirt". It is never sold directly. |
| **Variant** | One size and colour of a product: "Oxford Shirt, M, Blue". This is what is scanned, sold and counted. |
| **Sale** | The invoice. Created complete, with a number, in one request. |
| **Return** | Goods coming back against a sale. It produces a credit note document. |
| **Store credit** | The value of a return kept by the shop for the customer to spend later. An exchange is a return to store credit followed by a new sale paid with it. |

---

## 2. Tables

Every table has `id` (UUID), `created_at`, `updated_at` from the mixins unless noted. Money is `Numeric(10, 2)`. Quantities are
`Integer` (garments are sold in whole pieces). The **Scope** column says which tenant column the table carries for row-level
security.

### `apparel_settings` (Scope: organization)

One row per organization, created on first read with defaults. Primary key is `organization_id`.

| Column | Type | Default | Meaning |
|---|---|---|---|
| `prices_include_tax` | bool | true | Variant prices already contain GST. |
| `round_to_rupee` | bool | true | Invoice total rounds to the nearest rupee. |
| `low_rate` | Numeric(5,2) | 5.00 | GST rate at or below the slab price. |
| `slab_price` | Numeric(10,2) | 2500.00 | Per-piece taxable value where the rate changes. |
| `high_rate` | Numeric(5,2) | 18.00 | GST rate above the slab price. |
| `return_days` | int | 7 | Returns after this many days need a manager PIN. 0 means no limit. |
| `next_barcode` | bigint | 1 | Counter for generated barcodes. |

The three tax values are settings, not constants, because the government changes them. The defaults reflect the rates as
understood on 2026-11-16; **the owner should confirm them with an accountant before going live.**

### `apparel_categories` (Scope: organization)

`organization_id`, `name` String(100), `sort_order` int, `is_active` bool. Unique on (`organization_id`, lower(`name`)).

### `apparel_products` (Scope: organization)

| Column | Type | Notes |
|---|---|---|
| `organization_id` | UUID FK | |
| `category_id` | UUID FK `apparel_categories` | |
| `name` | String(150) | |
| `brand` | String(100) null | |
| `style_code` | String(64) null | Unique per organization where not null. The key CSV import groups by. |
| `hsn_code` | String(8) null | 4 to 8 digits. |
| `tax_mode` | String(10) | `slab` (default) or `fixed`. |
| `tax_rate` | Numeric(5,2) null | Required when `tax_mode = 'fixed'` (belts, bags, accessories). |
| `description` | Text null | |
| `is_active` | bool | Soft delete only; products are referenced by sale history. |

### `apparel_variants` (Scope: organization)

| Column | Type | Notes |
|---|---|---|
| `organization_id` | UUID FK | Carried here so unique indexes and RLS do not need a join. |
| `product_id` | UUID FK | |
| `size` | String(20) | "M", "32", "Free". Empty string is not allowed; use "Free". |
| `colour` | String(40) | "Blue". Use "Default" when the style has one colour. |
| `sku` | String(64) null | Unique per organization where not null. |
| `barcode` | String(64) null | Unique per organization where not null. |
| `mrp` | Numeric(10,2) null | Printed price. |
| `price` | Numeric(10,2) | Selling price. Check: `price >= 0` and (`mrp IS NULL OR price <= mrp`). |
| `sort_order` | int | Keeps sizes in the order the owner typed them (S, M, L, XL). |
| `is_active` | bool | |

Unique on (`product_id`, `size`, `colour`).

### `apparel_stock` (Scope: branch)

`branch_id`, `variant_id`, `quantity` int (may be negative), `low_stock_level` int null, `cost_price` Numeric(10,2) null. Unique on
(`branch_id`, `variant_id`). A missing row means zero on hand; create rows lazily, locked in a fixed order (sorted variant id)
to avoid deadlocks, as `inventory/service.py::_lock_levels` does.

### `apparel_purchases` (Scope: branch)

Same columns as `stock_purchases`: `branch_id`, `supplier_name`, `invoice_number`, `received_on`, `notes`, `total_cost`,
`created_by_id`.

### `apparel_stock_movements` (Scope: branch)

Append-only, no `updated_at`. `branch_id`, `variant_id`, `kind`, `quantity` (signed int, not zero), `balance_after`, `unit_cost`
null, `reason` String(500) null, `purchase_id` null, `sale_id` null, `return_id` null, `created_by_id`, `created_at`.

`kind` is one of `purchase`, `sale`, `sale_void`, `return`, `adjustment`, `count` (enum `ApparelMovementKind`). Index on
(`branch_id`, `variant_id`, `created_at`).

### `apparel_sales` (Scope: branch)

| Column | Type | Notes |
|---|---|---|
| `branch_id` | UUID FK | |
| `sale_number` | String(50) | From `next_document_number(db, branch, "bill")`. Unique per branch. |
| `business_date` | Date | Branch-local date. |
| `customer_id` | UUID FK `customers` null | |
| `salesperson_id` | UUID FK `staff_users` null | The floor staff who made the sale, for commission reports. |
| `created_by_id` | UUID FK `staff_users` | The cashier. |
| `subtotal` | money | Sum of quantity x unit price. |
| `discount_amount` | money | Line discounts plus the bill discount. |
| `discount_reason` | Text null | |
| `discount_approved_by_id` | UUID null | |
| `taxable_value`, `tax_cgst`, `tax_sgst`, `tax_igst`, `round_off`, `grand_total`, `paid_amount` | money | `round_off` may be negative. |
| `status` | String(30) | `BillStatus` values: `unpaid`, `partially_paid`, `paid`, `voided`. |
| `settled_at`, `voided_by_id`, `void_reason` | | |
| `client_sale_id` | String(64) null | Unique per branch where not null. |
| `notes` | Text null | |

### `apparel_sale_lines` (Scope: branch)

`branch_id`, `sale_id`, `variant_id`, snapshots (`name_snapshot`, `size_snapshot`, `colour_snapshot`, `hsn_snapshot`,
`barcode_snapshot`), `quantity` int > 0, `mrp_snapshot` null, `unit_price`, `line_discount`, `discount` (line discount plus the
allocated share of the bill discount), `tax_rate`, `taxable_value`, `cgst`, `sgst`, `igst`, `line_total`, `returned_quantity` int
default 0 (check: `returned_quantity <= quantity`).

### `apparel_payments` (Scope: branch)

`PaymentColumns` from the kit, plus `branch_id`, `sale_id` FK, `credit_note_id` FK null. `payment_mode` is one of `cash`, `card`,
`upi`, `credit_note` (enum `ApparelPaymentMode`). Unique on (`sale_id`, `idempotency_key`) where the key is not null. Check:
`credit_note_id IS NOT NULL` exactly when `payment_mode = 'credit_note'`.

### `apparel_returns` (Scope: branch)

`branch_id`, `return_number` (from `next_document_number(db, branch, "credit_note")`, unique per branch), `sale_id`,
`business_date`, `reason` Text, `refund_mode` (`cash` or `store_credit`), `total_amount`, `taxable_value`, `tax_cgst`, `tax_sgst`,
`tax_igst`, `created_by_id`, `approved_by_id` null, `shift_id` null.

### `apparel_return_lines` (Scope: branch)

`branch_id`, `return_id`, `sale_line_id`, `quantity` int > 0, `amount`, `taxable_value`, `cgst`, `sgst`, `igst`, `restock` bool
(false for damaged goods that do not go back on the shelf).

### `apparel_store_credits` (Scope: organization)

`organization_id`, `return_id` FK unique, `code` String(50) (the return number, printed on the credit note), `customer_id` null,
`amount`, `balance`, `status` (`open`, `used`, `cancelled`). Check: `0 <= balance <= amount`. Store credit is valid at every
branch of the business.

---

## 3. Rules

### 3.1 Tax rate of a line

1. Product `tax_mode = 'fixed'`: the rate is the product's `tax_rate`.
2. Product `tax_mode = 'slab'`:
   - `net_per_piece` = (quantity x unit price - line discount - this line's share of the bill discount) / quantity.
   - If prices include tax: `value = net_per_piece * 100 / (100 + low_rate)`. Otherwise `value = net_per_piece`.
   - If `value <= slab_price` the rate is `low_rate`, otherwise `high_rate`. Compare the unrounded value.

The bill discount share does not depend on the rate, so: allocate the bill discount first with `retailkit.tax.allocate`, pick
each line's rate, then call `retailkit.tax.compute_invoice` (it allocates the same way). Keep the rate choice in
`app/modules/apparel/tax.py` as a pure function with its own unit tests. Always pass `interstate=False`; dress shops sell to
walk-in customers.

Worked examples with the default settings and prices including tax (these must be test cases):

| Line | Rate | Taxable | CGST | SGST |
|---|---|---|---|---|
| Shirt at 999.00 | 5% | 951.43 | 23.79 | 23.78 |
| Kurta at 2,999.00 | 18% | 2,541.53 | 228.74 | 228.73 |
| Kurta at 2,999.00 with a 15% line discount (net 2,549.15) | 5% | 2,427.76 | 60.70 | 60.69 |
| Piece at exactly 2,625.00 | 5% | 2,500.00 | 62.50 | 62.50 |
| Piece at 2,625.01 | 18% | 2,224.58 | 200.22 | 200.21 |
| Two shirts at 1,400.00 each on one line | 5% (decided per piece) | 2,666.67 | 66.67 | 66.66 |

### 3.2 Creating a sale

`POST /apparel/sales` with lines (`variant_id`, `quantity`, optional `unit_price`, optional `line_discount`), an optional bill
discount with reason, optional `customer_id`, `salesperson_id`, `client_sale_id`, `approver_pin`.

In one transaction:

1. Load variants and their products for this organization; refuse inactive ones (`code="variant_inactive"`).
2. `unit_price` defaults to the variant's price. A different price needs a manager PIN and writes a `PRICE_OVERRIDE` audit
   row. It may never exceed MRP.
3. Compute tax (3.1). If total discount is above `DISCOUNT_APPROVAL_THRESHOLD_PERCENT` of the subtotal, require a manager PIN
   and write a `HIGH_DISCOUNT` audit row, as `generate_bill` does.
4. Allocate the invoice number, insert the sale and lines.
5. Take the quantities out of stock: one `sale` movement per line.
6. Queue `apparel.sale.created` on `Topic.BILLING` and `apparel.stock.changed` on `Topic.CATALOG`.

If `client_sale_id` matches an existing sale in the branch, return that sale (see `orders/service.py::_order_for_client_sale`).
The endpoint also takes the `idempotent("apparel_sales")` guard.

### 3.3 Paying

Endpoints mirror the bill endpoints and are implemented with `retailkit.tenders.Tenders`: single payment, split, terminal
start, terminal complete, terminal reverse. `on_settled` queues `apparel.sale.settled`; nothing else happens on settlement
because stock already moved.

Paying with store credit: `payment_mode = "credit_note"` with `credit_note_code`. Lock the store credit row, require
`status = 'open'` and `balance > 0`, apply `min(balance, amount, sale balance)`, reduce the balance, and set `status = 'used'` at
zero. Store credit never gives change in cash.

### 3.4 Voiding

Only an unpaid sale with nothing paid can be voided, with a manager PIN (same rule and audit as `void_bill`). Voiding puts the
stock back with `sale_void` movements. A paid sale is undone with a return.

### 3.5 Returns and exchanges

`POST /apparel/returns` with `sale_id`, lines (`sale_line_id`, `quantity`, `restock`), `reason`, `refund_mode`, optional
`approver_pin`.

1. The sale must be `paid`. Each quantity must be at most `quantity - returned_quantity` on its line.
2. If the sale is older than `return_days`, require a manager PIN.
3. Amounts come from the stored sale line, proportionally: `amount = q(line_total * qty / line.quantity)`, and the same for
   `taxable_value`, `cgst`, `sgst`. When a return takes the last pieces of a line, use what is left of the line's values so
   that the returns add up to the line exactly.
4. Allocate the credit note number, insert the return and lines, increase `returned_quantity`.
5. `restock = true` lines go back into stock with `return` movements.
6. `refund_mode = 'cash'`: record `shift_id` from the cashier's open shift. The cash source for the cash day (foundation F3)
   subtracts cash returns. `refund_mode = 'store_credit'`: create an `apparel_store_credits` row for the amount.
7. Write a `SALE_RETURN` audit row. Queue `apparel.return.created`.

An **exchange** is done by the POS as two calls: a return to store credit, then a new sale paid with that credit plus any
difference. No special server endpoint.

### 3.6 Barcodes

When a variant is saved without a barcode, generate one: `"29"` + `next_barcode` zero-padded to 10 digits + the EAN-13 check
digit (`retailkit.barcode.ean13_check_digit`). The prefix 20–29 is the range reserved for in-store codes. Take `next_barcode`
with `SELECT ... FOR UPDATE` on the settings row. Manufacturer barcodes typed or scanned by the owner are kept as they are.

### 3.7 Who may do what

| Action | Roles |
|---|---|
| Read products, search, look up a barcode, read stock counts | any signed-in staff |
| Create sales, take payments, create returns | owner, manager, cashier |
| Products, variants, categories, settings, import, purchases, adjustments, labels, reports | owner, manager |
| Cost prices and stock value | owner, manager (hide from others, as inventory does) |

---

## 4. Endpoints

All under `/api/v1/apparel`.

| Method and path | Purpose |
|---|---|
| `GET /settings`, `PUT /settings` | Module settings. |
| `GET /categories`, `POST /categories`, `PATCH /categories/{id}` | Categories. |
| `GET /products` | Paged list for the admin app: `search`, `category_id`, `include_inactive`. Each product has its variants. |
| `POST /products` | Create a product with its variant grid: `sizes: [...]`, `colours: [...]`, `price`, `mrp`, or an explicit `variants: [...]` list. |
| `GET /products/{id}`, `PATCH /products/{id}` | Read (with variants and, given `branch_id`, stock per variant) and update. |
| `POST /products/{id}/variants`, `PATCH /variants/{id}` | Add a size or colour later; change price, MRP, barcode, active. |
| `GET /variants/lookup?code=` | Exact barcode or SKU match for the scanner. 404 `barcode_not_found`. Barcode match wins over SKU. |
| `GET /catalog/search?q=&category_id=&branch_id=&limit=` | For the POS: products matching name, brand, style code, with variants and stock. Server-side, max 50 results. |
| `POST /import` | CSV import with `dry_run`, section 5. |
| `GET /stock` | Stock on hand for a branch: `search`, `low_only`, `category_id`. |
| `PUT /stock/{variant_id}` | Low-stock level and cost price. |
| `POST /purchases`, `GET /purchases`, `GET /purchases/{id}` | Stock received. Idempotent. |
| `POST /adjustments` | `delta` or `counted`, with a reason. Idempotent. |
| `GET /movements` | The ledger, paged, filter by variant and kind. |
| `POST /sales` | Create a sale. Idempotent. |
| `GET /sales`, `GET /sales/{id}` | List (filters: `on_date`, `status`, `customer_id`, `search` on sale number) and read with lines and payments. |
| `POST /sales/{id}/payments`, `POST /sales/{id}/payments/split` | Record tenders. |
| `POST /sales/{id}/payments/terminal`, `POST /payments/{id}/terminal/complete`, `POST /payments/{id}/reverse` | Pine Labs flow. |
| `POST /sales/{id}/void` | Void an unpaid sale. |
| `GET /sales/{id}/receipt?format=rows\|text\|html&paper_mm=` | Printed invoice. Match the shapes of the existing bill receipt endpoints so the POS printing code works unchanged. |
| `POST /returns`, `GET /returns`, `GET /returns/{id}`, `GET /returns/{id}/receipt` | Returns and the printed credit note. |
| `GET /store-credits/{code}` | Balance check at the counter. |
| `POST /labels` | Body: `[{variant_id, copies}]`. Returns a printable HTML page of barcode labels (section 6). |
| `GET /reports/day?branch_id=&date=` | Day report (section 7). |
| `GET /reports/sales-by-product?from=&to=` | Units and value by product and by size. |
| `GET /customers/{id}/sales` | A customer's purchases. |

---

## 5. CSV import

One row per variant. Columns: `style_code`, `name`, `category`, `brand`, `hsn`, `size`, `colour`, `mrp`, `price`, `barcode`, `sku`,
`gst_rate`, `opening_stock`.

- Required: `name`, `category`, `size`, `price`. `colour` defaults to "Default".
- Rows belong to the same product when `style_code` matches, or, without a style code, when category and name match.
- `gst_rate` blank means `tax_mode = 'slab'`; a value means `fixed` at that rate. All rows of one product must agree.
- `opening_stock` needs a `branch_id` in the request and creates `count` movements. It is applied only to variants created by
  this import, never to existing ones.
- Matching an existing variant: by SKU, then barcode, then (product, size, colour). A barcode or SKU may not move to a
  different variant.
- Behaviour copied from the existing import: every row is validated, bad rows are reported with line number and reasons, good
  rows are imported in one transaction, `dry_run` saves nothing, limit 10,000 rows.

---

## 6. Barcode labels

`POST /labels` returns an HTML page sized for A4 sticker sheets: a grid of labels, each with the business name, product name,
size and colour, MRP, selling price when lower, and the barcode as inline SVG (`retailkit.barcode`: EAN-13 when the code is 13
digits with a valid check digit, Code 128 otherwise). The admin app opens it in the browser print dialog. Request options:
`columns` (default 3) and `label_height_mm` (default 30). Dedicated label printers are out of scope.

---

## 7. Day report

`GET /reports/day` returns, for one branch and business date:

- number of sales, units sold, subtotal, discounts, taxable value, CGST, SGST, round-off, grand total (paid sales only);
- tax summary by rate (taxable value and tax per rate);
- payments by mode (successful payments, including `credit_note`);
- returns: count, total, split by refund mode;
- net cash = cash payments minus cash refunds;
- top 10 products by value, and units by size.

This replaces the Z-report for dress shops. It is computed live; archiving is out of scope.

---

## 8. Screens

### POS app (`apps/pos-app/src/apparel/`)

| File | What it does |
|---|---|
| `ApparelApp.tsx` | Route state like `RetailApp.tsx`: sale, pay, returns, day end, printer. Reuses `DayEndScreen`, `KotPrinterScreen` (printer settings), `CustomerSheet`, `TopBar`. |
| `SaleScreen.tsx` | Scan-first. A search box that also receives keyboard-scanner input (copy the handling from `QuickSaleScreen.tsx`); a scan calls `/variants/lookup` and adds the variant directly. Typing searches `/catalog/search` and lists products; tapping one opens the picker. The cart shows name, size and colour, MRP struck through when the price is lower, quantity stepper, line discount, and totals from the server-side rules mirrored in `packages/shared-utils` for display only. The server's numbers are the truth after the sale is created. |
| `VariantPickerSheet.tsx` | A grid with sizes as columns and colours as rows. Each cell shows the stock on hand and is tappable; out-of-stock cells are dimmed but still selectable. |
| `apparelCartStore.ts` | Zustand store like `cartStore.ts`, keyed by variant. |
| `PayScreen.tsx` | Sale summary plus `retailkit/PayPanel` with the apparel `TenderApi`. Adds a "Store credit" tender that asks for the credit note code and shows its balance. |
| `ReturnsScreen.tsx` | Find a sale by number, by scanning the invoice number, or by customer phone. Tick lines and quantities, choose "restock" per line, reason, and "Cash refund" or "Exchange". Exchange creates the store credit and returns to `SaleScreen` with the credit pre-applied. |
| `DayScreen.tsx` | The day report for managers. |

### Admin app (`apps/admin-app/src/screens/apparel/`)

| File | What it does |
|---|---|
| `ProductsScreen.tsx` | List with search and category chips; product editor with name, brand, category, HSN, tax mode; a size list and a colour list that build the variant grid; per-variant price, MRP, barcode. |
| `ImportSheet.tsx` | CSV import with template download and per-row errors, modelled on `components/ImportItemsSheet.tsx`. |
| `StockScreen.tsx` | Stock by product as a size by colour grid; receive a delivery; adjust or count; low-stock list. Modelled on `screens/StockScreen.tsx`. |
| `LabelsScreen.tsx` | Pick variants and copies (or "all from the last delivery") and print. |
| `ReportsScreen.tsx` | Day report and sales by product and size. |
| `SettingsCard.tsx` | The module settings, in `SettingsScreen.tsx`. |

---

## 9. Stages

Each stage ends with: tests green in both modes, lint and type checks clean, screens checked in the browser, `docs/api.md`
updated, one commit.

| Stage | Scope | Done when |
|---|---|---|
| **A1 Catalogue** | Settings, categories, products, variants, barcode generation, lookup, catalog search, CSV import. Admin `ProductsScreen` and `ImportSheet`. Seed script creates a demo shop with about 30 products. | Creating a product with 4 sizes and 3 colours makes 12 variants with unique generated barcodes; a 1,000-row CSV imports with per-row errors; a scanned code finds its variant. |
| **A2 Stock** | Stock, purchases, adjustments, counts, ledger. Admin `StockScreen`. | Receiving a delivery, adjusting and counting produce the right balances and ledger rows; cost is hidden from cashiers. |
| **A3 Sales** | `apparel/tax.py`, sales, payments through `Tenders`, void, receipt, cash-day source. No screens. | Every row of the worked examples table passes; cash, split and terminal payments settle a sale; void restores stock; closing the cash day counts apparel cash; a receipt renders at 58 and 80 mm. |
| **A4 Counter** | POS `ApparelApp`, `SaleScreen`, `VariantPickerSheet`, `PayScreen`, day end. | In the browser with the simulator: scan two variants, pick a third from the grid, give a line discount, pay part cash and part card, print the invoice, close the day with the right expected cash. |
| **A5 Returns** | Returns, store credit, credit note receipt, POS `ReturnsScreen`. | Partial return for cash reduces expected cash; an exchange for a dearer item collects only the difference; a second return of the same pieces is refused; returns of a whole line add up to the line exactly. |
| **A6 Labels and reports** | Labels, day report, sales by product, customer sales list. Admin `LabelsScreen`, `ReportsScreen`, POS `DayScreen`. | A printed label sheet scans correctly at the counter; the day report matches the sum of the day's sales, returns and payments in a test with mixed rates. |

## 10. Tests to write (minimum)

`tests/modules/test_apparel_catalog.py`, `test_apparel_stock.py`, `test_apparel_sales.py`, `test_apparel_returns.py`,
`test_apparel_reports.py`, and `tests/core/test_apparel_tax.py`.

- The category guard: a restaurant or hardware organization gets 404 on every apparel route.
- Tenant isolation: organization B cannot read, sell or return organization A's variants, sales or store credits.
- RLS coverage for every `apparel_` table.
- The tax table in 3.1, plus a bill discount that moves a line across the slab.
- Stored line values sum to the sale totals for a 10-line mixed sale with a bill discount.
- Idempotency on sales, payments, purchases, adjustments and returns.
- Duplicate `client_sale_id` returns the first sale.
- Store credit: partial use leaves a balance; overuse is refused; a used credit cannot be used again; concurrent use of one
  credit by two sales cannot overspend it.
- The existing restaurant and grocery suites still pass untouched.

## 11. Out of scope for this module

Alterations and tailoring orders, loyalty points, gift vouchers sold at the counter, size charts, product photos, season and
collection tagging, commission calculation (only `salesperson_id` is recorded), stock transfer between branches, interstate
sales, card refunds on returns.
