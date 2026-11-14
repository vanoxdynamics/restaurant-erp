# Plan: offline billing as a module that can be switched on and off

Status: **phase 1 (groundwork) built; no offline billing yet.** Written 13 Nov 2026 against the code on `platform-admin-console`.
Phase 1 is described in [06-offline-phase-1-spec.md](06-offline-phase-1-spec.md) and in `docs/api.md`, "Devices and offline
groundwork". Phases 2 to 6 are not started.

## Summary

- **Online billing stays exactly as it is** and remains the default for every business. Nothing in this plan changes the
  online path until a switch is turned on.
- **Offline billing becomes a module** (`offline_billing`), switched on per plan or per subscriber in the platform console, like
  the kitchen display and inventory. There are three switches, and all three must be on for a device to bill offline.
- A device with offline billing **still works online whenever it can**. It bills on its own only while the server cannot be
  reached, and sends those sales up when the connection returns.
- The first version is deliberately narrow: **the retail app (quick sale), cash only, printed bill, no discounts, no voids.**
  Everything else waits for the connection.
- Seven places in today's code assume the server is always there (section 3). Each one is a way to lose or duplicate a sale
  if it is not dealt with, so each has a decision in section 4.

## 1. The three switches

| # | Switch | Who | Where it lives | Off means |
|---|---|---|---|---|
| 1 | **Module `offline_billing`** | You | Platform console: plan, per-subscriber override, and the existing platform-wide kill switch (`platform_modules`) | No device of that business may start a new offline sale. |
| 2 | **"Allow offline billing" for the business**, with its limits | Owner | Admin app, Settings | Same, but the owner's own choice. Limits: longest time offline, most sales, most cash held. |
| 3 | **Offline enabled on this device** | Owner or manager | Admin app, Staff > Devices | That device is online-only. Other devices are unaffected. |

Rules that hold whatever the switches say:

1. **A sale already made is never refused.** If a switch goes off (or the subscription lapses, or the business is suspended)
   while a device holds unsent sales, those sales are still accepted when it reconnects. Only *new* offline sales stop.
2. **Switching off is safe at any moment**: the device finishes sending what it holds, then behaves as today.
3. **Online is not a module.** It is the core (`pos`) and cannot be disabled. "Toggle" always means offline on or off.
4. **Going offline is automatic, never a button.** A cashier cannot choose to bill offline while the server is reachable;
   otherwise offline becomes a way around manager approvals and stock.

### The offline lease

A device cannot ask the server for permission while offline, so permission is given in advance. Each time an enabled device
talks to the server it receives a **lease**: "you may bill offline until *time T*, at most *N* sales, at most *₹X* cash", signed
by the server. No lease, an expired lease, or a used-up lease means the device shows "Connect to the internet to continue" and
makes no new sales. This is what makes switches 1 to 3, a lapsed subscription and a suspension effective on a device that is not
connected: they simply stop the lease being renewed. Suggested defaults: 24 hours, 300 sales, ₹50,000.

## 2. What works in each mode

| | Online (today) | Offline, version 1 | Offline, later |
|---|---|---|---|
| Quick sale: search, scan, weigh | yes | yes, from the catalog saved on the device | |
| GST bill, printed | yes | yes, with the device's own bill series | |
| Cash | yes | yes | |
| Card / UPI on the Pine Labs terminal | yes | **no** | yes, if the terminal's own SIM is up (phase 5) |
| Discount | yes, manager PIN above the limit | **no** | small discounts under the limit |
| Void a bill, reverse a payment | yes, manager PIN | **no** | no |
| Customer on the bill | yes | name and phone typed, matched when sent | saved customer list |
| Stock left on tiles | yes | last known, marked "as of 10:42" | |
| Stock deducted | at payment | when the sale is sent (stock may go negative; it already may) | |
| Café token number, KOT | yes | **no** (version 1 is for shops without a kitchen) | printed KOT, device token series |
| Restaurant tables, waiter phones, kitchen screen | yes | **no, not planned** | |
| Day end / cash count | yes | blocked on a device holding unsent sales | |
| Sign in with PIN | yes | only the person who last signed in online on that device | |

Restaurants are out of scope on purpose: waiter phones, the counter and the kitchen share one live order, and that cannot be
made correct on separate disconnected devices.

## 3. What in today's code breaks offline

Checked in the code, not assumed.

| # | Today | Where | Why it matters offline |
|---|---|---|---|
| A | Retries are recognised by a key kept in Redis for **24 hours**, tied to the **signed-in user** | `app/core/idempotency.py` (`COMPLETED_TTL_SECONDS = 86400`, key `idem:{scope}:{user_id}:{key}`) | A sale sent after 24 hours, or by a different cashier than made it, is **not** recognised as a repeat and would be billed twice. |
| B | Bill numbers come from one server counter per branch and financial year, with no gaps | `app/core/numbering.py` | A device cannot ask for the next number. Two offline devices would collide. |
| C | A bill number is 16 characters when the branch code has 4 (`BLR1/2627/000001`) | same | GST allows at most 16. A device series must fit in the same 16. Separately, and already true today: a branch code may be up to 6 characters (`app/schemas/floor.py`; the column allows 20), so an **online** bill number can be 18 characters now. Worth limiting branch codes to 4 regardless of this plan. |
| D | Devices are not records. A linked device holds a token valid for 365 days that cannot be withdrawn | `link_device` in `app/modules/staff/service.py` | There is nothing to switch on, give a series to, or cut off if a device is lost. |
| E | GST is worked out only on the server, in `Decimal`, rounding half up | `app/modules/billing/tax.py`, `app/core/money.py` | The device must reach the same paise for every bill, in JavaScript. |
| F | The printed bill is laid out by the server | `app/modules/billing/receipt.py` | The device has nothing to print from. |
| G | The app keeps the catalog and the cart in memory only; its only storage is the secure key store, meant for small secrets | `apps/pos-app/src/stores` | Nothing survives a restart, and there is nowhere to keep thousands of items or a queue of sales. |
| H | PIN sign-in is checked by the server; the session is in memory, locks after 2 idle minutes and expires after 8 hours | `sessionStore.ts`, `ACCESS_TOKEN_EXPIRE_MINUTES` | After the first idle lock offline, nobody can sign in. |
| I | Day end adds up cash by payment time since the day was opened | `close_cash_day` in `app/modules/staff/service.py` | A sale sent after the day was closed is missing from that day's cash, and is counted against the wrong day or none. |
| J | A business whose subscription lapsed is read-only: every write is refused | `app/core/entitlements.py` | Sales made in good faith before the lapse would be refused when sent. |

## 4. Decisions for you to review

| # | Decision | Recommendation | Why |
|---|---|---|---|
| O1 | **Which apps** | Retail app only (grocery, general retail). Cafés in a later phase. Never the restaurant apps. | One device, no kitchen, no shared order. |
| O2 | **Payments offline** | Cash only in version 1. | Card and UPI need the bank; an unconfirmed card payment with no server to record it is the riskiest case in the product. |
| O3 | **Bill numbers** (fixes B, C) | Every offline-enabled device gets its own series: one letter added to the branch code and a 5-digit counter, e.g. `BLR1A/2627/00001`. A branch code of at most 4 characters keeps this within 16; a device at a branch with a longer code cannot be enabled. Online bills keep the branch series. | GST permits several series. No two devices can ever produce the same number, and no gaps appear in any series. |
| O4 | **Devices become records** (fixes D) | New `devices` table: branch, name, series letter, offline on/off, last seen, revoked. Existing linked devices are added on their next contact. | Needed for switch 3, for the series, and to cut off a lost device. |
| O5 | **Duplicates** (fixes A) | Every offline sale carries an id made on the device. The server stores it on the order with a unique rule in the **database**: the same sale sent twice, by anyone, at any time, returns the first result. | Redis is the wrong place for a guarantee that must hold for days. |
| O6 | **Who is right about the amount** (fixes E) | The device works out GST with the same rules. The server works it out again from the lines the device sent. If they agree (they must), fine. If not, **the printed amount stands**, the bill is stored as printed, and it is flagged for you to review. | The customer already has the paper. A bill must never change after printing. |
| O7 | **Prices offline** | The price on the device when the sale was made is the price. A later price change on the server does not alter it. Sales made on an old catalog are listed for the owner. | Same reason. |
| O8 | **Printing** (fixes F) | The bill layout is ported to the app and checked against the server's with shared sample bills. Offline bills print "OFFLINE" small at the foot until sent. | So a reprint online is identical. |
| O9 | **Storage** (fixes G) | A real database on the device (SQLite) for the catalog and the queue of sales. A sale is written to it **before** it prints. | Survives restarts, crashes and a flat battery. |
| O10 | **Sign-in offline** (fixes H) | Only the cashier who last signed in online on that device can unlock it offline, for the life of the lease. Five wrong PINs lock offline mode until the device reconnects. Managers and owners cannot sign in offline. | A 4-digit PIN checked on the device is weak; this limits what it protects to one cashier's cash sales. |
| O11 | **Late sales and day end** (fixes I) | A device holding unsent sales cannot close the day. If another device closed it, late sales are added to **the day they were made**, shown as "added after closing" on that day's report with the cash difference, and the owner is told. | Sales belong to the day the customer paid. Nothing is hidden or moved. |
| O12 | **Lapsed or suspended while offline** (fixes J) | Sales made inside a valid lease are accepted whatever the subscription says now. | Rule 1. The lease already limits the exposure. |
| O13 | **The device's clock** | The device remembers the server's time at last contact and refuses to bill offline if its own clock has gone backwards or jumped more than the lease allows. Both the device's time and the time received are stored. | Bill dates are legal dates; clocks on cheap devices drift or get changed. |
| O14 | **What it costs the shop** | A paid add-on module, or included in a higher plan. No code needed either way. | Plans and modules already work per subscriber. |

## 5. What gets built

### 5.1 Server

| Change | Detail |
|---|---|
| Module | `offline_billing` in `app/core/modules.py` (depends on `pos`), migration for the module-key checks, as was done for `inventory`. |
| Devices | `devices` table and endpoints to list, name, enable offline, revoke. Linking a device creates its record. A revoked device's token stops working. |
| Business setting | `offline_allowed` and the three limits, edited by the owner, returned with `/me/entitlements`. |
| Lease | `POST /devices/lease`: returns the signed lease and the server time. Refused when any switch is off, or the business is not in full access. |
| Offline pack | `GET /devices/offline-pack`: catalog with prices, GST rates, HSN, units, barcodes; branch and business details for the bill; a catalog version. Sent only when changed. |
| Accept sales | `POST /devices/offline-sales`: a batch. Each sale is checked and stored in its own transaction as order + bill (device series number) + cash payment, with the device's sale id unique in the database. The answer lists each sale: stored, already stored, or needs attention with the reason. |
| Never drop a sale | A sale that cannot be stored cleanly (item deleted, amounts disagree, lease exceeded) is still kept, in a review list, with everything the device sent. Owners and you can see and resolve it. |
| Day and reports | Late sales land on their own business date; daily report gains "added after closing". |
| Stock, customers | Stock is deducted when the sale is stored. A typed phone number is matched to a customer or creates one. |
| Events | Stored offline sales raise the usual events, marked so nothing prints twice. |

### 5.2 Retail app

| Change | Detail |
|---|---|
| Local database | Catalog, queue of sales, device series counter, lease. |
| Billing on the device | GST and bill layout ported to TypeScript, working in whole paise. |
| Connection state | One place decides online or offline, from real request results (not the phone's signal icon). The existing 20-second timeout becomes a short probe once offline. |
| Offline sale flow | Quick sale > "Cash received" > write to the database > print. No bill screen round trip. |
| Banner | "Offline. 4 sales waiting to be sent. Cash only." Tapping it lists them. |
| Sending | Starts by itself on reconnect, oldest first, in small batches, and after every app start. |
| Guards | Signing out, unlinking the device and day end are refused while sales are unsent, with the reason. |
| Simulator | An "offline" switch in the development simulator, so every case can be tried in the browser. |

### 5.3 Admin app and platform console

- **Admin app:** the business switch and limits; the device list with the offline switch, last seen, unsent count at last
  contact, and revoke; the review list; late sales on the day's report.
- **Platform console:** the module appears with plans and overrides automatically. Add a per-subscriber view of devices,
  leases and the review list for support.

## 6. Phases

Each phase leaves the product shippable with offline still off for everyone.

| # | What | Done when |
|---|---|---|
| 0 | **Questions to Pine Labs** (section 8) | answers in writing |
| 1 | **Groundwork, no offline yet:** devices as records, database-level duplicate rule on orders, module key, business setting, lease endpoint. Build spec: [06-offline-phase-1-spec.md](06-offline-phase-1-spec.md) | all current tests pass unchanged, in normal and row-level-security mode; a lost device can be revoked |
| 2 | **Same answer on both sides:** GST and bill layout in TypeScript, with a shared file of sample bills that both the Python and the TypeScript tests must match to the paisa and to the character | 1,000 generated bills match, including weighed items and every GST rate |
| 3 | **Offline sale on the device:** local database, offline pack, cash sale, device series, print, banner | airplane-mode sale in the simulator and on a Pine Labs device survives an app kill before and after printing |
| 4 | **Sending:** accept-sales endpoint, review list, late-day handling, guards | the failure drills in section 7 all pass |
| 5 | **Pilot** with one grocery, switch on for that subscriber only | two weeks, zero lost or doubled sales, day-end cash matches |
| 6 | Later, each its own decision: cafés (printed KOT, token series), card/UPI when the terminal has its own connection, small discounts, saved customers | |

A rough size: phases 1 to 4 are about as much work as the inventory module twice over. Phase 2 is the one that must not be
rushed.

## 7. How we will know it is safe

These are tested on purpose, automatically where possible, before the pilot:

1. The app is killed between saving a sale and printing it, and between printing and sending.
2. The same batch is sent twice, and sent again by a different cashier three days later: one bill each.
3. The connection drops halfway through a batch.
4. Two devices in one branch sell offline at once: no shared numbers, both series without gaps.
5. The module is switched off, the subscription lapses, and the business is suspended, each while a device holds sales: all
   sales arrive; no new offline sale can start after the lease ends.
6. A price changes and an item is deleted on the server while the device is offline.
7. The day is closed on another device before the offline device reconnects.
8. The device clock is set back a day.
9. A revoked device tries to send, and tries to get a lease.
10. Another business's device sends a sale naming this business's branch: refused (and under row-level security too).
11. The lease runs out mid-shift: the cashier is told clearly and nothing is lost.
12. With every switch off, the app makes **no** new calls and behaves exactly as it does today.

## 8. Open questions

For Pine Labs:

1. Can our app reach our server over the terminal's own SIM when the shop's Wi-Fi is down, or is network access restricted
   to their hosts? (Already listed in [02-pinelabs-integration.md](02-pinelabs-integration.md), section 5.)
2. May a card or UPI transaction be started with a reference made on the device, with no server call first?
3. Does their approval of the app need to be repeated when offline billing is added?

For your accountant:

4. Is a separate bill series per device acceptable in your GST filings, and how should it appear in the export?
5. Is "added after closing" on a day's report acceptable, or must late sales be reported on the day they were received?

For you:

6. The decisions in section 4, most of all O2 (cash only), O6 (printed amount stands) and O11 (late sales go to their own day).

## 9. Risks

| Risk | How it is handled |
|---|---|
| **A sale is lost** because the device is reset, broken or stolen before sending | Sales are saved before printing and sent at the first chance; the lease keeps the number held small; the banner and the guards keep staff aware; the customer's printed bill and the device's printed offline summary remain as paper proof. This risk can be made small, not zero. |
| **A sale is billed twice** | Database-level unique sale id (O5), tested by drill 2. |
| **Device and server disagree by a paisa** | Shared sample bills (phase 2); if it ever happens anyway, the printed amount stands and the bill is flagged (O6). |
| **Offline used to dodge controls** | No discounts, voids or manager sign-in offline; going offline is automatic only; every offline sale is marked as such in reports. |
| **Cash goes missing during a long outage** | Lease limits on time, count and cash; day end shows offline cash separately. |
| **The online path regresses** | Phase 1 changes are behind the module; drill 12; the full existing suite must pass unchanged at every phase. |
| **Bill number over 16 characters** | Branch code length is limited when offline is enabled for a device; checked by a test. |
| **Weak PIN on the device** | Only the last online cashier, lease-limited, five attempts (O10). |

## 10. Not in this plan

Restaurant offline; offline card refunds; offline purchases, stock counts or item editing; syncing between devices without
the server; offline for the admin app or the platform console.
