"""
A local platform-console operator for preview/QA databases, so the console can be checked in the browser.

    python -m scripts.seed_platform_admin            # create it (once) and print a current authenticator code
    python -m scripts.seed_platform_admin --code     # just print a current code

Sign-in: preview-ops@example.com / preview-console-2026, then the 6-digit code. The authenticator key is kept in
PREVIEW_TOTP_FILE (default: .preview-platform-totp next to this repository's service folder, never committed), because the
console asks for a fresh code at every sign-in. Only for local preview databases — never run against production.
"""

import asyncio
import os
import sys
from pathlib import Path

from sqlalchemy import select

from app.cli import create_platform_admin
from app.core.database import AsyncSessionLocal, dispose_engine
from app.core.platform_security import totp_code
from app.models.platform import PlatformAdmin

EMAIL, PASSWORD, NAME = "preview-ops@example.com", "preview-console-2026", "Preview Operator"
KEY_FILE = Path(os.environ.get("PREVIEW_TOTP_FILE", Path(__file__).resolve().parents[1] / ".preview-platform-totp"))


async def ensure_operator() -> str | None:
    """Create the operator if it does not exist; returns its new authenticator key, or None when it already existed."""
    async with AsyncSessionLocal() as db:
        exists = await db.scalar(select(PlatformAdmin.id).where(PlatformAdmin.email == EMAIL))
    await dispose_engine()
    if exists is not None:
        return None
    secret, _, _ = await create_platform_admin(EMAIL, NAME, PASSWORD)
    return secret


def main(code_only: bool) -> None:
    if not code_only:
        secret = asyncio.run(ensure_operator())
        if secret is not None:
            KEY_FILE.write_text(secret, encoding="utf-8")
            print(f"Created {EMAIL}; authenticator key saved to {KEY_FILE}.")
    if not KEY_FILE.exists():
        raise SystemExit(f"No saved authenticator key at {KEY_FILE}; the operator was made elsewhere.")
    print(f"Current code: {totp_code(KEY_FILE.read_text(encoding='utf-8').strip())}")


if __name__ == "__main__":
    main("--code" in sys.argv[1:])
