# Plan: the lean counter app (Pine Labs device) and the waiter app

Decided with the product owner on 2026-11-04.

- **One Pine Labs device per restaurant**, at the counter. It is the restaurant's **billing desk and KOT printer**.
- **Waiters use their own Android phones** with the waiter app from the Play Store. Phones have no printer and take no payment.
- **All payment happens at the counter.**
- **KOTs sent from waiters' phones print automatically on the counter device** (restaurants without a kitchen screen).
- **The cashier can also take orders** on the counter device (takeaway, walk-ins, small places with no waiters).
- Ship a **small but complete** first version for Pine Labs UAT; extras come in later releases.

## 1. Two builds from one code base

| | Waiter app | Counter app |
|---|---|---|
| Runs on | staff phones (Play Store) | the Pine Labs device (Pine Labs app store / MDM) |
| Android package | `com.vanoxdynamics.pos` | `com.vanoxdynamics.pos.counter` (UAT: `com.vanoxdynamics.pos.counter.uat`) |
| App name | Restaurant POS | Restaurant Counter (UAT: Counter UAT) |
| Build switch | `APP_VARIANT=waiter` | `APP_VARIANT=counter` / `counter-uat` |
| Prints | nothing (KOTs print at the counter) | KOTs, VOID KOTs, customer bills |
| Takes payment | no | card and UPI through Pine Labs, cash, split |

Both builds use the same screens and the same server. The variant only decides **what this device can do** (`src/device.ts`):
printer (`pinelabs`, `system` for a browser or a tablet with a print service, or none), payments (`pinelabs`, a simulator for
development, or manual entry), and whether it is the branch's **print station**.

What a restaurant may use is still decided by its plan and modules (platform console) and by the signed-in person's role. The
variant never grants anything the server would refuse.

## 2. Counter app screens

Phone-sized, one column, big buttons.

1. **Sign in.** Pick your name, enter your PIN. Cashier, manager or owner in normal use; small places may let anyone with a
   billing role sign in here.
2. **Bills (home).** Every open table and takeaway with its amount and state (ordering, billed, partly paid), newest activity first.
   A banner shows KOTs that failed to print. Buttons: **New order** (table or takeaway), **KOT printer**, **Day end**.
3. **Order.** The existing order screen: menu, quantities, options, notes, **Print KOT** (one KOT per send for the table),
   mark served, cancel with manager PIN (prints a VOID KOT), transfer table.
4. **Bill and pay.**
   - Generate the bill, optionally with a discount and reason (manager PIN above the threshold, as the server already enforces).
   - Tenders: **Card** and **UPI** through Pine Labs, **Cash** (amount received, change shown), several tenders for one bill.
   - Pine Labs prints the bank charge slip; we print the **customer bill** when the cashier taps **Print bill**.
   - A card payment that was charged by mistake can be **reversed** the same day with a manager PIN (Pine Labs void).
   - A payment whose result never arrived is shown as **unconfirmed** with **Approved** (enter approval code) or **Failed**, so
     money is never lost track of.
5. **KOT printer.** The print queue: printing, printed, failed (with the printer's reason, e.g. out of paper), **Retry**; today's
   KOTs with **Reprint**; paper width (58 or 80 mm); **Test print**.
6. **Day end.** Pine Labs **card settlement** with its per-bank summary, then **close shift** with the cash count.

Not on the counter app: menu and price editing, staff and PINs, reports and Z-report, kitchen screen, platform settings. Those stay
in the admin app, the kitchen tablet and the platform console.

## 3. Waiter app changes

- **Send KOT** instead of Print KOT on plans without a kitchen screen. Each KOT shows **Printed at counter ✓** or **Waiting for
  counter printer**. Cancelling still takes a manager PIN; the VOID KOT prints at the counter.
- **No Bill or payment buttons** for waiters. Managers and owners signed in on a phone keep billing, but without Pine Labs there is
  no card machine on a phone, so they record cash or a manual card/UPI reference.
- No printing code runs on a phone.

## 4. The print station

The counter device keeps a **print queue** (`src/printing/printQueue.ts`):

- Every few seconds it asks the server for `GET /kots/station/print-queue`: slip KOTs that have never printed, and VOID KOTs
  whose original went out but whose VOID notice has not printed.
- It prints with the **device's own token** (issued when the counter was linked), not a person's session, so it keeps printing
  while the counter is locked or no one is signed in. Prints it confirms are attributed to no person; a reprint someone taps
  goes through their session and is audited to them.
- KOTs sent from the counter itself are added immediately, without waiting for the next check.
- Each KOT is printed once at a time; on the printer's success the counter confirms with `POST /kots/{id}/slip/printed`
  (idempotent, so a retried confirmation is never a reprint). A printer error keeps it in the queue as **failed** with the reason.
- Only a device with an automatic printer result (Pine Labs, or the development simulator) prints unattended. A browser counter
  lists waiting KOTs with a **Print** button and asks "Did the KOT print?", as today.

## 5. Server changes

| Change | Why |
|---|---|
| **Terminal payments**: `POST /billing/bills/{id}/terminal-payments` starts a payment (status `pending`, not counted yet, with a 10-character reference for the terminal); `POST /billing/payments/{id}/complete` records success (with the terminal's details) or failure; `GET /billing/bills/{id}` lists pending ones | a card or UPI payment is recorded before the terminal is called, so a crash can never lose a charged payment |
| **Payment reversal**: `POST /billing/payments/{id}/reverse` (manager PIN) marks a Pine Labs payment `reversed` and reopens the bill's balance | card void on the same day |
| **Customer bill rendering**: `GET /billing/bills/{id}/receipt` as print rows, text or HTML for 58/80 mm | the counter prints the customer bill |
| **Print rows on KOTs**: the slip JSON gains `rows` (text, alignment, emphasis) for the requested paper width | the Pine Labs printer prints line by line |
| **VOID notices are tracked**: `kots.void_printed_at`; confirming a voided KOT's print sets it once instead of counting a reprint | the print station knows which VOID notices are still to print |
| **`GET /kots/print-queue`**, and device-token `/kots/station/print-queue`, `/kots/station/{id}/slip`, `/kots/station/{id}/printed` | what the print station must print next, printable while the counter is locked |
| **Check-only reversal** (`check_only: true`) | the manager PIN is checked before the card is refunded on the terminal |

All of it is tested like the rest of the backend, including under row-level security.

## 6. Phases

| # | What | Done when |
|---|---|---|
| 1 | Server changes above | tests pass in normal and RLS mode |
| 2 | Device profile, Pine Labs bridge with a development simulator, print queue | the counter works end to end in a browser with the simulator |
| 3 | Counter screens (bills, bill and pay with terminal payments, KOT printer, day end) and waiter-app changes | walk-through in the browser on both variants |
| 4 | Android build: variants, package names, Pine Labs native module, UAT signing | a UAT APK builds locally |
| 5 | Pine Labs UAT on their device (see `02-pinelabs-integration.md`) | their checklist passes |
| 6 | Play Store release of the waiter app | published |

## 7. Status (2026-11-04)

- **Phases 1-3 done.** Server changes with tests (terminal payments, reversal, receipts, print rows, print queue, device-token
  print station); counter and waiter builds; Pine Labs protocol in TypeScript with unit tests and a development simulator.
  Walked through in the browser with the simulator: a locked counter printing a waiter's KOT, card payment whose answer was lost
  and recovered by Get Status, bill printing, and a manager reversal.
- **Phase 4 in part.** Variants, package names and the Pine Labs native module are in place; the module compiles. The full APK
  does not build on the development machine until Windows long paths are enabled, or use EAS Build
  (see [03-android-builds.md](03-android-builds.md)). UAT signing key created outside the repository.
- **Phase 5** waits for Pine Labs' ApplicationId, a UAT device and the UAT server address.

## 8. Later (not in the first release)

Split a bill by items, tips, SMS/e-bills, uploading invoices to Pine Labs, linking the device to a branch automatically from its
terminal ID, offline order taking, reprints requested from a waiter's phone.
