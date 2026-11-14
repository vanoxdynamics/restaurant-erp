# Offline billing, phase 1: build spec

For whoever builds phase 1 of [05-offline-billing-plan.md](05-offline-billing-plan.md). Read that plan first (sections 1, 3 and 4).
This file says exactly what to build, in what order, and how to know it is right. Where it and the plan disagree, this file wins;
the differences are listed in section 9.

**Phase 1 adds the groundwork and no offline behaviour.** After it, no device can bill offline yet, and with nothing switched on
the product behaves exactly as before.

## 0. Ground rules

1. **Server only**, plus types in `packages/api-client`. No screens in this phase.
2. **Copy the inventory module's shape.** It is the newest module and the pattern to follow: `app/models/inventory.py`,
   `app/schemas/inventory.py`, `app/modules/inventory/{router,service}.py`, `tests/modules/test_inventory.py`, and migration
   `alembic/versions/2026_11_12_0900-e5b9f7c32d84_inventory_module.py`. The customers module (`app/modules/customers`) is a second,
   simpler example.
3. **Every existing test must pass unchanged**, except the one line named in step 1. If an existing test fails, the change is
   wrong; do not edit the test to suit it.
4. **Do not add or change a migration or a model while a test run is in progress.** The suite ends with `alembic check`
   (`tests/test_migrations.py`), which fails if models or migrations changed after the run began.
5. **One Alembic migration for the whole phase**, `down_revision = 'f6c1a8d43e95'` (customers). Write it by hand in the style of
   the inventory migration; `alembic check` must then report no differences.
6. **Row-level security:** every new table needs `ENABLE` + `FORCE ROW LEVEL SECURITY` and the `tenant_scope` policy. `devices`
   has `branch_id`, so it uses the branch form of the policy (copy `SCOPE` from the inventory migration). Service code must
   still filter by organization or branch itself; the policy is a backstop.
7. **Run tests in both modes** before calling it done (commands in section 8).
8. **Errors** use the classes in `app/core/errors.py` with a `code` and `details`. Never return another business's existence:
   a device, order or setting of another organization is `404`, or for a customer-style reference `422`.

A file already exists from a first start: `services/api-fastapi/app/models/devices.py`. It matches section 3 and is not yet
imported anywhere. Check it against section 3, then register it.

## 1. Step 1: module key

- `app/core/modules.py`: add `OFFLINE_BILLING = "offline_billing"` to `ModuleKey`, and to `MODULES`:
  name "Offline billing", description "Counter devices keep billing in cash when the internet is down, and send the sales
  later", `depends_on=(ModuleKey.POS,)`. Not core.
- Migration: recreate the three module-key check constraints with the new key (the `_recreate` helper in the inventory
  migration), and add the module to the plan with code `legacy_all_modules`, exactly as inventory did. Downgrade deletes the
  rows with this key from `plan_modules`, `organization_modules`, `platform_modules`, then restores the old constraints.
- `tests/modules/test_entitlements.py` line 59: the expected set gains `"offline_billing"`. This is the only edit allowed to an
  existing test.
- `packages/api-client/src/types.ts`: add `'offline_billing'` to the comment that lists module keys (near "Module keys, e.g.").

Having the module does nothing by itself. The business switch and the device switch (steps 4 and 3) both default to off.

## 2. Step 2: a sale id that makes duplicates impossible

Why: retries are recognised today by a Redis key that lasts 24 hours and is tied to the signed-in user
(`app/core/idempotency.py`). A sale sent days later, or by a different cashier, must still not be billed twice.

**Database**

| Change | Detail |
|---|---|
| `orders.client_sale_id` | `String(64)`, nullable |
| Index `uq_orders_branch_client_sale` | unique on `(branch_id, client_sale_id)` where `client_sale_id IS NOT NULL` |

**Behaviour**

- `OrderCreate` gains `client_sale_id: Optional[str]`, pattern `^[A-Za-z0-9_-]{8,64}$`. `OrderRead` returns it.
- In `create_order` (`app/modules/orders/service.py`), **before anything else** (before the token counter, before the table
  lock): if `client_sale_id` is set and an order with that id exists in the resolved branch, return that order, loaded with
  `load_order`. Nothing else happens: no new number, no events.
- Two requests racing with the same id: the second hits the unique index when it flushes. Catch `IntegrityError`, check the
  constraint name is `uq_orders_branch_client_sale` (re-raise anything else), roll back, and return the existing order.
- The first order wins even if the second request's items differ. Do not compare payloads.
- The router keeps returning `201` in both cases. The existing `X-Idempotency-Key` handling is untouched and still required.
- Orders created without the field behave exactly as today.

**Tests** (`tests/modules/test_offline_groundwork.py`, or split per step)

1. Same `client_sale_id`, different `X-Idempotency-Key`, same cashier: one order in the database, both answers have the same
   `id` and `order_number`.
2. Same id sent by a different cashier of the same branch: same order back.
3. Same id with different items: the first order back, unchanged.
4. Same id in another branch of the same business, and in another business: a new order each (the rule is per branch).
5. Two concurrent requests with the same id (`asyncio.gather`): one order, two successful answers.
6. Bad ids (too short, spaces, 65 characters): `422`.
7. A daily token is not consumed by the duplicate (café category: token of the next real sale is +1, not +2).

## 3. Step 3: devices become records

Why: a linked device today holds a token valid for 365 days (`link_device` in `app/modules/staff/service.py`) and exists
nowhere else. It cannot be listed, named, cut off or given a bill series.

**Table `devices`** (model `Device`, file already written)

| Column | Type | Notes |
|---|---|---|
| `id` | UUID | primary key |
| `branch_id` | UUID, FK `branches.id`, indexed | |
| `device_id` | `String(100)`, unique | the id the device chose when linking; the `sub` of its token |
| `name` | `String(80)`, nullable | set by a manager |
| `offline_enabled` | boolean, default false | switch 3 |
| `series_letter` | `String(1)`, nullable | `A` to `Z` |
| `last_seen_at` | timestamptz, nullable | |
| `revoked_at` | timestamptz, nullable | |
| `revoked_by_id` | UUID, FK `staff_users.id`, nullable | |
| `created_at`, `updated_at` | | `TimestampMixin` |

Constraints: unique `(branch_id, series_letter)` where the letter is not null; check `series_letter` is null or one capital
letter; check `offline_enabled` implies a letter and not revoked.

**Rules**

- **Linking creates the record.** In `link_device`: no record with this `device_id` → create it. A record exists, same branch,
  not revoked → reuse it. Otherwise (revoked, or another branch) → `409 device_id_taken`. The code is still consumed only on
  success paths as today; do not change the rate limiting.
- **Devices linked before this table** have valid tokens and no record. The first time such a device presents its token, create
  its record (insert, on conflict on `device_id` do nothing, then read it). This is the only way a record is created outside
  linking.
- **Revoked means the token is dead.** Add one dependency in `app/core/auth.py`, `require_device`, that: decodes the token as
  `require_device_branch` does; finds or creates the record as above; raises `401 device_revoked` if `revoked_at` is set, or if
  the token's branch differs from the record's; updates `last_seen_at` only when it is null or more than 5 minutes old (so a
  polling device does not write on every request); returns the `Device`. Use it in:
  1. `GET /staff/auth/roster` (replace `require_device_branch`),
  2. `require_print_station` (it already has a database session),
  3. the lease endpoint (step 5).
  Keep `require_device_branch` as the pure decoder; `require_device` calls it.
- **Series letter:** given the first time offline is enabled for the device: the first letter `A` to `Z` not used by any device
  of that branch, **including revoked ones**. It is never cleared or changed afterwards. No letter left →
  `409 no_series_left`. Take the branch row `FOR UPDATE` while choosing, so two managers cannot give out the same letter.
- **Enabling offline** (`offline_enabled: true`) needs, in this order: the device not revoked (`409 device_revoked`); the
  `offline_billing` module (`enforce_module`, giving the usual `403 module_disabled`); the branch code at most 4 characters
  (`422 branch_code_too_long`, see section 6). It does **not** need the business switch; the lease checks that.
- **Disabling offline** keeps the letter.
- **Revoking** sets `revoked_at`, `revoked_by_id`, and `offline_enabled = false`. It cannot be undone; the device is linked
  again with a new setup code, which gives it a new `device_id` and so a new record. Revoking twice is a no-op that returns
  the record.

**Endpoints** (new router `app/modules/devices/router.py`, prefix `/staff/devices`; include it in `app/main.py` **before**
`staff_router`). Owner and manager with a password session, i.e. the same `managers` dependency as the staff router.

| Endpoint | Does |
|---|---|
| `GET /staff/devices?branch_id=&include_revoked=false` | devices of the branch (`resolve_branch_id`), newest first |
| `PATCH /staff/devices/{id}` | `name`, `offline_enabled`; only the fields sent change |
| `POST /staff/devices/{id}/revoke` | cut the device off |

A device of a branch the caller cannot access is `404` (`require_branch_access`).

**Not changed in this phase:** `POST /staff/auth/pin` does not look at the device record. A revoked device cannot fetch the staff
list, so the app cannot reach the PIN pad, but the endpoint itself still answers anyone with a branch, a staff id and a PIN, as
today. Leave it; note it in `docs/api.md`.

**Tests**

1. Linking creates a record with the right branch; listing shows it; a cashier and a PIN-only manager session get `403`.
2. A token made as before this change (build one with `create_access_token`, no record) works for the roster, and a record
   appears after the call.
3. After revoke: roster `401 device_revoked`; the print-station endpoints `401`; lease `401`; the device still appears with
   `include_revoked=true` and not without.
4. `last_seen_at` is set on first contact and not rewritten by a second call straight after.
5. Enabling offline: without the module `403`; branch code `LONG12` → `422`; success gives letter `A`, a second device `B`.
6. Revoke the device holding `A`, enable a third device: it gets `C`, never `A`.
7. Disable then enable again: same letter.
8. Another business's manager: `404` on patch and revoke; their list never shows the device.
9. Linking with a `device_id` that belongs to a revoked record: `409 device_id_taken`, and the setup code is still usable.
10. Existing tests in `tests/modules/test_staff.py` and `tests/modules/test_print_station.py` pass unchanged.

## 4. Step 4: the business's own switch and limits

**Columns on `organizations`**

| Column | Type | Default | Check |
|---|---|---|---|
| `offline_allowed` | boolean | false | |
| `offline_max_hours` | integer | 24 | 1 to 72 |
| `offline_max_sales` | integer | 300 | 1 to 2000 |
| `offline_max_cash` | `Numeric(10, 2)` | 50000.00 | above 0, at most 500000 |

**Endpoints** in `app/modules/business/router.py`

| Endpoint | Who | Does |
|---|---|---|
| `GET /business/offline` | owner, manager | the four values, plus `module_enabled` (does the subscription have `offline_billing`) |
| `PATCH /business/offline` | **owner only** | change any of the four; only the fields sent change |

Turning `offline_allowed` on without the module is `403 module_disabled`. Changing limits or turning it off is always allowed.
These values are **not** added to `/me/entitlements` in this phase (see section 9).

**Tests:** defaults; a manager can read and cannot patch; out-of-range values `422`; on without the module `403`; another
business's values are never returned or changed.

## 5. Step 5: the lease

`POST /staff/devices/lease`, authenticated by **`X-Device-Token` only** (`require_device`), no staff session. After
`require_device`, call `scope_session(db, organization_id)` as `require_print_station` does.

**Refusals**, checked in this order, all `403 offline_not_allowed` with `details.reason`:

| `reason` | When |
|---|---|
| `subscription_inactive` | the organization's access is not `full` (read-only or blocked) |
| `module_disabled` | no `offline_billing` module (this also covers the platform-wide kill switch, which entitlements already apply) |
| `business_disabled` | `offline_allowed` is false |
| `device_disabled` | the device's `offline_enabled` is false |

A revoked device never gets this far (`401`). An inactive branch is `401 device_not_linked`, as in `require_print_station`.

**The lease** is a JWT made with `create_access_token`: subject = the device's `device_id`; claims `typ: "offline_lease"`,
`branch_id`, `org`, `series` (the letter), `max_sales`, `max_cash` (as a string); `expires_delta` = `offline_max_hours`.

**Response** (`OfflineLeaseRead`): `lease`, `device_id`, `branch_id`, `series_letter`, `issued_at`, `expires_at`, `max_sales`,
`max_cash`, `server_time`.

Each call gives a fresh lease; nothing is stored. Add `decode_offline_lease(token) -> Optional[dict]` next to
`decode_access_token`, returning the claims only when the signature and expiry are valid **and** `typ == "offline_lease"`.
Phase 4 will use it; phase 1 tests it.

**A lease must never work as a login.** In `resolve_token` (`app/core/auth.py`), reject any token whose `typ` is set (today it
rejects only `"device"`; staff tokens carry no `typ`). `require_device_branch` already demands `typ == "device"`.

**Tests**

1. Each refusal reason, one at a time, with the other three satisfied.
2. Success: the claims decode with `decode_offline_lease`; `expires_at - issued_at` equals the business's hours; limits match.
3. Turn the business switch off, ask again: refused. Turn the device switch off: refused. Platform-wide kill switch on the
   module (`platform_modules` row, then `invalidate_entitlements`): refused.
4. Suspend the organization, and separately let its subscription lapse: refused with `subscription_inactive`.
5. The lease sent as `Authorization: Bearer` to `GET /staff/me`: `401`. The lease sent as `X-Device-Token` to the roster:
   `401`. A device token and a staff token given to `decode_offline_lease`: `None`.
6. Device of business A cannot obtain a lease describing business B (there is no input to tamper with; assert the claims carry
   A's branch and organization).

## 6. Bill numbers: what phase 1 must and must not do

Phase 1 prints no offline bills, but it gives out the series letters, so the format is fixed now:

```text
online (today)    {CODE}/{FY}/{000001}        BLR1/2627/000001     6-digit counter
device series     {CODE}{LETTER}/{FY}/{00001} BLR1A/2627/00001     5-digit counter
```

GST allows at most 16 characters. The device form is `len(CODE) + 12`, so the branch code must be **at most 4 characters** for
a device to be enabled for offline: that is the `422 branch_code_too_long` rule in step 3. 99,999 offline bills per device per
year is ample.

Do not change `app/core/numbering.py` in this phase.

**Already wrong today, separate from offline:** branch codes may be up to 6 characters (`app/schemas/floor.py`, pattern
`^[A-Z0-9]{2,6}$`), which makes an online bill number up to 18 characters. Do not fix it inside this phase; it needs its own
decision, because existing branches may already have long codes.

## 7. Documentation

- `docs/api.md`: a section "Devices and offline groundwork" covering the endpoints, error codes and the PIN-login note above.
- `docs/operations.md`, section 4 (incident playbooks): "A device is lost or stolen: revoke it in Staff > Devices (API:
  `POST /staff/devices/{id}/revoke`)".
- `packages/api-client`: types `Device`, `OfflineSettings`, `OfflineLease`; client methods for the six endpoints;
  `clientSaleId` on `createOrder`. No hooks or screens yet.

## 8. Done when

Run from `services/api-fastapi`, with Postgres and Redis up (`docker compose up -d postgres redis`):

```bash
venv/Scripts/python -m ruff check app tests
```

```bash
venv/Scripts/python -m pytest -q
```

```bash
RLS_TEST_ROLE=rls_app TEST_DATABASE_URL=<the DATABASE_URL from .env with the database name restaurant_erp_test_rls> TEST_REDIS_URL=redis://localhost:6379/14 venv/Scripts/python -m pytest -q
```

And from the repository root:

```bash
pnpm typecheck
```

All of the following hold:

1. Both test runs pass, with only the one allowed edit to an existing test.
2. `alembic upgrade head`, `alembic downgrade -1`, `alembic upgrade head` all succeed on a database with data in it.
3. With no switch turned on, no existing endpoint's response has changed except for the new optional fields
   (`OrderRead.client_sale_id`).
4. A revoked device is refused on all three device paths.
5. The same sale id can never produce two orders in one branch.

Each full run takes about 20 minutes on the development machine. Run the new test file alone while working, and the full
suite in both modes once at the end.

## 9. Where this differs from the plan

| Plan said | This spec says | Why |
|---|---|---|
| Device series `FM1A/2627/000123` (6 digits) | 5-digit counter, branch code at most 4 | The 6-digit form only fits 16 characters with a 3-character code. |
| `POST /devices/lease` | `POST /staff/devices/lease` | The device endpoints already live under `/staff/devices`. |
| Offline setting returned with `/me/entitlements` | Separate `GET /business/offline`; the lease carries the limits | Entitlements are cached per organization; keeping them unchanged avoids touching every request's hot path in a groundwork phase. Revisit in phase 3 when the app needs it. |
| "Existing linked devices are added on their next contact" | Same, made precise: on first use of `require_device` | |

## 10. Not in phase 1

Any offline sale, the offline pack, the accept-sales endpoint, the device bill counter itself, screens in the admin app or the
POS, changes to PIN sign-in, audit-log entries for device changes (the audit action list has a database check; add them with
the admin screens).
