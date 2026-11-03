# 09 — Billing & payments

Fixes: F04 (applied), F05 (billing stubs). Depends on: 08. Parallel with: 10.

Files: `app/modules/billing/{router,service,tax}.py`, tests. Money rules from `00-README.md` apply strictly.

## Tax engine — `app/modules/billing/tax.py` (pure functions, no DB)

```python
@dataclass(frozen=True)
class TaxLine: line_total: Decimal; tax_rate: Decimal
@dataclass(frozen=True)
class TaxBreakdown: subtotal; discount; taxable_value; cgst; sgst; grand_total; round_off

def compute_bill(lines: list[TaxLine], discount: Decimal) -> TaxBreakdown
```
1. `subtotal = sum(line_total)`. `discount <= subtotal` else `DomainError(422)`.
2. Discount is applied **before** tax (GST is charged on the post-discount value). Allocate the discount
   across lines in proportion to `line_total`; give the rounding remainder to the largest line so the
   allocations sum exactly to `discount`.
3. Per line: `tax = (line_total - allocated_discount) * tax_rate / 100`. Sum unrounded per rate.
4. `cgst = q(total_tax / 2)`, `sgst = q(total_tax) - cgst` (so the halves always add up).
5. `grand_total = q(taxable_value + cgst + sgst)`. Optional whole-rupee round-off behind a branch setting
   (`round_off` field, default off) — confirm with the business.

Unit tests with hand-computed fixtures: single 5% line; mixed 5% + 18% lines; discount allocation with
remainder; 100% discount; `Decimal` inputs only (assert passing a `float` raises `TypeError`).

The model's docstring "CGST (2.5%) and SGST (2.5%)" is the special case of a 5% rate; the engine must use
each line's `tax_rate_snapshot`, not a hard-coded 2.5%.

## Endpoints

| Method & path | Roles | Idempotency |
|---|---|---|
| `POST /billing/bills` | cashier, manager, owner | required |
| `GET /billing/bills/{id}` | branch access | — |
| `GET /billing/bills` | cashier, manager, owner | — (new; filters `status`, `business_date`, paginated) |
| `POST /billing/bills/{id}/payments` | cashier, manager, owner | required |
| `POST /billing/bills/{id}/split-payments` | cashier, manager, owner | required |
| `GET /billing/bills/{id}/upi-qr` | cashier, captain, manager, owner | — |
| `POST /billing/bills/{id}/void` | manager+ or approver_pin | optional (new) |

### Generate bill
1. Lock order `FOR UPDATE`; must be `active`, in user's branch, have ≥1 non-cancelled item.
2. Refuse if any order item is still `ordered` (not fired) → `Conflict("unfired_items")` — prevents billing
   food the kitchen never got. (Make it a warning flag `allow_unfired: bool` if the business wants takeaway shortcuts.)
3. Discount: `pct = discount / subtotal * 100`; if `pct > settings.DISCOUNT_APPROVAL_THRESHOLD_PERCENT`,
   `approver_id = resolve_approver(...)` and `record_audit(HIGH_DISCOUNT, amount=discount)`. Set `discount_approved_by_id`.
4. `compute_bill(...)` over non-cancelled lines.
5. `bill_number = next_document_number(db, branch, "bill")`, `business_date` from the order.
6. Insert bill (`status=unpaid`, `paid_amount=0`). The partial unique index prevents a second active bill
   → map `IntegrityError` to `Conflict("order already billed")`.
7. `order.status = billed`; dine-in table → `billed`.
8. Commit; publish `billing: bill.created`, `tables: table.status_changed`.

### Record payment
1. Lock bill `FOR UPDATE`; status must be `unpaid` or `partially_paid`.
2. `amount <= balance_due` → else `DomainError(422, "overpayment")`. Exception: cash may exceed the balance —
   then store `amount = balance_due` and return `change_due` in the response (add the field to a
   `PaymentResult` schema). Decide and document; **default: allow cash overpay with change, reject others**.
3. Insert `Payment(status=success, received_by_id=user.id, shift_id=user's open shift, idempotency_key)`.
   Cash does **not** require an open shift: cash is reconciled per business day via the Z-report. If the receiver
   happens to have an open shift, the payment is tagged with it so per-shift blind-drop variance still works.
4. `paid_amount += amount`; `status = paid` when `paid_amount == grand_total` (set `settled_at=utcnow()`),
   else `partially_paid`.
5. On `paid`: `order.status = completed`; table → `vacant` (and clear merges pointing at it); schedule
   `deduct_bom_recipe_inventory(order_id)` (file 11).
6. Commit; publish `bill.payment_recorded` / `bill.settled`, `table.status_changed`.

### Split payments
Same as above but atomic for all tenders: validate `sum(amounts) == balance_due` exactly (cash-change rule
doesn't apply to splits — keep it simple), insert all payments, settle once. Any failure → nothing is written.

### UPI QR
- Requires `branch.upi_vpa` → else `DomainError(422, "upi_not_configured")`.
- Amount = `balance_due` (not grand total, so partial payments work).
- `upi_intent_url = "upi://pay?" + urlencode({"pa": vpa, "pn": payee_name, "am": f"{amount:.2f}", "cu": "INR", "tr": bill_number, "tn": f"Bill {bill_number}"}, quote_via=quote)`.
- `qr_payload` = same string (client renders the QR). Don't generate PNGs server-side.
- Note in code: confirming a UPI payment needs a PSP webhook — out of scope; the cashier records it
  manually with the UTR as `reference_id`.

### Void bill
- Only `unpaid` bills can be voided directly. Paid bills → refund flow (out of scope; return `Conflict("refund_required")`).
- `resolve_approver`, set `status=voided`, `voided_by_id`, `void_reason`; order back to `active`; table back to `occupied`.
- `record_audit(BILL_VOID, amount=grand_total)`. Bill number is **not** reused (GST: voided invoices stay in the series).

## Tests

- Generate: totals match tax engine; number format `BLR1/2627/000001`; second bill → 409; unfired items → 409;
  10% discount OK without approval, 11% without approver → 403, with manager PIN → OK + audit row; order → billed.
- Idempotent replay of generate/payment returns cached response and doesn't double-insert.
- Payment: partial → `partially_paid`; final → `paid`, order completed, table vacant; card overpay → 422;
  cash overpay returns change; cash without an open shift succeeds untagged; paying a voided bill → 409.
- Concurrency: two simultaneous payments for the full balance → exactly one succeeds.
- Split: exact sum settles; off by ₹0.01 → 422 and nothing persisted.
- UPI: URL contains `am=` balance, `tr=` bill number, URL-encoded payee name; no VPA → 422.
- Void: unpaid OK with approval; paid → 409; audit row.
- Cross-branch → 404; waiter generating bill → 403.

## Done when

No stubs remain in billing; tax engine has 100% line coverage; tests pass.
