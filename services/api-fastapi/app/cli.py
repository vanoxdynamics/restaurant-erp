"""
Operational commands.

    python -m app.cli create-owner --username owner --full-name "Owner" --branch-code BLR1 --branch-name "Indiranagar"

Creates the branch if it does not exist and the first owner account (prompts for password and PIN).
"""

import argparse
import asyncio
import getpass
import re
import sys

from sqlalchemy import select

from app.core.database import AsyncSessionLocal, dispose_engine
from app.core.enums import Role
from app.core.security import compute_pin_lookup, get_password_hash, get_pin_hash
from app.models.floor import Branch
from app.models.staff import StaffUser


async def create_owner(username: str, full_name: str, branch_code: str, branch_name: str,
                       password: str, pin: str) -> None:
    async with AsyncSessionLocal() as db:
        branch = await db.scalar(select(Branch).where(Branch.code == branch_code))
        if branch is None:
            branch = Branch(code=branch_code, name=branch_name)
            db.add(branch)
            await db.flush()
        if await db.scalar(select(StaffUser).where(StaffUser.username == username)):
            raise SystemExit(f"User {username!r} already exists")
        db.add(StaffUser(
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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    owner = sub.add_parser("create-owner", help="Create the first owner account")
    owner.add_argument("--username", required=True)
    owner.add_argument("--full-name", required=True)
    owner.add_argument("--branch-code", required=True)
    owner.add_argument("--branch-name", default="Main Branch")
    args = parser.parse_args(argv)

    if args.command == "create-owner":
        code = args.branch_code.upper()
        if not re.fullmatch(r"[A-Z0-9]{2,6}", code):
            raise SystemExit("branch code must be 2-6 letters/digits")
        password = getpass.getpass("Password (min 10 chars): ")
        if len(password) < 10:
            raise SystemExit("password too short")
        pin = getpass.getpass("PIN (4-6 digits): ")
        if not re.fullmatch(r"\d{4,6}", pin):
            raise SystemExit("PIN must be 4-6 digits")
        asyncio.run(create_owner(args.username.lower(), args.full_name, code, args.branch_name, password, pin))
        print(f"Owner {args.username!r} created in branch {code}.", file=sys.stderr)


if __name__ == "__main__":
    main()
