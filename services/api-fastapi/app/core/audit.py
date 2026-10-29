import uuid
from decimal import Decimal
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AuditAction
from app.models.audit import AuditLog


def record_audit(
    db: AsyncSession,
    *,
    branch_id: uuid.UUID,
    action: AuditAction,
    actor_id: Optional[uuid.UUID],
    entity_type: str,
    entity_id: Optional[uuid.UUID] = None,
    approver_id: Optional[uuid.UUID] = None,
    amount: Optional[Decimal] = None,
    details: Optional[Dict[str, Any]] = None,
) -> AuditLog:
    """Add an audit row to the current transaction (committed with the business change)."""
    entry = AuditLog(
        branch_id=branch_id,
        action_type=action.value,
        actor_id=actor_id,
        approver_id=approver_id,
        entity_type=entity_type,
        entity_id=entity_id,
        amount=amount,
        details=_jsonable(details or {}),
    )
    db.add(entry)
    return entry


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (uuid.UUID, Decimal)):
        return str(value)
    return value
