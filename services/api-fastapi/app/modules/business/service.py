"""
The business's own branding: the name and logo printed on its bills. Set by the business (owner or manager), not the platform.
"""

import base64
import binascii
from typing import Optional, Tuple

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.core.clock import utcnow
from app.core.errors import NotFound, ValidationFailed
from app.core.events import commit
from app.models.organization import Organization

LOGO_MAX_BYTES = 500 * 1024
# Only image formats every printer path can show, identified by their first bytes (not by what the client claims).
_SIGNATURES = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"))


def logo_data_url(org: Organization) -> Optional[str]:
    if not org.logo or not org.logo_content_type:
        return None
    return f"data:{org.logo_content_type};base64,{base64.b64encode(org.logo).decode()}"


def bill_name(org: Organization) -> str:
    return org.display_name or org.name


async def load_with_logo(db: AsyncSession, org_id) -> Organization:
    org = await db.scalar(select(Organization).where(Organization.id == org_id).options(undefer(Organization.logo))
                          .execution_options(populate_existing=True))
    if org is None:
        raise NotFound("Organization not found")
    return org


def profile(org: Organization) -> dict:
    return {"name": bill_name(org), "display_name": org.display_name, "registered_name": org.name,
            "logo_data_url": logo_data_url(org), "logo_updated_at": org.logo_updated_at}


async def update_display_name(db: AsyncSession, org_id, display_name: Optional[str]) -> dict:
    await db.execute(update(Organization).where(Organization.id == org_id).values(display_name=display_name))
    await commit(db)
    return profile(await load_with_logo(db, org_id))


def check_logo(image_base64: str) -> Tuple[bytes, str]:
    raw = image_base64.split(",", 1)[1] if image_base64.startswith("data:") else image_base64
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValidationFailed("The logo is not a valid image file", code="invalid_logo") from exc
    if len(data) > LOGO_MAX_BYTES:
        raise ValidationFailed("The logo is too large; use an image under 500 KB", code="logo_too_large",
                               details={"max_bytes": LOGO_MAX_BYTES})
    content_type = next((ct for sig, ct in _SIGNATURES if data.startswith(sig)), None)
    if content_type is None:
        raise ValidationFailed("The logo must be a PNG or JPEG image", code="invalid_logo")
    return data, content_type


async def set_logo(db: AsyncSession, org_id, image_base64: Optional[str]) -> dict:
    values = {"logo": None, "logo_content_type": None, "logo_updated_at": utcnow()}
    if image_base64 is not None:
        data, content_type = check_logo(image_base64)
        values.update(logo=data, logo_content_type=content_type)
    await db.execute(update(Organization).where(Organization.id == org_id).values(**values))
    await commit(db)
    return profile(await load_with_logo(db, org_id))
