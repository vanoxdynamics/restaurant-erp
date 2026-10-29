# 06 — Staff, authentication, shifts

Fixes: F02 (staff routes), F05 (staff stubs), F12. Depends on: 05. Parallel with: 07, 10.

Files: `app/modules/staff/router.py`, new `app/modules/staff/service.py`, `tests/modules/staff/`.

## Endpoints

| Method & path | Auth | Behavior |
|---|---|---|
| `POST /staff/auth/pin` | public, rate-limited | PIN login (below) |
| `POST /staff/auth/login` | public, rate-limited | password login (below) |
| `GET /staff/me` | any | current user profile (new) |
| `GET /staff` | owner, manager | paginated; manager sees own branch only; filters `role`, `is_active` |
| `POST /staff` | owner, manager | create (below) |
| `PATCH /staff/{id}` | owner, manager | `StaffUserUpdate` (new) |
| `POST /staff/{id}/reset-pin` | owner, manager | `PinResetRequest` (new) |
| `POST /staff/{id}/revoke-sessions` | owner, manager | bump `token_version` (new) |
| `POST /staff/shifts/open` | cashier, waiter, captain, manager | open own shift |
| `POST /staff/shifts/close` | same | close own open shift |
| `GET /staff/shifts/current` | any | own open shift or 404 (new) |

## PIN login (`login_with_pin`)

1. Rate-limit check (`login:pin:{branch_id}:{device_id or client IP}`).
2. `lookup = compute_pin_lookup(branch_id, pin)`; `SELECT ... WHERE branch_id=:b AND pin_lookup=:l AND is_active`.
3. If found, still `verify_pin(pin, user.hashed_pin)` (defense in depth). If not found, run a dummy
   `verify_pin` against a constant hash so response time doesn't reveal existence.
4. Failure → record attempt, raise `Unauthorized("Invalid PIN")`. Success → clear counter.
5. Issue token with claims `role`, `branch_id`, `tv`. PIN tokens: `expires_delta = ACCESS_TOKEN_EXPIRE_MINUTES`
   (8 h shift). Owners/managers **must not** be allowed to PIN-login into admin scope — add claim
   `"amr": "pin"`; analytics/staff-management routes in this file and 11 require `amr == "pwd"` for manager+.
   (Approvals via `approver_pin` still work.)

## Password login

1. Rate-limit per username and per IP.
2. Load active user by `username`; user must have `hashed_password` (else same generic error).
3. `verify_password`; dummy verify on miss. Generic error message for every failure.
4. If `password_hash.verify_and_update` signals a rehash is needed (old bcrypt hash), store the new argon2 hash.
5. Token with `"amr": "pwd"`.

## Create staff

- Manager may only create in own branch and only roles ranked **below** manager (waiter, captain,
  cashier, kitchen). Owner may create any role in any branch. Violations → `Forbidden`.
- Normalize `username` to lowercase. Hash PIN and password; set `pin_lookup`.
- Unique violation on username/email → `Conflict("username taken")`; on `(branch_id, pin_lookup)` →
  `Conflict("PIN already in use at this branch")` (tell the manager to pick another; this is the trade-off of PIN-only login, D2).
- Same rank rule for `PATCH` (can't promote someone to your own rank or above) and `reset-pin`.
- `PATCH is_active=false` also bumps `token_version`.

## Shifts

**Open:** `branch_id` from user; insert `Shift(start_time=utcnow(), opening_cash, status=open)`.
The partial unique index makes a second open shift an `IntegrityError` → map to `Conflict("shift already open")`.

**Close (blind drop):**
1. `SELECT ... FOR UPDATE` the user's open shift; none → `NotFound`.
2. `expected_cash = opening_cash + SUM(payments.amount WHERE payment_mode='cash' AND shift_id = shift.id AND status='success')`
   (payments get `shift_id` from the cashier's open shift in file 09).
3. Set `end_time`, `blind_drop_cash`, `closing_cash = blind_drop_cash`, `expected_cash`, `cash_variance = blind_drop - expected`, `status=closed`.
4. Response: cashier gets `ShiftRead` (no expected/variance — keeps the drop blind); manager+ gets `ShiftReadManager`.
5. If `abs(cash_variance)` > configurable threshold → `record_audit(... "cash_variance" ...)` (add to `AuditAction`).

Manager closing someone else's shift: `POST /staff/shifts/{shift_id}/close` (manager+), same logic.

## Bootstrap

There's no way to create the first owner without auth. Add a CLI: `python -m app.cli create-owner --username ... --branch-code ...`
(prompts for password + PIN via `getpass`). Use `typer` or `argparse`; document it in the service README.

## Tests

- PIN login: success returns token with right claims; wrong PIN 401; PIN from another branch 401;
  inactive user 401; 6 failures → 429; response timing for unknown PIN not trivially shorter (assert dummy verify called).
- Password login: success; wrong password; user without password; bcrypt-legacy hash gets upgraded to argon2.
- Create: manager can create waiter in own branch; manager cannot create manager/owner (403); manager
  cannot create in other branch (403); waiter cannot create anyone (403); duplicate username 409; duplicate PIN in branch 409.
- Deactivation invalidates existing token (401 on next call).
- Shifts: open, second open 409, close computes variance correctly with cash + card payments mixed
  (only cash counts), cashier response hides expected cash, manager response shows it.
- `create-owner` CLI creates a user who can password-login.

## Done when

All six original staff endpoints are implemented, the new ones exist, and tests pass.
