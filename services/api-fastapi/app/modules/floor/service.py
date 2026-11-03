import uuid
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, with_loader_criteria

from app.core.audit import record_audit
from app.core.auth import CurrentUser, require_branch_access
from app.core.enums import AuditAction, OrderStatus, TableStatus, Topic
from app.core.errors import Conflict, NotFound
from app.core.entitlements import compute_entitlements, enforce_limit
from app.core.events import commit, queue_event
from app.core.lookups import get_or_404
from app.models.floor import Branch, DiningTable, Floor
from app.models.orders import Order
from app.schemas.floor import BranchCreate, DiningTableCreate, FloorCreate, TableMergeRequest, TableTransferRequest


async def list_branches(db: AsyncSession, user: CurrentUser) -> Sequence[Branch]:
    stmt = select(Branch).where(Branch.organization_id == user.organization_id).order_by(Branch.name)
    if not user.is_owner:
        stmt = stmt.where(Branch.id == user.branch_id)
    return (await db.scalars(stmt)).all()


async def create_branch(db: AsyncSession, user: CurrentUser, payload: BranchCreate) -> Branch:
    ent = await compute_entitlements(db, user.organization_id)
    active = await db.scalar(
        select(func.count()).select_from(Branch).where(Branch.organization_id == user.organization_id, Branch.is_active.is_(True))
    )
    enforce_limit(active or 0, ent.max_branches, "branches")
    branch = Branch(organization_id=user.organization_id, **payload.model_dump())
    db.add(branch)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise Conflict("Branch code already exists", code="branch_code_taken") from exc
    await commit(db)
    return branch


async def get_branch(db: AsyncSession, user: CurrentUser, branch_id: uuid.UUID) -> Branch:
    require_branch_access(user, branch_id)
    return await get_or_404(db, Branch, branch_id)


async def create_floor(db: AsyncSession, user: CurrentUser, branch_id: uuid.UUID, payload: FloorCreate) -> Floor:
    await get_branch(db, user, branch_id)
    floor = Floor(branch_id=branch_id, **payload.model_dump())
    floor.tables = []
    db.add(floor)
    await commit(db)
    return floor


async def create_table(db: AsyncSession, user: CurrentUser, floor_id: uuid.UUID, payload: DiningTableCreate) -> DiningTable:
    floor = await get_or_404(db, Floor, floor_id)
    require_branch_access(user, floor.branch_id)
    table = DiningTable(floor_id=floor.id, status=TableStatus.VACANT.value, **payload.model_dump())
    db.add(table)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise Conflict("Table number already exists on this floor", code="table_number_taken") from exc
    await commit(db)
    return table


def _table_sort_key(table: DiningTable) -> tuple:
    number = table.table_number
    digits = "".join(ch for ch in number if ch.isdigit())
    return (int(digits) if digits else 0, number)


async def list_floors_and_tables(db: AsyncSession, user: CurrentUser, branch_id: uuid.UUID) -> List[Floor]:
    await get_branch(db, user, branch_id)
    stmt = (
        select(Floor)
        .where(Floor.branch_id == branch_id, Floor.is_active.is_(True))
        .options(
            selectinload(Floor.tables),
            with_loader_criteria(DiningTable, DiningTable.is_active.is_(True)),
        )
        .order_by(Floor.sort_order, Floor.name)
        .execution_options(populate_existing=True)
    )
    floors = list((await db.scalars(stmt)).all())
    for floor in floors:
        floor.tables.sort(key=_table_sort_key)
    return floors


async def _lock_tables(
    db: AsyncSession, user: CurrentUser, table_ids: List[uuid.UUID]
) -> Tuple[Dict[uuid.UUID, DiningTable], uuid.UUID]:
    """Lock tables in id order (deadlock-safe) and verify they share one accessible branch."""
    rows = (await db.execute(
        select(DiningTable, Floor.branch_id)
        .join(Floor, DiningTable.floor_id == Floor.id)
        .where(DiningTable.id.in_(table_ids), DiningTable.is_active.is_(True))
        .order_by(DiningTable.id)
        .with_for_update(of=DiningTable)
        .execution_options(populate_existing=True)
    )).all()
    if len(rows) != len(set(table_ids)):
        raise NotFound("Table not found")
    branch_ids = {branch_id for _, branch_id in rows}
    if len(branch_ids) != 1:
        raise Conflict("Tables belong to different branches", code="cross_branch")
    branch_id = branch_ids.pop()
    require_branch_access(user, branch_id)
    return {table.id: table for table, _ in rows}, branch_id


async def _active_order_for_table(db: AsyncSession, table_id: uuid.UUID) -> Optional[Order]:
    return await db.scalar(
        select(Order)
        .where(Order.table_id == table_id, Order.status.in_([OrderStatus.ACTIVE.value, OrderStatus.BILLED.value]))
        .with_for_update()
    )


async def transfer_table(db: AsyncSession, user: CurrentUser, payload: TableTransferRequest) -> DiningTable:
    tables, branch_id = await _lock_tables(db, user, [payload.source_table_id, payload.target_table_id])
    source, target = tables[payload.source_table_id], tables[payload.target_table_id]

    order = await _active_order_for_table(db, source.id)
    if order is None:
        raise Conflict("Source table has no active order", code="no_active_order")
    if target.status != TableStatus.VACANT.value or target.merged_into_table_id is not None:
        raise Conflict("Target table is not vacant", code="table_not_vacant")

    order.table_id = target.id
    target.status = source.status
    source.status = TableStatus.VACANT.value
    # Tables merged into the source follow the party.
    merged = (await db.scalars(
        select(DiningTable).where(DiningTable.merged_into_table_id == source.id).with_for_update()
    )).all()
    for table in merged:
        table.merged_into_table_id = None
        table.status = TableStatus.VACANT.value

    record_audit(
        db, branch_id=branch_id, action=AuditAction.TABLE_TRANSFER, actor_id=user.id,
        entity_type="order", entity_id=order.id,
        details={"from_table": source.id, "to_table": target.id, "reason": payload.reason},
    )
    queue_event(db, branch_id, Topic.TABLES, "table.transferred", target.id,
                {"from_table_id": str(source.id), "to_table_id": str(target.id)})
    queue_event(db, branch_id, Topic.ORDERS, "order.updated", order.id, {"table_id": str(target.id)})
    await commit(db)
    return target


async def merge_tables(db: AsyncSession, user: CurrentUser, payload: TableMergeRequest) -> DiningTable:
    ids = [payload.primary_table_id, *payload.secondary_table_ids]
    tables, branch_id = await _lock_tables(db, user, ids)
    primary = tables[payload.primary_table_id]
    if primary.merged_into_table_id is not None:
        raise Conflict("Primary table is itself merged into another table", code="already_merged")

    for table_id in payload.secondary_table_ids:
        table = tables[table_id]
        if table.merged_into_table_id is not None:
            raise Conflict("Table is already merged", code="already_merged", details={"table_id": str(table_id)})
        if await _active_order_for_table(db, table_id) is not None:
            raise Conflict("Table has an active order; settle or transfer it first", code="table_has_order",
                           details={"table_id": str(table_id)})
        table.merged_into_table_id = primary.id
        table.status = TableStatus.OCCUPIED.value

    if primary.status == TableStatus.VACANT.value:
        primary.status = TableStatus.OCCUPIED.value

    record_audit(
        db, branch_id=branch_id, action=AuditAction.TABLE_MERGE, actor_id=user.id,
        entity_type="table", entity_id=primary.id, details={"secondary_table_ids": payload.secondary_table_ids},
    )
    queue_event(db, branch_id, Topic.TABLES, "table.merged", primary.id,
                {"secondary_table_ids": [str(i) for i in payload.secondary_table_ids]})
    await commit(db)
    return primary


async def unmerge_table(db: AsyncSession, user: CurrentUser, primary_id: uuid.UUID) -> DiningTable:
    tables, branch_id = await _lock_tables(db, user, [primary_id])
    primary = tables[primary_id]
    merged = (await db.scalars(
        select(DiningTable).where(DiningTable.merged_into_table_id == primary.id).with_for_update()
    )).all()
    if not merged:
        raise Conflict("Table has no merged tables", code="not_merged")
    for table in merged:
        table.merged_into_table_id = None
        table.status = TableStatus.VACANT.value
    if await _active_order_for_table(db, primary.id) is None:
        primary.status = TableStatus.VACANT.value
    queue_event(db, branch_id, Topic.TABLES, "table.unmerged", primary.id,
                {"released_table_ids": [str(t.id) for t in merged]})
    await commit(db)
    return primary
