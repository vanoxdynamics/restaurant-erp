# Plan: one billing app for every kind of shop

Draft for review, 2026-11-05. Builds on [01-counter-app-plan.md](01-counter-app-plan.md) and
[02-pinelabs-integration.md](02-pinelabs-integration.md).

**Status (2026-11-05).** Phases 1–3 and the console part of phase 4 are built: categories and settings, decimal quantities,
item fields, counter sales with tokens, billing without KOT (and auto-KOT for cafés), neutral receipts, the quick-sale screen
with keyboard-scanner support and weight entry, barcode lookup, CSV import in the admin app, and the category picker and
per-setting overrides in the console. Not done: the Pine Labs camera scan (call 1009, needs a device to test), the app
rename and new package/UAT key (D1, needs your decision and a new signing key), returns (phase 6) and inventory (phase 7).
API details: [api.md](../api.md#business-categories-and-shops).

**Goal.** The app on the Pine Labs device becomes a general billing app. Each business picks a **category** (restaurant, café,
grocery, general retail). The basic flow is the same for everyone: add items, bill, pay, print. The category only switches on
extra steps and screens. One APK, one Pine Labs approval, one server.

---

## 1. Decisions for you to review

Each has a recommendation. These are the main parts; everything after section 1 follows from them.

| # | Decision | Recommendation | Why |
|---|---|---|---|
| D1 | **App name and package on Pine Labs** (cannot change after approval) | **Vanox Billing**, `com.vanoxdynamics.billing` (UAT: `com.vanoxdynamics.billing.uat`). Waiter phone app stays `com.vanoxdynamics.pos`. | Not tied to restaurants. Nothing has gone to Pine Labs yet, so changing now only means a new UAT signing key. |
| D2 | **Categories at launch** | **Restaurant, Café/QSR/Bakery, Grocery/Kirana, General retail.** Pharmacy, apparel and electronics later. | Only offer what we fully support; a pharmacy needs batches and expiry dates we don't have. |
| D3 | **Category per business or per branch** | **Per business** at launch; per branch later. | Simpler. Per branch matters only for owners running different kinds of shops. |
| D4 | **Who sets the category** | **You, in the platform console**, when creating the subscriber; shown read-only to the owner. | Category changes screens and possibly pricing; it should not change by accident. |
| D5 | **Category vs. paid modules** | Category is a **workflow preset** (screens and steps). Paid features stay **modules** (kitchen display, analytics, later inventory). You can override any preset setting per business. | Lets you sell the same modules to every category, and fix one-off cases (a café that wants tables). |
| D6 | **Weighed items** (0.75 kg of onions) | Quantities become **decimals (3 places)** everywhere, and items get a **unit** (piece, kg, g, litre, ml). Price is per unit. | The only clean way; storing grams as whole numbers breaks for litres and for display. It touches about 27 files, so it goes first. |
| D7 | **How items are found at the counter** | **Search and tap** for everyone; **barcode** via the Pine Labs camera scanner, and USB/Bluetooth scanners (they type like a keyboard) on tablets and browsers. | Works on the handheld without extra hardware. The Pine Labs base scanner can come later. |
| D8 | **Loading a shop's items** | **CSV import** in the admin app (name, category, price, GST rate, HSN, unit, barcode). | A grocery store has thousands of items; nobody types those in one by one. |
| D9 | **Stock tracking** | **Not in the first version.** Sell without tracking stock; an **Inventory** module (paid) comes next. | Stock (purchases, suppliers, adjustments) is a product of its own; billing must ship first. |
| D10 | **Returns** | **Phase 2**, right after launch: return items from a past bill and issue a credit note (cash back or card refund). | Common in retail, but the first version can live with same-day card reversal (already built) and voiding unpaid bills. |
| D11 | **Café token numbers** | **Yes**: a daily token number printed large on the bill, so customers are called when their order is ready. | Standard in QSRs; cheap to build. |
| D12 | **Pricing** | Your existing plans can stay as they are. If you want category-specific plans (e.g. a cheaper "Kirana" plan), create them in the console; no code needed. | Plans and modules already work per subscriber. |

---

## 2. How categories work

A category sets **defaults for six workflow settings**. They are stored per business, so you can override any of them in the
console.

| Setting | Restaurant | Café / QSR | Grocery | General retail |
|---|---|---|---|---|
| **Table service** (floor, tables, guests, waiters) | on | off | off | off |
| **Kitchen tickets** (KOT on send; screen or printed per the `kds` module) | on | on | off | off |
| **Quick sale** as the counter's home screen | off (bills list) | on | on | on |
| **Barcode scanning** | off | off | on | on |
| **Weighed / loose items** | off | on (bakery) | on | off |
| **Token numbers** | off | on | off | off |

Paid modules work on top of any category: `kds` (kitchen display) only matters where kitchen tickets are on; `analytics`
everywhere; `inventory` later.

**The basic flow, identical for every category:**

```text
add items  ->  bill (GST, discount)  ->  pay (cash / card / UPI / split)  ->  print bill
```

What categories add:

```text
Restaurant:  open table -> add items -> send KOT -> serve -> [basic flow]
Café / QSR:  quick sale -> [basic flow] -> KOT to kitchen + token on the bill
Grocery:     quick sale (scan, weigh) -> [basic flow]
Retail:      quick sale (scan) -> [basic flow]
```

Billing, payments (Pine Labs), reversal, printing, day end, settlement, staff, shifts, reports, plans and modules are shared and
do not change per category.

---

## 3. What changes

### 3.1 Server

| Change | Detail |
|---|---|
| **Business category and settings** | `organizations.category` (restaurant, cafe, grocery, retail) and `organizations.settings` (the six settings above). Set with the subscriber in the console; returned to apps with `/me/entitlements`. |
| **Item fields** | `barcode` (unique per business), `sku`, `hsn_code`, `unit` (piece, kg, g, l, ml), `sold_by_weight`. Barcode lookup endpoint for the scanner. |
| **Decimal quantities** | Order lines, KOT lines and bill calculations use quantities with 3 decimals. Whole-number items keep working unchanged. |
| **Counter sale** | New order type `counter_sale`: no table. A quick-sale order can be **billed straight away**: when kitchen tickets are off, billing no longer refuses items that weren't sent to the kitchen (today it does). |
| **KOT for cafés** | When kitchen tickets are on but table service is off, billing a counter sale also fires its KOT (one action at the counter). |
| **Token numbers** | Daily per-branch counter assigned to counter sales when the setting is on; printed on the bill and KOT. |
| **Receipt wording** | Neutral bill: "Counter sale", no table/guest lines, HSN and unit per line when set, token number when on. Restaurant bills unchanged. |
| **CSV item import** | `POST /retail/items/import`: validate every row, report errors per row, create or update by SKU/barcode. |

### 3.2 Counter app (Pine Labs device)

- Reads the category and settings with the plan; picks the home screen.
- **Quick sale screen** (new, shared by café, grocery, retail): search box, category chips, item grid, **Scan** button (Pine Labs
  camera scanner), keyboard-scanner support, cart with quantity steppers, **weight entry** for loose items (number pad in kg/g),
  then **Bill and pay** on the existing bill screen.
- Wording by category: "Items" instead of "Menu", "Counter sale" instead of "Takeaway", no table words outside restaurants.
- The restaurant home (bills to collect, tables, KOTs) stays as it is.

### 3.3 Admin app (owner's phone)

- Shows the business category (read-only).
- Item editor gains barcode, SKU, HSN code, unit and "sold by weight".
- **Import items from CSV** with a downloadable template and a per-row error report.

### 3.4 Platform console (you)

- Category picker when creating a subscriber, and on its detail page (with the six settings, overridable).
- Optional: category shown in the subscriber list and as a filter.

### 3.5 Waiter phone app

Unchanged; it is only used by restaurants (table service on). It refuses to start for businesses without table service, with a
clear message.

### 3.6 Pine Labs

- New app name and package (D1), a new UAT signing key, updated description ("billing and payments for retail and restaurants").
- UAT adds two cases: quick sale paid by card, and the camera barcode scan (Pine Labs call 1009).
- The integration itself (payments, reversal, printing, settlement) does not change.

---

## 4. Phases

| # | What | Done when |
|---|---|---|
| 1 | **Foundation:** category and settings, decimal quantities, item fields, counter sale order type, billing without KOT where off | existing restaurant tests still pass; new tests for each category's rules, in normal and RLS mode |
| 2 | **Quick sale:** quick-sale screen, search, keyboard scanners, weight entry, neutral receipts, token numbers | a grocery sale with a weighed item and a café sale with a token, end to end in the browser with the simulator |
| 3 | **Catalog tools:** item fields in the admin app, CSV import, barcode lookup, Pine Labs camera scan | 1,000-row CSV imports with per-row errors; a scanned barcode adds the item |
| 4 | **Console and app identity:** category in the console, rename to Vanox Billing and the new package, new UAT key | a new subscriber of each category gets the right screens |
| 5 | **Pine Labs UAT** of the general app | their checklist passes ([02-pinelabs-integration.md](02-pinelabs-integration.md) plus the two new cases) |
| 6 | **Returns and credit notes** (D10) | partial return of a paid bill, refund by cash or card, credit note printed and in reports |
| 7 | **Inventory module** (D9) | stock on hand, purchases, adjustments, low-stock alerts |

Phases 1 to 4 come before UAT, so Pine Labs reviews the general app once.

---

## 5. Not in the first version

Stock and purchases (phase 7), returns (phase 6), customer accounts and credit ("khata"), loyalty, batches and expiry (pharmacy),
size/colour matrices (apparel), serial numbers (electronics), interstate GST (IGST) and B2B invoices with the buyer's GSTIN,
e-invoicing and e-way bills, per-branch categories.

---

## 6. Risks

| Risk | How it is handled |
|---|---|
| **Decimal quantities touch money calculations** | Done first, behind the existing billing tests, with new tests for weighed items and rounding (bills round to paise, as today). |
| **Restaurant flow regresses** | Restaurants keep every current setting; the full current test suite must pass unchanged at every phase. |
| **Large catalogs slow the quick-sale screen** | Search on the server with an index on name, SKU and barcode; the app loads a page at a time. |
| **Scanner variety** | Pine Labs camera scan on the device, keyboard-style scanners elsewhere; the base scanner later. |
| **Selling categories we don't fully support** | The console only offers the four launch categories. |

---

## 7. How we will know it works

- Each category's settings produce the right screens and server rules (tests per category, normal and RLS).
- Grocery: scan two items, weigh 0.750 kg of a loose item, bill, pay by UPI, printed bill shows units, HSN and correct GST.
- Café: counter sale with token, KOT prints at the counter, card payment, token on the bill.
- Retail: scan, discount with manager PIN, split cash and card, reverse the card part.
- Restaurant: today's flows unchanged (existing end-to-end tests).
- CSV import of 1,000 items with a few bad rows: good rows imported, each bad row reported with the reason.
