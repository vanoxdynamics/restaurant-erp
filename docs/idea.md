# The idea, on one page

**Billing for any shop, on the Pine Labs machine it already has.**

## The problem

Shops in India bill on paper, on a laptop, or in a phone app, and take card and UPI on a separate Pine Labs machine. The amount
is typed twice, the two never match exactly, and the owner sees the day's numbers only after closing.

## The idea

One billing app that runs **on the Pine Labs device itself**. The shop adds items, the bill is made with GST, the guest pays by
card, UPI or cash on the same device, and the bill prints from its printer. Nothing is typed twice.

Each business picks a **category** when it signs up. The basic flow is the same for everyone; the category only adds the steps
that kind of shop needs:

| Category | Adds |
|---|---|
| **Restaurant** | tables, KOTs to the kitchen (screen or printed), waiters ordering from their phones |
| **Café / QSR / bakery** | quick counter sale, KOT, token number on the bill |
| **Grocery / kirana** | barcode scan, items sold by weight |
| **General retail** | barcode scan, sizes and colours |

## What every shop gets

- **Billing:** GST bills, discounts with manager approval, card/UPI/cash, split payment, printed bill.
- **Owner's phone app:** today's sales, cash vs card vs UPI, staff, menu or items, reports.
- **Control:** staff PINs, shifts and cash counts, an audit trail of voids and reprints.
- **Day end:** card settlement and cash count in two taps.

## Why shops stay

Billing alone is easy to replace; this is what keeps them, by category:

| Category | What keeps them |
|---|---|
| Restaurant | kitchen flow (KOTs) and, next, Swiggy/Zomato orders in the same system |
| Café / QSR | speed at the counter, tokens |
| Grocery / kirana | customer credit (khata) and simple stock |
| General retail | stock, returns and exchanges |
| Everyone with GST | an export for the accountant (GSTR, Tally) |

## Why us

- **Built into the card machine** the shop already rents from Pine Labs: no extra hardware, no double entry.
- **One app for every kind of shop:** approved by Pine Labs once, sold through Pine Labs to all its merchants.
- **Restaurants already work end to end:** tables, KOTs, waiter phones, kitchen screen or printed KOTs, payments, reversal, day end.
- **We control each shop's features** from our console: plans, modules, category, switched on or off without a new app.

## How we make money

A monthly or yearly subscription per business, with paid add-on modules (kitchen display, analytics, later inventory, online
orders). Plans and modules are already managed in the platform console.

## Order of work

1. **Now:** finish restaurants and the Pine Labs approval of the general app (name, categories, quick sale).
2. **Next:** cafés, grocery and retail on the quick-sale screen; returns; accountant export.
3. **Then:** what keeps each category: khata and stock, Swiggy/Zomato, inventory as a paid module.

## Not doing (for now)

Pharmacy (batches and expiry), electronics (serial numbers), B2B and e-invoicing, our own payment hardware.

Details: [neutral billing plan](pos-devices/04-neutral-billing-plan.md), [Pine Labs integration](pos-devices/02-pinelabs-integration.md).
