# Pine Labs integration: requirements and UAT plan

Source: Pine Labs **App to App Integration** guide (Plutus Smart / Android POS),
https://developer.pinelabs.com/in/instore/app-integration (read 2026-11-04). The counter app this applies to is described in
[01-counter-app-plan.md](01-counter-app-plan.md).

## 1. How the integration works

Our counter app (the "billing app" in Pine Labs' terms) is installed on the Pine Labs device next to Pine Labs' own app,
**Plutus Smart** (`com.pinelabs.masterapp`). Every payment, print and settlement is a JSON request from our app to theirs, answered
with JSON. That is what "route each call" means.

Pine Labs documents two transports. We use the **hybrid (Intent) transport**, built for apps like ours (React Native):

```text
Intent action  com.pinelabs.masterapp.HYBRID_REQUEST
Request extra  "REQUEST_DATA"   = request JSON
Launched with  startActivityForResult
Response extra "RESPONSE_DATA"  = response JSON (in onActivityResult)
```

The other transport (Messenger over a bound service, action `com.pinelabs.masterapp.SERVER`) stays available if Pine Labs asks for it.

Implementation: a small Expo native module, `apps/pos-app/modules/pinelabs` (Kotlin), exposes one call, `request(json) → json`,
plus `isAvailable()`. Everything else (building requests, reading responses, mapping errors) is TypeScript in
`apps/pos-app/src/devices/pinelabs.ts`, so it can be unit-tested and replaced by a simulator off the device.

Every request carries this header:

| Field | Value |
|---|---|
| `ApplicationId` | **issued by Pine Labs for our app**; build-time setting `EXPO_PUBLIC_PINELABS_APP_ID` |
| `UserId` | the signed-in staff member's username |
| `MethodId` | see below |
| `VersionNo` | `"1.0"` |

A response is successful when `Response.ResponseCode` is `0`. Code **100** means the transaction was started but the result is not
known yet, and must be followed by **Get Status**.

## 2. What we use

| MethodId | Call | Our use |
|---|---|---|
| 1001 | **DoTransaction** | card sale `4001`, card void `4006`, UPI sale `5120`, UPI void `5121`, get status `5122` |
| 1002 | **PrintData** | KOTs, VOID KOTs, customer bills, test print |
| 1003 | **Settlement** | day end: settles the card batch, Pine Labs prints the settlement slip |
| 1004 | **Get Terminal Info** | shown in the counter's settings; stored with each payment |
| 1018 | Additional settings (`kiosk_mode`) | later: lock the device to our app |

Not used in the first release: Bluetooth base (1005/1006), scanners (1007/1009/1010), Upload Invoice (1008), EMI, loyalty, wallets,
NCMC transit.

### 2.1 Card and UPI payment

1. The counter asks our server to **start a terminal payment** for the bill (`POST /billing/bills/{id}/terminal-payments`). The
   server saves it as `pending` (not yet counted) and returns a 10-character **BillingRefNo** (Pine Labs allows at most 10; our
   bill numbers are longer).
2. The app sends **DoTransaction** with `TransactionType` 4001 (card) or 5120 (UPI), `PaymentAmount` in paise, `BillingRefNo`,
   `IsSwipe: true`.
3. Pine Labs takes the card or UPI and prints the bank charge slip.
4. The app reports the result to the server (`POST /billing/payments/{id}/complete`):
   - **Approved** when `ResponseCode` is `0` **and** `ApprovalCode` is not empty (Pine Labs: "presence of non-zero length approval
     code indicates successful authorization").
   - **Declined/cancelled** otherwise; the payment is stored as `failed` with Pine Labs' message.
   - **Unknown** (code 100, or no response at all): the app runs **Get Status** (5122) with the same BillingRefNo and amount. If that
     still gives no answer, the payment stays `pending` and the bill shows it as **unconfirmed** until the cashier resolves it
     (approved with the approval code from the charge slip, or failed).

Stored with each Pine Labs payment (`payments.provider = 'pinelabs'`, details in `provider_meta`): BillingRefNo, ApprovalCode,
RetrievalReferenceNumber, TerminalId, MerchantId, InvoiceNumber, BatchNumber, AcquiringBankCode, AcquirerName, CardType, the
**masked** card number, CardEntryMode, transaction type, Pine Labs' response code and message. We never store or log a full card
number, expiry date or cardholder track data.

### 2.2 Reversal (void)

Same day, before settlement, a manager can reverse a Pine Labs payment, in this order:

1. The server checks the reversal (payment reversible, manager PIN valid) without changing anything (`check_only: true`). A
   wrong PIN stops here, before the card is touched.
2. DoTransaction with `TransactionType` 4006 (card) or 5121 (UPI), the original `InvoiceNo` and `BankCode`, `PaymentAmount` and
   `BillingRefNo`.
3. On success the server marks the payment `reversed`, takes it off the bill's paid amount and reopens the bill
   (`POST /billing/payments/{id}/reverse`, audited). If this last call fails, the app keeps the terminal's answer and offers
   **Record reversal**, so the card is never voided twice.
After settlement it becomes a refund (4002), which is not in the first release.

### 2.3 Printing

PrintData takes an array of lines. Each KOT and bill is rendered by the server as **print rows** for the paper width and mapped:

| Our row | PrintData line |
|---|---|
| normal text | `PrintDataType 0`, `PrinterWidth` 32 (58 mm) or 48 (80 mm), `IsCenterAligned` as the row says |
| bold (item lines, marker) | the same with `textStyle 1` |
| big (station, VOID/REPRINT banner) | `PrinterWidth 24`, bold, centred (rows are already wrapped to fit) |

`PrintRefNo`: a reference up to 10 characters (from the KOT or bill number). `SavePrintData: false`: the server is the copy we keep.

The printer answers with a status: success, out of paper, paper jam, overheated and so on. Success confirms the KOT as printed. Any
other status keeps it in the print queue as **failed** with that reason; it is never assumed printed.

### 2.4 Settlement

Day end calls Settlement (1003). Pine Labs settles the batch and prints its slip; we show the per-bank summary (counts and amounts).
Storing settlement summaries on the server for reconciliation comes later.

## 3. Android requirements

| Item | Value |
|---|---|
| Package (UAT) | `com.vanoxdynamics.pos.counter.uat` |
| Package (production) | `com.vanoxdynamics.pos.counter` |
| Package visibility | `<queries>` for `com.pinelabs.masterapp` and the `HYBRID_REQUEST` intent (Android 11+) |
| Build | Expo prebuild + Gradle (or EAS), `APP_VARIANT=counter-uat`, `EXPO_PUBLIC_PINELABS_APP_ID`, `EXPO_PUBLIC_API_URL` |
| Signing | a dedicated UAT key; the production key is created and stored by the company (losing it means no more updates) |
| Network | HTTPS to our API's fixed domain; ask Pine Labs whether the device restricts domains |

## 4. What Pine Labs needs from us

| Item | Status |
|---|---|
| Company name: Vanox Dynamics | ready |
| Package names (above) | ready |
| Application details: restaurant billing and KOT printing at the counter; card and UPI sale, void, get status, print, settlement | ready (this document) |
| Request and response parameters we use | section 2 |
| A test login for our app (username and PIN on a demo restaurant) | to create on the UAT server |
| UAT APK, signed | after the Android build |
| Signing certificate SHA-256 | after the Android build |
| **NDA** with Pine Labs | business, to arrange |
| **Secure code review report** (OWASP/SANS) | business, to arrange (external auditor) |
| **Penetration test report** (OWASP/SANS) | business, to arrange (external auditor); needs a reachable UAT server |
| Change-management and patching process | short document, to write |

The last four come from Pine Labs' **Security Requirement** section and are needed before their security team signs the app.

## 5. What we need from Pine Labs

1. The **ApplicationId** for UAT and later for production.
2. Confirmation that the **hybrid Intent** transport is right for us.
3. Whether **Get Status (5122)** also resolves **card** transactions, or only UPI.
4. The **device model**, Android version, screen size and **printer width** (24/32/48 characters).
5. A **UAT device** (or remote access), and test merchant/terminal IDs and test cards/UPI.
6. Whether the device restricts network access, and how our domain gets allowed.
7. How updates reach devices after approval (their store/MDM).

## 6. UAT checklist (what we test before handing over)

| # | Case | Expected |
|---|---|---|
| 1 | Card sale, approved | payment recorded with approval code, bill paid, table freed, bank slip printed |
| 2 | Card sale, declined | payment `failed` with Pine Labs' message, bill still unpaid |
| 3 | Card sale, cancelled on the terminal | same as declined |
| 4 | UPI sale, approved / declined | as 1 / 2 |
| 5 | UPI sale returns code 100 | Get Status resolves it |
| 6 | App killed while the terminal is charging | on reopening, the bill shows the payment as unconfirmed; resolving it updates the bill |
| 7 | Split: part cash, part card | bill paid once both are recorded |
| 8 | Void a card payment, same day | payment `reversed`, bill reopened, audit entry |
| 9 | Print KOT, bill, VOID KOT, test print (58 mm) | correct layout, nothing cut off |
| 10 | Printer out of paper | KOT stays failed with the reason; retry prints it once |
| 11 | KOT sent from a waiter's phone | prints at the counter within a few seconds, phone shows "Printed at counter" |
| 12 | Settlement | Pine Labs slip printed, summary shown |
| 13 | Terminal info | terminal ID and store name shown |
| 14 | Plutus app missing or busy | clear message; nothing recorded as paid |
