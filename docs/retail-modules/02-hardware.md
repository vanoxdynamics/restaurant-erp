# Hardware shop module (`hardware`)

Read [00-foundation.md](00-foundation.md) first; stage F must be finished. Build this after the dress shop module
([01-apparel.md](01-apparel.md)) so that the helper kit and the shared counter UI have already been used once. This module
must not import from `app/modules/apparel/`.

**What a hardware or electrical shop needs that the existing retail app cannot do:** selling one product in several units
(a coil or a metre of cable, a box or a piece of screws); cut lengths and loose quantities; negotiated and wholesale prices;
estimates that become invoices; customers who buy on credit and pay later; and GST invoices for business buyers, including
buyers in another state.

- Backend package: `services/api-fastapi/app/modules/hardware/`
- Models: `app/models/hardware.py`, schemas: `app/schemas/hardware.py`
- Route prefix: `/api/v1/hardware`, guarded by `require_category(BusinessCategory.HARDWARE)`
- Tables: all prefixed `hardware_`
- POS screens: `apps/pos-app/src/hardware/`, admin screens: `apps/admin-app/src/screens/hardware/`
- API client: `packages/api-client/src/hardware.ts`

---

## 1. Words used here

| Word | Meaning |
|---|---|
| **Product** | One sellable thing at one size: "PVC Elbow 1/2 inch", "Copper Wire 2.5 sq mm". Each size is its own product; there are no variants in this module. |
| **Base unit** | The unit stock is counted in and the price is set for: metre for wire, piece for screws. |
| **Alternate unit** | Another unit the product is bought or sold in, with a factor: 1 coil = 90 m, 1 box = 100 pcs. |
| **Price tier** | `retail` or `wholesale`. Chosen per sale, defaulting from the customer's account. |
| **Estimate** | A quotation. It reserves nothing and moves no stock until it is converted into a sale. |
| **Account** | A customer's credit record (khata): limit, balance owed, and a ledger of every change. |

---

## 2. Tables

Every table has `id`, `created_at`, `updated_at` unless noted. Money is `Numeric(10, 2)` except balances and totals that can
grow large, which are `Numeric(12, 2)`. Quantities are `Numeric(14, 3)`.

### `hardware_settings` (Scope: organization)

Primary key `organization_id`, created on first read.

| Column | Type | Default | Meaning |
|---|---|---|---|
| `prices_include_tax` | bool | true | Product prices already contain GST. |
| `round_to_rupee` | bool | true | Invoice total rounds to the nearest rupee. |
| `default_tax_rate` | Numeric(5,2) | 18.00 | Pre-filled on new products. |
| `estimate_valid_days` | int | 15 | Default validity of an estimate. |
| `state_code` | String(2) null | null | The shop's GST state code, used when a branch has no GSTIN. |

### `hardware_categories` (Scope: organization)

`organization_id`, `name`, `sort_order`, `is_active`. Unique on (`organization_id`, lower(`name`)).

### `hardware_products` (Scope: organization)

| Column | Type | Notes |
|---|---|---|
| `organization_id`, `category_id` | UUID FK | |
| `name` | String(150) | |
| `brand` | String(100) null | |
| `size_spec` | String(60) null | Free text printed after the name: "1/2 inch", "2.5 sq mm", "6A". |
| `sku`, `barcode` | String(64) null | Each unique per organization where not null. |
| `hsn_code` | String(8) null | |
| `tax_rate` | Numeric(5,2) | 0 to 40. |
| `base_unit` | String(10) | `HardwareUnit` value. |
| `allow_fraction` | bool | True for cut lengths and loose goods. False means the base quantity must be a whole number. |
| `price` | money | Retail price per base unit. |
| `wholesale_price` | money null | Per base unit. Null means wholesale buyers pay the retail price. |
| `min_price` | money null | Per base unit. Selling below it needs a manager PIN. |
| `mrp` | money null | Per base unit. A line price may not exceed it. |
| `is_active` | bool | Soft delete only. |

`HardwareUnit` (new enum in `app/core/enums.py`): `pcs`, `box`, `pkt`, `dozen`, `set`, `pair`, `bag`, `bundle`, `roll`, `coil`, `kg`,
`g`, `m`, `ft`, `sqft`, `l`, `ml`.

### `hardware_product_units` (Scope: organization)

`organization_id`, `product_id`, `unit`, `factor` Numeric(12,4) > 0 (how many base units one of this unit holds), `price` null,
`wholesale_price` null (both per this unit; null means base price x factor), `barcode` null (unique per organization where not
null). Unique on (`product_id`, `unit`); the unit may not equal the product's base unit. The service must also refuse a
barcode that already exists on a product or on another unit row.

### `hardware_stock` (Scope: branch)

`branch_id`, `product_id`, `quantity` (base unit, may be negative), `low_stock_level` null, `cost_price` null (per base unit).
Unique on (`branch_id`, `product_id`).

### `hardware_purchases` (Scope: branch) and `hardware_stock_movements` (Scope: branch)

As in `stock_purchases` and `stock_movements`. Movement quantities are always in the base unit. `kind` is one of `purchase`,
`sale`, `sale_void`, `return`, `adjustment`, `count` (enum `HardwareMovementKind`). Movements also carry `sale_id` and `return_id`.

### `hardware_accounts` (Scope: organization)

| Column | Type | Notes |
|---|---|---|
| `organization_id` | UUID FK | |
| `customer_id` | UUID FK `customers`, unique | One account per customer. |
| `price_tier` | String(10) | `retail` (default) or `wholesale`. |
| `credit_limit` | Numeric(12,2) | Default 0, which means no credit. |
| `balance` | Numeric(12,2) | What the customer owes. Negative means the shop holds an advance. |
| `state_code` | String(2) null | Place of supply when the customer has no GSTIN. |
| `is_active` | bool | |

### `hardware_ledger_entries` (Scope: organization)

Append-only, no `updated_at`. `organization_id`, `customer_id`, `branch_id`, `kind` (`opening`, `sale`, `receipt`, `return`,
`adjustment`), `amount` (signed; positive increases what the customer owes), `balance_after`, `sale_id` null, `payment_id` null,
`return_id` null, `note` String(500) null, `created_by_id`, `created_at`. Index on (`customer_id`, `created_at`).

Every change to `hardware_accounts.balance` writes exactly one ledger entry in the same transaction, with the account row
locked `FOR UPDATE`.

### `hardware_sales` (Scope: branch)

The same columns as `apparel_sales` in [01-apparel.md](01-apparel.md) section 2 (number, date, customer, cashier, totals,
status, void fields, `client_sale_id`, notes), without `salesperson_id`, plus:

| Column | Type | Notes |
|---|---|---|
| `price_tier` | String(10) | The tier this sale was priced at. |
| `on_account_amount` | Numeric(12,2) | The part charged to the customer's account. Default 0. |
| `buyer_gstin` | String(15) null | Snapshot of the customer's GSTIN at the time of sale. |
| `place_of_supply` | String(2) null | Buyer's state code, when known. |
| `is_interstate` | bool | True means IGST was charged. |
| `estimate_id` | UUID FK null | The estimate this sale came from. |

The model's `balance_due` property is `grand_total - paid_amount - on_account_amount`.

### `hardware_sale_lines` (Scope: branch)

`branch_id`, `sale_id`, `product_id`, snapshots (`name_snapshot`, `size_spec_snapshot`, `hsn_snapshot`), `unit`, `unit_factor`,
`quantity` (in the unit sold), `base_quantity` (`quantity x unit_factor`), `unit_price` (per unit sold), `line_discount`,
`discount`, `tax_rate`, `taxable_value`, `cgst`, `sgst`, `igst`, `line_total`, `returned_quantity` (in the unit sold, default 0).

### `hardware_payments` (Scope: branch)

`PaymentColumns` from the kit, plus `branch_id`, `sale_id` FK null, `customer_id` FK null. `payment_mode` is one of `cash`, `card`,
`upi`, `cheque`, `bank_transfer` (enum `HardwarePaymentMode`). Check: exactly one of `sale_id` and `customer_id` is set. A row
with `sale_id` is a tender on a sale; a row with `customer_id` is money received against an account.

### `hardware_estimates` (Scope: branch) and `hardware_estimate_lines` (Scope: branch)

Estimate header: `branch_id`, `estimate_number` (from `next_document_number(db, branch, "estimate")`), `business_date`,
`valid_until`, `customer_id` null, `party_name` String(150) null and `party_phone` String(15) null (for a walk-in quote without
a customer record), `price_tier`, the same total columns as a sale, `is_interstate`, `status` (`open`, `converted`, `cancelled`),
`converted_sale_id` null, `notes`, `created_by_id`.

Estimate lines: the same columns as sale lines, without `returned_quantity`.

### `hardware_returns` and `hardware_return_lines` (Scope: branch)

As in the apparel module, with `refund_mode` one of `cash` or `account` and quantities as `Numeric(14,3)`.

---

## 3. Rules

### 3.1 Quantity

- A line names a product, a unit (the base unit or one of its alternate units) and a quantity with up to 3 decimals.
- `base_quantity = quantity x factor` (factor is 1 for the base unit), rounded to 3 decimals.
- If the product has `allow_fraction = false`, `base_quantity` must be a whole number (`code="whole_quantity_required"`). Half
  a box of 100 screws is fine; half a screw is not.

### 3.2 Price of a line

Resolved price for unit `u` and tier `t`:

1. The unit row's price for the tier, when the line uses an alternate unit and that price is set.
2. Otherwise the product's tier price x factor.
3. For `wholesale`, when no wholesale price is set at that level, fall back to the retail price at the same level.

The request may send its own `unit_price` (hardware shops negotiate). Rules, all per unit sold:

- Above the resolved price is allowed, up to `mrp x factor` when MRP is set.
- Below the resolved price is allowed for any cashier down to `min_price x factor`.
- Below `min_price x factor` needs a manager PIN and writes a `PRICE_OVERRIDE` audit row. When the product has no `min_price`,
  any price above zero is allowed without a PIN.

### 3.3 Tax

- One fixed rate per product. Call `retailkit.tax.compute_invoice` with `inclusive = settings.prices_include_tax`.
- **Seller state** = first two characters of the branch's GSTIN, or `settings.state_code` when the branch has none.
- **Buyer state** = first two characters of the customer's GSTIN, or the account's `state_code`. Unknown for walk-in customers.
- `interstate = True` only when both states are known and differ. Then the whole tax is IGST.

Worked examples with prices including tax at 18% (these must be test cases):

| Line | Gross | Taxable | CGST | SGST | IGST | Stock change |
|---|---|---|---|---|---|---|
| 2.5 m of cable at 42.00/m | 105.00 | 88.98 | 8.01 | 8.01 | 0 | -2.5 m |
| 1 coil of the same cable (factor 90, coil price 3,500.00) | 3,500.00 | 2,966.10 | 266.95 | 266.95 | 0 | -90 m |
| The same coil to a buyer in another state | 3,500.00 | 2,966.10 | 0 | 0 | 533.90 | -90 m |
| 0.5 box of screws (factor 100, 1.50 per piece, no box price) | 75.00 | 63.56 | 5.72 | 5.72 | 0 | -50 pcs |
| 0.005 box of the same screws | refused: `whole_quantity_required` | | | | | |

### 3.4 Creating a sale

`POST /hardware/sales` with lines (`product_id`, `unit`, `quantity`, optional `unit_price`, optional `line_discount`), optional
bill discount and reason, `customer_id`, `price_tier` (defaults to the account's tier, else `retail`), `client_sale_id`,
`approver_pin`.

One transaction: validate quantities (3.1), resolve and check prices (3.2), compute tax (3.3), apply the high-discount
approval rule, allocate the invoice number, insert the sale and lines, deduct `base_quantity` from stock with `sale`
movements, queue `hardware.sale.created` and `hardware.stock.changed`. Same idempotency and `client_sale_id` behaviour as
apparel.

### 3.5 Paying, and charging to account

- Tenders on a sale use `retailkit.tenders.Tenders`, which reads the model's `balance_due`.
- `POST /hardware/sales/{id}/charge-to-account` with an optional `amount` (default: the whole balance due) and optional
  `approver_pin`:
  1. The sale must have a customer with an active account, and be `unpaid` or `partially_paid`.
  2. If `account.balance + amount > account.credit_limit`, require a manager PIN and write a `CREDIT_LIMIT_OVERRIDE` audit
     row.
  3. Add a `sale` ledger entry, increase `account.balance` and `sale.on_account_amount`.
  4. If `balance_due` is now zero, mark the sale `paid` and set `settled_at`.
- A sale may mix both: part cash now, the rest on account.

### 3.6 Money received against an account

`POST /hardware/accounts/{customer_id}/receipts` with `amount`, `payment_mode`, optional `reference_id`. It inserts a
`hardware_payments` row with `customer_id` set (and the cashier's open shift), adds a `receipt` ledger entry with a negative
amount, and reduces the balance. It is not tied to particular invoices. Idempotent. Card-terminal receipts are out of scope;
card and UPI are recorded by hand with a reference.

### 3.7 Voiding and returns

- Void: as in apparel (unpaid, nothing paid, nothing on account, manager PIN, stock restored with `sale_void`).
- Return: as in apparel section 3.5, with these differences. Quantities are decimal and in the unit sold; restocked goods
  return `quantity x unit_factor` base units. `refund_mode = 'cash'` pays cash out. `refund_mode = 'account'` adds a `return`
  ledger entry with a negative amount and needs a customer with an account. There is no store credit in this module.
- A return of a sale that was charged to account must use `refund_mode = 'account'` up to the amount still on account.

### 3.8 Estimates

- `POST /hardware/estimates` takes the same body as a sale, plus `party_name`, `party_phone` and `valid_days`. It runs the same
  quantity, price and tax rules and stores the result. It moves no stock and takes no number from the invoice series.
- `PATCH /hardware/estimates/{id}` replaces the lines and recomputes, while `open`.
- `POST /hardware/estimates/{id}/convert` creates a sale from the estimate **at the estimate's prices**, in one transaction,
  and marks the estimate `converted`. Refused when the estimate is not `open` or `valid_until` has passed
  (`code="estimate_expired"`). Refused when the estimate has no `customer_id` and the request does not confirm a walk-in sale.
- `POST /hardware/estimates/{id}/copy` makes a new open estimate with the same lines at **today's** prices. This is how an
  expired quote is renewed.
- `POST /hardware/estimates/{id}/cancel`.

### 3.9 Cash-day source

Net cash = successful cash payments on sales + cash receipts against accounts - cash refunds on returns.

### 3.10 Who may do what

| Action | Roles |
|---|---|
| Read products, search, look up, read stock | any signed-in staff |
| Sales, payments, estimates, returns, receipts against accounts, charge to account within the limit | owner, manager, cashier |
| Products, units, categories, settings, import, purchases, adjustments, accounts (limits, tiers, opening balance, adjustments), reports | owner, manager |
| Cost prices, stock value, the full list of balances owed | owner, manager |

---

## 4. Endpoints

All under `/api/v1/hardware`.

| Method and path | Purpose |
|---|---|
| `GET /settings`, `PUT /settings` | Module settings. |
| `GET /categories`, `POST /categories`, `PATCH /categories/{id}` | Categories. |
| `GET /products`, `POST /products`, `GET /products/{id}`, `PATCH /products/{id}` | Products with their units. |
| `PUT /products/{id}/units` | Replace the alternate units of a product. |
| `GET /products/lookup?code=` | Barcode or SKU. Returns the product **and the unit the barcode belongs to**, so scanning a box barcode adds a box. |
| `GET /catalog/search?q=&category_id=&branch_id=&limit=` | Server-side search over name, brand, size spec and SKU, with stock. Max 50. |
| `POST /import` | CSV, section 5. |
| `GET /stock`, `PUT /stock/{product_id}`, `POST /purchases`, `GET /purchases`, `GET /purchases/{id}`, `POST /adjustments`, `GET /movements` | Stock. Purchase lines may be entered in any of the product's units with a cost per that unit; the service converts to base. |
| `POST /sales`, `GET /sales`, `GET /sales/{id}` | Sales. |
| `POST /sales/{id}/payments`, `.../payments/split`, `.../payments/terminal`, `POST /payments/{id}/terminal/complete`, `POST /payments/{id}/reverse` | Tenders. |
| `POST /sales/{id}/charge-to-account` | Put the balance on the customer's account. |
| `POST /sales/{id}/void` | Void. |
| `GET /sales/{id}/receipt?format=rows\|text\|html\|a4&paper_mm=` | Thermal bill, or an A4 tax invoice (section 6). |
| `POST /estimates`, `GET /estimates`, `GET /estimates/{id}`, `PATCH /estimates/{id}`, `POST /estimates/{id}/convert`, `.../copy`, `.../cancel`, `GET /estimates/{id}/receipt` | Estimates. |
| `GET /accounts` | Customers with accounts: `search`, `owing_only`. Managers only. |
| `GET /accounts/{customer_id}`, `PUT /accounts/{customer_id}` | Read (creates nothing) and create or update tier, limit, state code, active. |
| `POST /accounts/{customer_id}/opening-balance` | Once, when the account has no ledger entries. |
| `POST /accounts/{customer_id}/adjustments` | Manager correction with a note. |
| `POST /accounts/{customer_id}/receipts` | Money received. |
| `GET /accounts/{customer_id}/ledger` | Paged ledger. |
| `GET /accounts/{customer_id}/statement?from=&to=&format=json\|html` | Opening balance, entries, closing balance; printable. |
| `POST /returns`, `GET /returns`, `GET /returns/{id}`, `GET /returns/{id}/receipt` | Returns and credit notes. |
| `GET /reports/day?branch_id=&date=` | Day report (section 7). |
| `GET /reports/outstanding` | Balances owed, largest first, with days since the last receipt. |
| `GET /reports/sales-by-product?from=&to=` | Quantity (base unit) and value by product. |

---

## 5. CSV import

One row per product. Columns: `name`, `category`, `brand`, `size`, `price`, `wholesale_price`, `min_price`, `mrp`, `gst_rate`,
`hsn`, `unit`, `allow_fraction`, `barcode`, `sku`, `alt_unit`, `alt_factor`, `alt_price`, `alt_barcode`, `opening_stock`.

- Required: `name`, `category`, `price`. `unit` defaults to `pcs`; `gst_rate` defaults to the settings value.
- `allow_fraction` blank: true when the unit is `kg`, `g`, `m`, `ft`, `sqft`, `l` or `ml`, else false.
- `alt_unit` with `alt_factor` creates or updates one alternate unit. More units are added in the admin app.
- Unit aliases as in the existing import (`nos`, `piece`, `mtr`, `meter`, `metre`, `feet`, `kgs`, `ltr`, and so on).
- Matching an existing product: by SKU, then barcode, then (category, name, size).
- Everything else as the apparel import: per-row errors, `dry_run`, one transaction, 10,000 rows, `opening_stock` only for
  products created by this import.

---

## 6. Printed documents

- **Thermal bill** through `retailkit.receipt`: each line prints the name and size spec, then "quantity unit x rate" and the
  amount. Totals show CGST and SGST or IGST. When a part is on account, print "On account" and the customer's new balance.
- **A4 tax invoice** (`format=a4`), in `app/modules/hardware/invoice_a4.py`: an HTML page with seller and buyer blocks (names,
  addresses, GSTINs, place of supply), a line table (description, HSN, quantity, unit, rate, discount, taxable value, tax
  rate, tax, total), an HSN-wise tax summary, round-off, grand total, and the amount in words. Used for business buyers.
- **Estimate**: both formats, titled `ESTIMATE`, with "Valid until" and without payment lines.
- **Account statement**: A4 HTML.

---

## 7. Day report

For one branch and business date: sales count and totals (taxable value, CGST, SGST, IGST, round-off, grand total); tax summary
by rate; payments by mode on sales; **charged to account today**; **received against accounts today** by mode; returns by
refund mode; net cash (3.9); open estimates created today; top 10 products by value.

---

## 8. Screens

### POS app (`apps/pos-app/src/hardware/`)

| File | What it does |
|---|---|
| `HardwareApp.tsx` | Route state: sale, pay, estimates, accounts, returns, day end, printer. |
| `SaleScreen.tsx` | Search-first, because most hardware has no barcode: a search box (server-side search, results as a list with name, size spec, price per base unit and stock), category chips, and keyboard-scanner support. The customer is chosen at the top; choosing a customer with a wholesale account reprices the cart. Buttons: "Bill" and "Save as estimate". |
| `QuantitySheet.tsx` | Opens when a product is tapped or scanned: unit chips (base unit and alternate units, with the price of each), a number pad for the quantity (decimals only when allowed), and an editable rate. Shows the line total and, for managers, the minimum price. Modelled on `retail/WeightSheet.tsx`. |
| `hardwareCartStore.ts` | Cart keyed by product and unit. |
| `PayScreen.tsx` | Sale summary plus `retailkit/PayPanel` with the hardware `TenderApi`. Adds "On account", shown when the customer has an account, with the current balance and limit. |
| `EstimatesScreen.tsx` | List of open estimates with search; open one to edit (loads it into the cart), print, convert to a sale, copy or cancel. |
| `AccountsScreen.tsx` | Find a customer, see the balance, take a receipt, print a statement. Cashiers see one customer at a time; the full list is for managers. |
| `ReturnsScreen.tsx` | As in apparel, with decimal quantities and "Refund in cash" or "Credit the account". |
| `DayScreen.tsx` | The day report for managers. |

### Admin app (`apps/admin-app/src/screens/hardware/`)

| File | What it does |
|---|---|
| `ProductsScreen.tsx` | List, search, category chips; editor with name, brand, size spec, category, HSN, GST rate, base unit, fraction switch, retail, wholesale, minimum and MRP prices, and a list of alternate units (unit, factor, price, barcode). |
| `ImportSheet.tsx` | CSV import. |
| `StockScreen.tsx` | Stock on hand in base units; receive a delivery in any unit; adjust and count; low stock. |
| `AccountsScreen.tsx` | All accounts with balances; set tier and limit; opening balance; adjustments; ledger and statement. |
| `ReportsScreen.tsx` | Day report, outstanding balances, sales by product. |
| `SettingsCard.tsx` | Module settings. |

---

## 9. Stages

| Stage | Scope | Done when |
|---|---|---|
| **H1 Catalogue** | Settings, categories, products, alternate units, lookup, search, CSV import. Admin `ProductsScreen`, `ImportSheet`. Seed script with about 60 products across cable, pipe fittings, fasteners, paint, switches. | A product with a base unit and two alternate units saves and reads back; a box barcode lookup returns the box unit; a 1,000-row CSV imports with per-row errors. |
| **H2 Stock** | Stock, purchases in any unit, adjustments, counts, ledger. Admin `StockScreen`. | Receiving 2 coils adds 180 m at the right cost per metre; ledger balances are right; cost hidden from cashiers. |
| **H3 Sales** | Quantity, price and tax rules; sales; tenders; void; thermal receipt; cash-day source. No screens. | Every row of the worked examples passes; a price below minimum needs a PIN and is audited; an interstate sale stores IGST only. |
| **H4 Counter** | POS `HardwareApp`, `SaleScreen`, `QuantitySheet`, `PayScreen`, day end. | In the browser: sell 2.5 m and 1 coil of the same cable plus half a box of screws, change one rate, pay by UPI, print, and see stock fall by 92.5 m and 50 pcs. |
| **H5 Estimates** | Estimates end to end, POS `EstimatesScreen`, printed estimate. | Save an estimate, edit it, print it, convert it to a sale at the quoted prices after the product's price has changed; an expired estimate is refused and can be copied. |
| **H6 Accounts** | Accounts, ledger, charge to account, receipts, statement, outstanding report. POS `AccountsScreen`, admin `AccountsScreen`. | A sale part-paid in cash and the rest on account is `paid` with the right ledger entry; a charge over the limit needs a PIN; a cash receipt lowers the balance and raises expected cash; the statement's closing balance equals the account balance. |
| **H7 Returns, A4 invoice, reports** | Returns, A4 tax invoice, day report, sales by product. POS `ReturnsScreen`, `DayScreen`; admin `ReportsScreen`. | A return of 10 m from a 1-coil line restocks 10 m and credits the right proportion; the A4 invoice for an interstate business buyer shows IGST and an HSN summary that adds up; the day report matches the day's rows in a mixed test. |

## 10. Tests to write (minimum)

`tests/modules/test_hardware_catalog.py`, `test_hardware_stock.py`, `test_hardware_sales.py`, `test_hardware_estimates.py`,
`test_hardware_accounts.py`, `test_hardware_returns.py`, `test_hardware_reports.py`, and `tests/core/test_hardware_pricing.py`
for the pure quantity and price functions.

- The category guard, tenant isolation and RLS coverage for every `hardware_` table.
- The worked examples in 3.3.
- Price resolution: unit price set, unit price missing, wholesale missing at unit level, wholesale missing everywhere.
- Ledger integrity: after any sequence of charges, receipts, returns and adjustments, the last `balance_after` equals
  `account.balance`, and the sum of entry amounts equals the balance.
- Two concurrent charges against one account cannot both pass a limit that only allows one.
- Estimate conversion is idempotent: converting twice creates one sale.
- Idempotency on every money and stock POST.
- An apparel organization gets 404 on every hardware route, and the reverse.
- The existing restaurant, grocery and apparel suites still pass untouched.

## 11. Out of scope for this module

Product variants, serial numbers and warranty, batch tracking, delivery challans and e-way bills, e-invoicing, purchase orders
and supplier ledgers, interest on overdue balances, payment reminders by SMS or WhatsApp, invoice-wise settlement of receipts,
multiple price lists beyond retail and wholesale, card-terminal receipts against accounts, stock transfers between branches.
