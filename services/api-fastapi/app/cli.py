"""
Operational commands.

    python -m app.cli create-platform-admin --email you@example.com --full-name "Your Name"

Creates a platform operator (super admin). This is the only way to make the first one: no endpoint can.
It prints the TOTP secret and recovery codes once; store them in your password manager.

    python -m app.cli verify-audit                         # cron this daily; exit status 1 means the audit log was altered
    python -m app.cli dispatch-alerts                      # retry undelivered alert webhooks
    python -m app.cli reset-platform-admin-2fa --email ..  # break-glass for a lost authenticator

    python -m app.cli run-billing [--loop]

Runs the subscription renewal sweep (renewal invoices, reminders, past-due and grace handling) once or on a schedule.

    python -m app.cli create-owner --username owner --full-name "Owner" --branch-code BLR1 --branch-name "Indiranagar" \
        --org-slug acme --org-name "Acme Restaurants"

Creates the organization and branch if they do not exist and the first owner account (prompts for password and PIN).
"""

import argparse
import asyncio
import getpass
import re
import sys

from sqlalchemy import select

from app.core.database import AsyncSessionLocal, dispose_engine
from app.core.entitlements import ensure_legacy_subscription
from app.core.enums import Role
from app.core.security import compute_pin_lookup, get_password_hash, get_pin_hash
from app.models.floor import Branch
from app.models.organization import Organization
from app.models.staff import StaffUser


async def create_owner(username: str, full_name: str, branch_code: str, branch_name: str,
                       password: str, pin: str, org_slug: str, org_name: str) -> None:
    async with AsyncSessionLocal() as db:
        org = await db.scalar(select(Organization).where(Organization.slug == org_slug))
        if org is None:
            org = Organization(slug=org_slug, name=org_name)
            db.add(org)
            await db.flush()
        await ensure_legacy_subscription(db, org)
        branch = await db.scalar(select(Branch).where(Branch.code == branch_code))
        if branch is not None and branch.organization_id != org.id:
            raise SystemExit(f"Branch code {branch_code!r} belongs to another organization")
        if branch is None:
            branch = Branch(code=branch_code, name=branch_name, organization_id=org.id)
            db.add(branch)
            await db.flush()
        if await db.scalar(select(StaffUser).where(StaffUser.username == username)):
            raise SystemExit(f"User {username!r} already exists")
        db.add(StaffUser(
            organization_id=org.id,
            branch_id=branch.id,
            username=username,
            full_name=full_name,
            role=Role.OWNER.value,
            hashed_password=get_password_hash(password),
            hashed_pin=get_pin_hash(pin),
            pin_lookup=compute_pin_lookup(branch.id, pin),
        ))
        await db.commit()
    await dispose_engine()


async def create_platform_admin(email: str, full_name: str, password: str) -> tuple[str, str, list[str]]:
    from app.core.enums import PlatformRole
    from app.models.platform import PlatformAdmin
    from app.modules.platform.admin_service import create_admin_record
    from app.modules.platform.audit import append_audit

    async with AsyncSessionLocal() as db:
        if await db.scalar(select(PlatformAdmin.id).where(PlatformAdmin.email == email)):
            raise SystemExit(f"Platform admin {email!r} already exists")
        admin, secret, uri, codes = await create_admin_record(
            db, email=email, full_name=full_name, role=PlatformRole.SUPER_ADMIN, password=password)
        await append_audit(db, action="admin.bootstrap", admin_id=admin.id, target_type="platform_admin",
                           target_id=admin.id, after={"email": admin.email, "role": admin.role})
        await db.commit()
    await dispose_engine()
    return secret, uri, codes


async def verify_audit() -> dict:
    """Check the platform audit chain; raises a critical alert when it is broken. Exit status 1 means tampering."""
    from app.core.enums import AlertSeverity
    from app.modules.platform.alerts import raise_alert
    from app.modules.platform.audit import verify_chain

    async with AsyncSessionLocal() as db:
        result = await verify_chain(db)
        if not result["ok"]:
            await raise_alert(db, "audit.chain_broken", AlertSeverity.CRITICAL,
                              f"The platform audit log was altered: the chain breaks at entry #{result['first_bad_seq']}", result)
            await db.commit()
    await dispose_engine()
    return result


async def rotate_encryption_key() -> int:
    """Re-encrypt every stored authenticator secret under PLATFORM_ENCRYPTION_KEY. Returns how many were rewritten."""
    from app.core.platform_security import reencrypt_secret
    from app.models.platform import PlatformAdmin

    count = 0
    async with AsyncSessionLocal() as db:
        for admin in (await db.scalars(select(PlatformAdmin).with_for_update())).all():
            admin.totp_secret_enc = reencrypt_secret(admin.totp_secret_enc)
            count += 1
        await db.commit()
    await dispose_engine()
    return count


async def reset_platform_admin_2fa(email: str) -> tuple[str, str, list[str]]:
    """Break-glass: new authenticator secret and recovery codes, all sessions ended. For a lost phone or lost codes."""
    from app.core.enums import AlertSeverity
    from app.models.platform import PlatformAdmin
    from app.modules.platform.admin_service import rotate_second_factor
    from app.modules.platform.alerts import raise_alert
    from app.modules.platform.audit import append_audit

    async with AsyncSessionLocal() as db:
        admin = await db.scalar(select(PlatformAdmin).where(PlatformAdmin.email == email))
        if admin is None:
            raise SystemExit(f"No platform admin with email {email!r}")
        secret, uri, codes = await rotate_second_factor(db, admin)
        await append_audit(db, action="admin.2fa_reset_cli", admin_id=admin.id, target_type="platform_admin", target_id=admin.id,
                           after={"email": admin.email, "via": "command line"})
        await raise_alert(db, "admin.2fa_reset", AlertSeverity.CRITICAL,
                          f"The second factor of {admin.email} was reset from the command line (all their sessions ended)", {"admin": admin.email})
        await db.commit()
    await dispose_engine()
    return secret, uri, codes


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    owner = sub.add_parser("create-owner", help="Create the first owner account")
    owner.add_argument("--username", required=True)
    owner.add_argument("--full-name", required=True)
    owner.add_argument("--branch-code", required=True)
    owner.add_argument("--branch-name", default="Main Branch")
    owner.add_argument("--org-slug", default="default", help="lowercase letters, digits, dashes")
    owner.add_argument("--org-name", default="Default Organization")
    sub.add_parser("verify-audit", help="Verify the platform audit-log hash chain (exit 1 if tampered)")
    sub.add_parser("dispatch-alerts", help="Retry delivery of undelivered security alerts to the webhook")
    sub.add_parser("rotate-encryption-key", help="Re-encrypt stored authenticator secrets under the current PLATFORM_ENCRYPTION_KEY")
    reset = sub.add_parser("reset-platform-admin-2fa", help="Break-glass: re-enrol a platform admin's authenticator")
    reset.add_argument("--email", required=True)
    billing = sub.add_parser("run-billing", help="Run the subscription renewal sweep")
    billing.add_argument("--loop", action="store_true", help="keep running on BILLING_SWEEP_INTERVAL_SECONDS")
    pa = sub.add_parser("create-platform-admin", help="Create a platform operator (super admin)")
    pa.add_argument("--email", required=True)
    pa.add_argument("--full-name", required=True)
    args = parser.parse_args(argv)

    if args.command == "verify-audit":
        result = asyncio.run(verify_audit())
        if not result["ok"]:
            print(f"AUDIT CHAIN BROKEN at entry #{result['first_bad_seq']} after {result['checked']} good entries", file=sys.stderr)
            raise SystemExit(1)
        print(f"ok: {result['checked']} entries verified; head hash {result['head_hash']}")
        return

    if args.command == "dispatch-alerts":
        from app.modules.platform.alerts import dispatch_pending_alerts

        async def _dispatch() -> dict:
            try:
                return await dispatch_pending_alerts()
            finally:
                await dispose_engine()

        print(asyncio.run(_dispatch()))
        return

    if args.command == "rotate-encryption-key":
        print(f"re-encrypted {asyncio.run(rotate_encryption_key())} authenticator secret(s) under the current key")
        return

    if args.command == "reset-platform-admin-2fa":
        email = args.email.strip().lower()
        if input(f"Type the email again to confirm resetting the second factor of {email}: ").strip().lower() != email:
            raise SystemExit("Not confirmed; nothing changed")
        secret, uri, codes = asyncio.run(reset_platform_admin_2fa(email))
        print(f"Second factor of {email} reset; all their sessions were ended.", file=sys.stderr)
        print(f"\nNew authenticator key: {secret}\n  uri: {uri}", file=sys.stderr)
        print("\nNew recovery codes (the old ones no longer work):", file=sys.stderr)
        for code in codes:
            print(f"  {code}", file=sys.stderr)
        return

    if args.command == "run-billing":
        from app.core.database import dispose_engine as _dispose
        from app.core.logging import configure_logging
        from app.core.redis import close_redis_pool
        from app.worker.billing import run_forever, run_once

        configure_logging(False)

        async def _go() -> None:
            try:
                await (run_forever() if args.loop else run_once())
            finally:
                await close_redis_pool()
                await _dispose()

        asyncio.run(_go())
        return

    if args.command == "create-platform-admin":
        from pydantic import TypeAdapter, EmailStr
        from app.core.platform_security import validate_admin_password

        email = TypeAdapter(EmailStr).validate_python(args.email).lower()
        password = getpass.getpass("Password (14+ chars, 3 of: lower/upper/digit/symbol): ")
        if password != getpass.getpass("Repeat password: "):
            raise SystemExit("passwords do not match")
        try:
            validate_admin_password(password, email)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        secret, uri, codes = asyncio.run(create_platform_admin(email, args.full_name, password))
        print(f"Platform admin {email} created.", file=sys.stderr)
        print(f"\nAdd to your authenticator app:\n  secret: {secret}\n  uri:    {uri}", file=sys.stderr)
        print("\nRecovery codes (each works once; shown only now):", file=sys.stderr)
        for code in codes:
            print(f"  {code}", file=sys.stderr)
        return

    if args.command == "create-owner":
        code = args.branch_code.upper()
        if not re.fullmatch(r"[A-Z0-9]{2,6}", code):
            raise SystemExit("branch code must be 2-6 letters/digits")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,58}[a-z0-9]", args.org_slug):
            raise SystemExit("org slug must be 3-60 lowercase letters, digits or dashes")
        password = getpass.getpass("Password (min 10 chars): ")
        if len(password) < 10:
            raise SystemExit("password too short")
        pin = getpass.getpass("PIN (4-6 digits): ")
        if not re.fullmatch(r"\d{4,6}", pin):
            raise SystemExit("PIN must be 4-6 digits")
        asyncio.run(create_owner(args.username.lower(), args.full_name, code, args.branch_name, password, pin,
                                 args.org_slug, args.org_name))
        print(f"Owner {args.username!r} created in branch {code}.", file=sys.stderr)


if __name__ == "__main__":
    main()
