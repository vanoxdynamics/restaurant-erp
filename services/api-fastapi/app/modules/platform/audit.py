"""Hash-chained, append-only audit trail for platform actions."""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import Request
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.platform import PlatformAuditLog

GENESIS_HASH = "0" * 64
_CHAIN_LOCK_KEY = 7243001  # pg advisory lock id; serializes appends so the chain has no forks
_CHAIN_FIELDS = ("created_at", "admin_id", "action", "target_type", "target_id", "before", "after",
                 "reason", "ip", "user_agent", "request_id")


@dataclass(frozen=True)
class RequestMeta:
    ip: Optional[str] = None
    user_agent: Optional[str] = None
    request_id: Optional[str] = None

    @classmethod
    def from_request(cls, request: Request) -> "RequestMeta":
        return cls(
            ip=request.client.host if request.client else None,
            user_agent=(request.headers.get("user-agent") or "")[:255] or None,
            request_id=getattr(request.state, "request_id", None),
        )


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def compute_hash(prev_hash: str, fields: Dict[str, Any]) -> str:
    canonical = json.dumps({k: fields[k] for k in _CHAIN_FIELDS}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256((prev_hash + canonical).encode()).hexdigest()


def _row_fields(row: PlatformAuditLog) -> Dict[str, Any]:
    return {
        "created_at": row.created_at.astimezone(timezone.utc).isoformat(),
        "admin_id": str(row.admin_id) if row.admin_id else None,
        "action": row.action, "target_type": row.target_type, "target_id": row.target_id,
        "before": row.before, "after": row.after, "reason": row.reason,
        "ip": row.ip, "user_agent": row.user_agent, "request_id": row.request_id,
    }


async def append_audit(
    db: AsyncSession,
    *,
    action: str,
    admin_id: Optional[uuid.UUID] = None,
    target_type: Optional[str] = None,
    target_id: Optional[str | uuid.UUID] = None,
    before: Optional[Dict[str, Any]] = None,
    after: Optional[Dict[str, Any]] = None,
    reason: Optional[str] = None,
    meta: Optional[RequestMeta] = None,
) -> PlatformAuditLog:
    """Add an audit row to the current transaction. The caller commits it together with the change itself."""
    meta = meta or RequestMeta()
    await db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _CHAIN_LOCK_KEY})
    prev_hash = await db.scalar(select(PlatformAuditLog.hash).order_by(PlatformAuditLog.seq.desc()).limit(1)) or GENESIS_HASH
    created_at = datetime.now(timezone.utc)
    fields = {
        "created_at": created_at.isoformat(),
        "admin_id": str(admin_id) if admin_id else None,
        "action": action, "target_type": target_type, "target_id": str(target_id) if target_id else None,
        "before": _jsonable(before) if before is not None else None,
        "after": _jsonable(after) if after is not None else None,
        "reason": reason, "ip": meta.ip, "user_agent": meta.user_agent, "request_id": meta.request_id,
    }
    entry = PlatformAuditLog(
        created_at=created_at, admin_id=admin_id, action=action, target_type=fields["target_type"],
        target_id=fields["target_id"], before=fields["before"], after=fields["after"], reason=reason,
        ip=meta.ip, user_agent=meta.user_agent, request_id=meta.request_id,
        prev_hash=prev_hash, hash=compute_hash(prev_hash, fields),
    )
    db.add(entry)
    await db.flush()
    return entry


async def verify_chain(db: AsyncSession, batch: int = 500) -> Dict[str, Any]:
    """
    Walk the whole chain and recompute every hash. Returns {ok, checked, head_hash, first_bad_seq}.
    Editing or removing a row from the middle breaks the chain; truncating the tail is only detectable by
    comparing `head_hash` with a copy exported earlier, so export it to storage the app cannot write.
    """
    expected_prev = GENESIS_HASH
    checked = 0
    last_seq = 0
    while True:
        rows = (await db.scalars(
            select(PlatformAuditLog).where(PlatformAuditLog.seq > last_seq).order_by(PlatformAuditLog.seq).limit(batch)
        )).all()
        if not rows:
            break
        for row in rows:
            if row.prev_hash != expected_prev or compute_hash(row.prev_hash, _row_fields(row)) != row.hash:
                return {"ok": False, "checked": checked, "head_hash": expected_prev, "first_bad_seq": row.seq}
            expected_prev = row.hash
            checked += 1
            last_seq = row.seq
    return {"ok": True, "checked": checked, "head_hash": expected_prev, "first_bad_seq": None}
