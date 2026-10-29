# 08 — Orders & KOTs

Fixes: F05 (orders + kots stubs), F18. Depends on: 05, 07. Parallel with: 10.

Files: `app/modules/orders/{router,service}.py`, `app/modules/kots/{router,service}.py`, tests.

## Orders

| Method & path | Roles | Notes |
|---|---|---|
| `GET /orders` | any (branch-scoped) | `branch_id` defaults to user's branch; filters `status`, `table_id`, `business_date`; paginated; `selectinload(items→modifiers)` |
| `POST /orders` | captain, waiter, cashier, manager, owner | idempotency **required**; below |
| `GET /orders/{id}` | branch access | 404 if other branch |
| `PATCH /orders/{id}` | same as create | `OrderUpdate` (guest_count, notes) with `version` |
| `POST /orders/{id}/items` | same | append items; body `{version, items: [...]}` (change from bare list so the lock applies) |
| `POST /orders/{id}/items/{item_id}/cancel` | manager+ or approver_pin | new; only `ordered` items cancel freely; `fired`+ items need approval and void their KOT line |
| `POST /orders/{id}/cancel` | manager+ or approver_pin | new; `OrderCancelRequest`; only if no paid bill; voids open KOTs; frees table |

### Create order
1. `branch = user's branch` (or authorized `branch_id` for owners).
2. Dine-in: lock table `FOR UPDATE`; must be in branch, `vacant` (or `reserved`), not merged into another table → else `Conflict`.
   Set table `occupied`.
3. `order_number = await next_document_number(db, branch, "order")`, `business_date = business_date(branch.timezone)`,
   `waiter_id = user.id`, `opened_by_shift_id` = user's open shift if any.
4. Build lines with `_build_order_items(db, items)` (shared with add-items):
   - Load all referenced `MenuItem`s (+ variants + modifiers) in **one** query (`WHERE id IN (...)`), not per line.
   - Item must be `is_active` and `is_available_86`; variant (if given) must belong to the item and be available;
     if the item has variants, `variant_id` is required. Unknown/unavailable → `DomainError(422, "item_unavailable", details={item_id})`.
   - Modifiers must belong to a group linked to the item; enforce each group's `min_selection`/`max_selection`.
   - Snapshots: `name_snapshot`, `variant_name_snapshot`, `unit_price_snapshot = variant.price or base_price`,
     `tax_rate_snapshot`, `station_snapshot = item.station`, `OrderItemModifier` rows with `price_snapshot`.
   - `line_total = q((unit_price + sum(modifier prices)) * quantity)`.
5. `order.subtotal = sum(line_total)`; `total_amount = subtotal` (tax is computed on the bill, file 09 — document this on `OrderRead`).
6. Commit; publish `orders: order.created` and `tables: table.status_changed`.
7. Return with items loaded.

### Update / add items — optimistic lock
- Load order; if `order.version != payload.version` → `Conflict("stale_version", details={"current_version": order.version})`
  **before** mutating (gives a clean 409 instead of relying solely on `StaleDataError`, which still guards the race).
- Order must be `active` → else `InvalidTransition`.
- Add items: append via `_build_order_items`, recompute totals, publish `order.items_added`.

## KOTs

| Method & path | Roles | Notes |
|---|---|---|
| `GET /kots` | kitchen, captain, manager, owner | branch-scoped; filters `station`, `status` (default: queued+preparing+ready), `order_id`; ordered by `created_at`; `selectinload(items)` |
| `POST /kots/fire` | captain, waiter, cashier, manager, owner | idempotency **required**; `KOTFireRequest`; returns `list[KOTRead]` |
| `PATCH /kots/{id}/status` | kitchen, manager, owner | transition per `KOT_TRANSITIONS` |
| `POST /kots/{id}/void` | manager+ or approver_pin | below |

### Fire
1. Lock the order `FOR UPDATE`; must be `active` and in user's branch.
2. Select order items with `status = ordered` (and in `order_item_ids` if given). Lock them
   `FOR UPDATE SKIP LOCKED` is **not** wanted — use plain `FOR UPDATE` so two concurrent fires serialize and
   the second sees zero `ordered` items. Zero items → `Conflict("nothing_to_fire")`.
3. Group by `station_snapshot`. For each station: `kot_number = next_document_number(db, branch, "kot")`,
   create `KOT(branch_id, order_id, station, fired_by_id)` and `KOTItem`s (name = name_snapshot + variant,
   notes include modifier names, quantity).
4. Mark those order items `fired`.
5. Commit; publish one `kots: kot.fired` event per KOT (station boards filter by station).
6. Return the KOTs with items loaded.

### Status update
- Validate transition; `voided` not allowed here. Update `KOTItem.status` and the linked `OrderItem.status`
  (`preparing`/`ready`/`served`) in the same transaction. Publish `kot.status_changed`.

### Void
1. `approver_id = await resolve_approver(db, user, kot.branch_id, payload.approver_pin)`.
2. KOT must not be `served` or `voided` → `InvalidTransition`. (Voiding a served item is a bill-level
   comp/discount, not a KOT void — say so in the error message.)
3. Set `status=voided`, `void_reason`, `voided_by_id=user.id`, `voided_approved_by_id=approver_id`; items `voided`;
   linked order items `cancelled`; recompute order `subtotal`/`total_amount`.
4. `record_audit(KOT_VOID, amount = sum of voided line totals, details={kot_number, reason, items})`.
5. Commit; publish `kot.voided` and `order.updated`; trigger `send_manager_push_alert` as a background task (file 11).

## Tests

- Create dine-in order: snapshots correct, table → occupied, number format `ORD-BLR1-YYYYMMDD-0001`.
- Create on occupied table → 409; 86'd item → 422 with item id; variant from another item → 422;
  modifier max_selection exceeded → 422; missing variant when item has variants → 422.
- Idempotent replay of create returns same order, one row in DB.
- Price change after ordering doesn't change the order.
- Update with stale version → 409 with `current_version`; correct version → version increments.
- Add items to a `cancelled` order → 409.
- Fire: items split into kitchen + bar KOTs with distinct numbers; items become `fired`; second fire → 409 `nothing_to_fire`;
  two concurrent fires → exactly one set of KOTs.
- KOT status: queued→preparing→ready→served OK; queued→served → 409; waiter can't update (403).
- Void: waiter without PIN 403; waiter with manager PIN OK and audit row records both actor and approver;
  served KOT void → 409; order totals drop by the voided amount.
- Cross-branch access to orders/KOTs → 404.

## Done when

No stubs remain in orders/kots; tests pass; every write publishes its event after commit.
