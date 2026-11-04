# Plan: "Kitchen display" module (screen plan vs. slip plan)

Goal: two kinds of subscribers on the same product.

| | **Screen plan** (has the `kds` module) | **Slip plan** (no `kds` module) |
|---|---|---|
| Sending an order to the kitchen | fires KOTs to the kitchen screen | fires KOTs and produces a **printed kitchen slip** |
| Kitchen staff | use the kitchen screen to start / finish tickets | no kitchen screen; the waiter or cashier hands the slip over |
| Item status | kitchen: preparing, ready; floor: served | **waiter marks the slip served** |
| Who closes the bill and takes payment | owner, manager, cashier (as today) | owner, manager, cashier **and waiter** |

Decisions already made with you: printed slip (thermal), waiter closes the bill in the slip plan only, waiter marks items served,
and one add-on module (not two modes).

## 1. What exists today (so the plan builds on it)

- Firing an order (`POST /kots/fire`) already creates **one KOT per station**, numbered (`KOT-BLR1-20261104-0001`), with the lines,
  modifiers and notes. That record is exactly what a kitchen slip needs, so the slip is a *rendering of a KOT*, not a new concept.
- KOT status runs `queued -> preparing -> ready -> served`; only kitchen roles may start or finish a ticket, floor staff may mark served.
- Voids, approvals, audit and analytics all hang off KOTs. Keeping KOTs in both plans means none of that has to be rebuilt.
- Payment and billing are limited to `owner, manager, cashier`. Waiters can fire orders and open shifts but not take payment.
- There is **no printing support** anywhere yet.
- Modules are already a platform concept (`pos`, `analytics`): plans include them, operators can override per subscriber, and the API
  and apps read `GET /me/entitlements`. So the screen/slip split costs one new module key.

## 2. Design

### 2.1 The module
- New module key **`kds`**, name "Kitchen display", description "Kitchen screen, ticket statuses and kitchen staff accounts".
  Depends on `pos`; not core. It appears automatically in the console's module switches, plan editor and per-subscriber overrides.
- A migration widens the module-key CHECK constraints (plans, overrides, platform switch) and **adds `kds` to the legacy plan**, so every
  existing subscriber keeps today's behaviour. Nothing changes for them unless an operator moves them to a slip plan.
- Suggested plans to create in the console (names are yours): *Standard* = `pos`, *Standard + Kitchen display* = `pos, kds`, *Pro* = `pos, kds, analytics`.

### 2.2 How firing behaves
- Firing works in both plans and creates the same KOTs.
- Each KOT records how it was fulfilled: new column `kots.fulfilment = 'screen' | 'slip'`, set at fire time from the subscriber's
  entitlements. Why it matters: if a subscriber later adds the module, slips already on paper never appear on the kitchen screen and get
  cooked twice; if they drop it, in-flight screen tickets stay visible to the floor to be marked served.
- Slip-plan KOTs start `queued`. The only legal move is `queued -> served` (waiter), skipping `preparing`/`ready`, which belong to the screen.

### 2.3 Server rules (all enforced by the API, the apps only hide buttons)
| Rule | Slip plan | Screen plan |
|---|---|---|
| `PATCH /kots/{id}/status` to `preparing` / `ready` | refused: `403 module_disabled` | allowed for kitchen roles |
| `PATCH /kots/{id}/status` to `served` | allowed from `queued` for floor staff | as today |
| Create or sign in a **kitchen-role** staff account | refused with a clear message (no screen to use) | allowed |
| Kitchen screen board (`GET /kots` as kitchen role) | refused | allowed |
| Bills, payments, closing the bill | adds **waiter** to the allowed roles | owner, manager, cashier |
| Bill void, payment void, discounts above the threshold | unchanged: manager only / manager PIN | unchanged |

Waiter payment is implemented as an entitlement-aware role check (`owner, manager, cashier` plus `waiter` when `kds` is absent), applied to
the same endpoints, so there is a single rule in one place. Payments record who took them; waiter-closed bills show in the Z-report and
audit log like any other, and cash goes through the waiter's own shift as it does for cashiers.

### 2.4 The slip
- `GET /kots/{id}/slip` returns the structured slip (branch, table, order and KOT number, station, time, waiter, guest count, lines with
  quantity, variant, modifiers and notes, and a marker: `NEW`, `ADDITIONAL` for later rounds, `REPRINT`, or `VOID`).
- The same endpoint renders three formats from one template: **ESC/POS bytes** (thermal printers), **80 mm / 58 mm HTML** (system print
  dialog), and **plain text** (fallback, tests).
- **One slip per send for the whole table** (decided 2026-11-04): on the slip plan each "Send to kitchen" makes a single KOT
  carrying every new item, with the lines grouped under a `[ STATION ]` heading when they span stations. Later sends are their
  own ADDITIONAL slip. The screen plan keeps one KOT per station so each station's screen shows only its own work.
- **Reprints** are recorded (`print_count`, `last_printed_by`, `last_printed_at`) and printed with a REPRINT banner, so a slip cannot be
  silently duplicated to run up cooking or hide a cancellation.
- **Voids after a slip exists:** cancelling a fired KOT prints a **VOID slip** and the app tells the waiter to take the original back, since
  the paper is already in the kitchen. Approvals keep working with the manager PIN; the kitchen-role approver simply does not exist here.
- Firing never waits on a printer: if printing fails the KOT still exists, the item is marked "slip not printed" and can be reprinted.

### 2.5 Apps
- **POS (waiter/cashier):** reads `modules` from entitlements (refreshed instantly by the existing `entitlements.changed` event).
  - Slip plan: the button reads **Print KOT**; it fires then prints; a "Reprint slip" action; each table shows slips awaiting "served" with a one-tap
    **Mark served**; the bill screen is available to waiters.
  - Screen plan: exactly as today.
- **Admin app:** hides the kitchen role and screen-only settings when the module is off.
- **Platform console:** no new screens. `kds` is just another module, plus a one-line note on the plan editor describing the difference.

### 2.6 Printing (the hardware question)
The cloud API cannot reach a printer on the restaurant's network, so printing must start on the device in the restaurant. Rollout in two steps:
1. **System print (works on day one, any printer with an OS driver):** the slip renders as an 80 mm page and the app opens the print dialog.
2. **Direct thermal printing:** the POS device sends ESC/POS straight to the printer over the LAN (port 9100) or Bluetooth, with a small
   per-branch printer table (name, station, connection, address, paper width, "print automatically on send"). Needs a native print library
   in the POS app (so a development build, not Expo Go) and real printers to test with.

## 3. Phases

| # | What | Done when |
|---|---|---|
| 1 | **Module and rules (backend):** `kds` module, migration (incl. legacy plan), `kots.fulfilment`, status and role rules, entitlement-aware waiter billing, kitchen-role restrictions | tests below pass; legacy subscribers unchanged |
| 2 | **Slip rendering (backend):** `GET /kots/{id}/slip` in text / HTML / ESC/POS, markers, reprint and VOID tracking, audit | golden-file tests of the three formats |
| 3 | **POS slip workflow:** send-and-print via the system dialog, Mark served, waiter bill screen, reprint, "not printed" state | walk-through on a tablet and web with both plan types |
| 4 | **Direct printers:** printer table, ESC/POS over LAN/Bluetooth, per-station routing, auto-print, retry queue | printed on real hardware |
| 5 | **Rollout:** admin-app tweaks, operator guide, plan presets, moving a pilot subscriber between plans | pilot restaurant live |

## 4. Tests that prove it
- Each rule in section 2.3 for both plan types, including the 403 codes and that `served` is allowed from `queued` only in slip mode.
- A subscriber with `pos` only cannot make kitchen accounts or move a KOT to `preparing`; one with `kds` can, as before.
- Waiter can take payment and close the bill in the slip plan but not void it or exceed the discount limit without a manager PIN; in the
  screen plan the same waiter is refused.
- **Plan switching:** adding `kds` leaves earlier slip KOTs off the kitchen screen; removing it leaves screen KOTs markable as served.
- A failed print never blocks firing; reprint increments the count and carries the REPRINT marker; void prints a VOID slip.
- Slip renderers: golden outputs for 58 mm and 80 mm, long item names, many modifiers, non-ASCII names.
- The full suite also runs under the restricted database role (row-level security), as before.

## 5. Risks and how they are handled
- **Waiters handling money.** Same controls as cashiers: own shift, discount approvals, no voids, everything in the audit trail. Flagged so owners can review waiter-closed bills.
- **Lost or unprinted slip means a forgotten order.** A visible "slip not printed" state, one-tap reprint, and the floor sees every slip still waiting to be marked served.
- **Reprint abuse.** Counted, attributed and marked on paper.
- **Switching plans mid-service.** The `fulfilment` flag keeps the two worlds from colliding.
- **No printer hardware yet.** Phases 1-3 need none; Phase 4 does.

## 6. Open questions (defaults in brackets, each is quick to change)
1. Which printer(s) will pilot restaurants use, and how are they connected: LAN, Bluetooth or USB? [assume network thermal printers, 80 mm]
2. Should the slip print **automatically** when the order is sent, or only when the waiter taps print? [auto-print as an option in the printer table, manual in Phase 3]
3. One slip per **station**, or one combined slip? **Decided: one slip per send for the whole table (slip plan only).**
4. Should **captains** also be allowed to close bills in the slip plan? [no: waiter only, as you said]
5. Should unserved slips **block** closing the bill? [no: show a warning, bill closes as usual]
6. Does the owner need a switch to let waiters take payment even on the screen plan? [no, out of scope]

## 7. Status

- **Phase 1 (module and rules): done.** `kds` module and its migration (existing plans keep it), `kots.fulfilment`, slip-mode status
  rules, waiter billing on plans without `kds`, kitchen-role restrictions. 18 new tests; full suite green in normal mode and as a
  restricted database role. Demo and console need no change: `kds` appears automatically as a module.
- **Phase 2 (slip rendering): done.** `GET /kots/{id}/slip` in JSON, text, HTML and ESC/POS; markers, print confirmation,
  audited reprints, VOID slips.
- **Phase 3 (POS slip workflow): built, checked on web.** The waiter app reads the plan from `/me/entitlements` (re-checked every
  minute). On the slip plan: Send prints the slip through the system print dialog (`expo-print` on tablets, a sandboxed frame on
  web) and asks "Did the slip print?"; tickets show "Slip not printed" / Print slip / Reprint; Mark served from queued;
  cancelling takes a manager PIN and prints a VOID slip; waiters get Bill and payment; the floor shows "to serve" and
  "not printed" chips. Not yet tried on a real tablet or printer.
- Phases 4-5: not started.
