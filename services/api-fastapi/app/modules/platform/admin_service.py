"""Platform admin accounts, sessions and audit-log queries."""

import uuid
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import PlatformRole
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.core.platform_security import (
    encrypt_secret,
    generate_recovery_codes,
    generate_totp_secret,
    hash_recovery_code,
    provisioning_uri,
    validate_admin_password,
)
from app.core.security import get_password_hash
from app.models.platform import PlatformAdmin, PlatformAuditLog, PlatformRecoveryCode, PlatformSession
from app.core.enums import AlertSeverity
from app.modules.platform.alerts import raise_alert
from app.modules.platform.audit import RequestMeta, append_audit
from app.modules.platform.auth_service import revoke_all_sessions, revoke_session
from app.modules.platform.deps import PlatformPrincipal
from app.schemas.platform import PlatformAdminCreate, PlatformAdminCreated, PlatformAdminRead, PlatformAdminUpdate


def _snapshot(admin: PlatformAdmin) -> dict:
    return {"email": admin.email, "full_name": admin.full_name, "role": admin.role, "is_active": admin.is_active}


async def create_admin_record(db: AsyncSession, *, email: str, full_name: str, role: PlatformRole, password: str):
    """Create an admin with a fresh TOTP secret and recovery codes. Returns (admin, secret, uri, recovery_codes)."""
    email = email.lower()
    try:
        validate_admin_password(password, email)
    except ValueError as exc:
        raise ValidationFailed(str(exc), code="weak_password") from exc

    secret = generate_totp_secret()
    admin = PlatformAdmin(
        email=email, full_name=full_name, role=role.value, hashed_password=get_password_hash(password),
        totp_secret_enc=encrypt_secret(secret),
    )
    db.add(admin)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise Conflict("An admin with that email already exists", code="email_taken") from exc
    codes = generate_recovery_codes()
    db.add_all(PlatformRecoveryCode(admin_id=admin.id, code_hash=hash_recovery_code(c)) for c in codes)
    return admin, secret, provisioning_uri(email, secret), codes


async def create_admin(db: AsyncSession, actor: PlatformPrincipal, payload: PlatformAdminCreate,
                       meta: RequestMeta) -> PlatformAdminCreated:
    admin, secret, uri, codes = await create_admin_record(
        db, email=payload.email, full_name=payload.full_name, role=payload.role,
        password=payload.password.get_secret_value(),
    )
    await append_audit(db, action="admin.create", admin_id=actor.admin.id, target_type="platform_admin",
                       target_id=admin.id, after=_snapshot(admin), meta=meta)
    await raise_alert(db, "admin.created", AlertSeverity.WARNING, f"{actor.admin.email} added operator {admin.email} as {admin.role}",
                      {"by": actor.admin.email, "new_admin": admin.email, "role": admin.role})
    await db.commit()
    return PlatformAdminCreated(admin=PlatformAdminRead.model_validate(admin), totp_secret=secret,
                                provisioning_uri=uri, recovery_codes=codes)


async def rotate_second_factor(db: AsyncSession, admin: PlatformAdmin) -> tuple[str, str, list[str]]:
    """New TOTP secret and recovery codes for `admin`; old codes stop working and every session ends."""
    from sqlalchemy import delete

    secret = generate_totp_secret()
    admin.totp_secret_enc = encrypt_secret(secret)
    admin.totp_last_step = 0
    admin.token_version += 1
    await db.execute(delete(PlatformRecoveryCode).where(PlatformRecoveryCode.admin_id == admin.id))
    codes = generate_recovery_codes()
    db.add_all(PlatformRecoveryCode(admin_id=admin.id, code_hash=hash_recovery_code(c)) for c in codes)
    await revoke_all_sessions(db, admin.id, "2fa_reset")
    return secret, provisioning_uri(admin.email, secret), codes


async def list_admins(db: AsyncSession) -> List[PlatformAdmin]:
    return list((await db.scalars(select(PlatformAdmin).order_by(PlatformAdmin.email))).all())


async def _active_super_admin_count(db: AsyncSession) -> int:
    return await db.scalar(
        select(func.count()).select_from(PlatformAdmin)
        .where(PlatformAdmin.role == PlatformRole.SUPER_ADMIN.value, PlatformAdmin.is_active.is_(True))
    ) or 0


async def update_admin(db: AsyncSession, actor: PlatformPrincipal, admin_id: uuid.UUID,
                       payload: PlatformAdminUpdate, meta: RequestMeta) -> PlatformAdmin:
    admin = await db.scalar(select(PlatformAdmin).where(PlatformAdmin.id == admin_id).with_for_update())
    if admin is None:
        raise NotFound("Admin not found")
    changes = payload.model_dump(exclude_unset=True)
    if admin.id == actor.admin.id and ("role" in changes or changes.get("is_active") is False):
        raise Forbidden("You cannot change your own role or deactivate yourself", code="self_change")

    before = _snapshot(admin)
    new_role = changes.get("role", PlatformRole(admin.role))
    new_active = changes.get("is_active", admin.is_active)
    loses_super = admin.role == PlatformRole.SUPER_ADMIN.value and admin.is_active and (
        new_role != PlatformRole.SUPER_ADMIN or not new_active)
    if loses_super and await _active_super_admin_count(db) <= 1:
        raise Conflict("At least one active super admin must remain", code="last_super_admin")

    for field, value in changes.items():
        setattr(admin, field, value.value if isinstance(value, PlatformRole) else value)
    if "role" in changes or changes.get("is_active") is False:
        admin.token_version += 1  # invalidates outstanding tokens
        await revoke_all_sessions(db, admin.id, "admin_changed")
    await append_audit(db, action="admin.update", admin_id=actor.admin.id, target_type="platform_admin",
                       target_id=admin.id, before=before, after=_snapshot(admin), meta=meta)
    if before["role"] != admin.role or before["is_active"] != admin.is_active:
        await raise_alert(db, "admin.changed", AlertSeverity.WARNING,
                          f"{actor.admin.email} changed {admin.email}: role {before['role']} -> {admin.role}, active {before['is_active']} -> {admin.is_active}",
                          {"by": actor.admin.email, "admin": admin.email, "before": before, "after": _snapshot(admin)})
    await db.commit()
    return admin


async def list_sessions(db: AsyncSession, actor: PlatformPrincipal) -> List[dict]:
    stmt = select(PlatformSession).where(PlatformSession.revoked_at.is_(None)).order_by(PlatformSession.created_at.desc())
    if actor.role != PlatformRole.SUPER_ADMIN:
        stmt = stmt.where(PlatformSession.admin_id == actor.admin.id)
    sessions = (await db.scalars(stmt)).all()
    return [
        {**{c: getattr(s, c) for c in ("id", "admin_id", "created_at", "last_seen_at", "expires_at", "revoked_at",
                                       "ip", "user_agent")},
         "current": s.id == actor.session.id}
        for s in sessions
    ]


async def revoke_session_by_id(db: AsyncSession, actor: PlatformPrincipal, session_id: uuid.UUID,
                               meta: RequestMeta) -> None:
    session = await db.get(PlatformSession, session_id)
    if session is None or (session.admin_id != actor.admin.id and actor.role != PlatformRole.SUPER_ADMIN):
        raise NotFound("Session not found")
    await revoke_session(db, session, "revoked_by_admin")
    await append_audit(db, action="session.revoke", admin_id=actor.admin.id, target_type="session",
                       target_id=session.id, after={"owner": str(session.admin_id)}, meta=meta)
    await db.commit()


async def list_audit(db: AsyncSession, limit: int, before_seq: Optional[int], action: Optional[str]) -> List[PlatformAuditLog]:
    stmt = select(PlatformAuditLog).order_by(PlatformAuditLog.seq.desc()).limit(limit)
    if before_seq is not None:
        stmt = stmt.where(PlatformAuditLog.seq < before_seq)
    if action:
        stmt = stmt.where(PlatformAuditLog.action == action)
    return list((await db.scalars(stmt)).all())
