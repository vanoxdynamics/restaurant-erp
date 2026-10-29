# 07 — Catalog & floor

Fixes: F05 (catalog + floor stubs), F08, F19 (`category_id` typing). Depends on: 05. Parallel with: 06, 10.

Files: `app/modules/catalog/{router,service}.py`, `app/modules/floor/{router,service}.py`, tests.

## Catalog

| Method & path | Roles | Notes |
|---|---|---|
| `GET /catalog/categories` | any | active only by default; `?include_inactive=true` for manager+; order by `sort_order, name` |
| `POST /catalog/categories` | owner, manager | |
| `PATCH /catalog/categories/{id}` | owner, manager | new; includes `is_active` (soft delete) |
| `GET /catalog/items` | any | `category_id: Optional[UUID] = None`, `station`, `available_only: bool`; `selectinload(variants, modifier_groups→modifiers)` |
| `GET /catalog/menu` | any | new; categories → items → variants/modifier groups in one response for POS boot; add `ETag` = hash of max(`updated_at`) across catalog tables, honor `If-None-Match` → 304 |
| `POST /catalog/items` | owner, manager | creates item + variants + modifier-group links in one transaction; `category_id` must exist and be active → else `NotFound` |
| `PATCH /catalog/items/{id}` | owner, manager | new; uses `MenuItemUpdate` with `exclude_unset=True` |
| `PATCH /catalog/items/{id}/toggle-86` | owner, manager, captain, kitchen | sets flag, commits, `publish_after_commit(topic=catalog, type="item.86_changed")` for **every** branch (global menu, D1) |
| `PATCH /catalog/variants/{id}/toggle-86` | same | new |
| `GET /catalog/modifier-groups` | any | `selectinload(modifiers)` |
| `POST /catalog/modifier-groups` | owner, manager | new; uses the currently unused `ModifierGroupCreate`, with nested `modifiers: list[ModifierItemCreate]` |

Service rules:
- Never return ORM objects with unloaded relationships — every query that feeds `MenuItemRead`
  uses `selectinload(MenuItem.variants)`, `selectinload(MenuItem.modifier_groups).selectinload(ModifierGroup.modifiers)`.
- After `create`/`update`, re-query with the loaders (or `await db.refresh(obj, ["variants", ...])`)
  before returning.
- Price changes don't affect existing orders (they use snapshots) — add a test.

## Floor

| Method & path | Roles | Notes |
|---|---|---|
| `GET /floor/branches` | owner: all; others: own branch only | |
| `POST /floor/branches` | owner | `code` uppercase, unique → `Conflict` |
| `POST /floor/branches/{id}/floors` | owner, manager | new; `FloorCreate` |
| `POST /floor/floors/{id}/tables` | owner, manager | new; `DiningTableCreate`; duplicate number on floor → `Conflict` |
| `GET /floor/branches/{branch_id}/floors` | any with branch access | `selectinload(Floor.tables)`, active only, ordered by `sort_order`, tables by natural number |
| `POST /floor/tables/transfer` | captain, waiter, manager, owner | below |
| `POST /floor/tables/merge` | captain, manager, owner | below |
| `POST /floor/tables/{id}/unmerge` | captain, manager, owner | new; clears `merged_into_table_id` on secondaries, sets them `vacant` if no active order |

### Transfer (`source → target`)
1. Lock both tables `SELECT ... FOR UPDATE` **ordered by id** (prevents deadlock when two captains transfer A→B and B→A).
2. Both must be in a branch the user can access and the same branch.
3. Source must have an `active` order; target must be `vacant` and not merged → else `Conflict`.
4. Move the active order: `order.table_id = target.id` (optimistic version bump happens automatically).
5. `source.status = vacant`, `target.status = occupied`.
6. `record_audit(TABLE_TRANSFER, details={from, to, reason})`.
7. Commit; publish `tables` event `table.transferred` with both ids; publish `orders` event `order.updated`.
8. Return target `DiningTableRead`.

### Merge
1. Lock primary + secondaries ordered by id; same branch and same floor (relax to same branch if the
   business wants cross-floor merges — note in PR).
2. None of the secondaries can have an active order with items (or: move their items into primary's
   order — **default: reject with `Conflict`**, simpler and less surprising).
3. Set `secondary.merged_into_table_id = primary.id`, `secondary.status = occupied`.
4. Audit `TABLE_MERGE`, commit, publish.

## Tests

- Catalog: create category/item with variants and modifier groups → GET returns nested data (no
  `MissingGreenlet`); `available_only` excludes 86'd items; waiter cannot create (403); kitchen can toggle 86;
  toggle publishes an event (assert on a fake publisher); `/catalog/menu` returns 304 with matching ETag;
  inactive category hidden; `category_id` filter works and omitting it works.
- Floor: branch list scoped by role; duplicate table number 409; transfer moves order and statuses,
  writes audit row; transfer to occupied table 409; transfer across branches 403/404; concurrent
  opposite transfers (two sessions via `asyncio.gather`) don't deadlock — one succeeds, the other 409;
  merge sets pointers; merge with a secondary that has an active order 409; unmerge restores.

## Done when

No `NotImplementedError` or hard-coded `[]` in catalog/floor; tests pass.
