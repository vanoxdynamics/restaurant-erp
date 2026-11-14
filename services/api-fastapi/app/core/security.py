import hashlib
import hmac
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Union

import jwt
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from pwdlib.hashers.bcrypt import BcryptHasher

from app.core.config import settings

# Argon2 for new hashes; bcrypt verifier keeps legacy hashes readable.
password_hash = PasswordHash((Argon2Hasher(), BcryptHasher()))

# Used to equalize timing when a user lookup misses.
DUMMY_HASH = password_hash.hash("timing-equalizer")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return password_hash.verify(plain_password, hashed_password)
    except Exception:
        return False


def verify_and_update_password(plain_password: str, hashed_password: str) -> tuple[bool, Optional[str]]:
    """Verify a password and return a replacement hash when the stored one uses a legacy scheme."""
    try:
        return password_hash.verify_and_update(plain_password, hashed_password)
    except Exception:
        return False, None


def get_password_hash(password: str) -> str:
    return password_hash.hash(password)


def verify_pin(plain_pin: str, hashed_pin: str) -> bool:
    return verify_password(plain_pin, hashed_pin)


def get_pin_hash(pin: str) -> str:
    return password_hash.hash(pin)


def compute_pin_lookup(branch_id: uuid.UUID, pin: str) -> str:
    """Deterministic keyed digest of a PIN, scoped to a branch, for O(1) PIN login lookup."""
    message = f"{branch_id}:{pin}".encode()
    return hmac.new(settings.PIN_PEPPER.encode(), message, hashlib.sha256).hexdigest()


def create_access_token(
    subject: Union[str, Any],
    claims: Optional[Dict[str, Any]] = None,
    expires_delta: Optional[timedelta] = None,
) -> str:
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))

    to_encode: Dict[str, Any] = {
        "sub": str(subject),
        "iat": now,
        "exp": expire,
        "jti": uuid.uuid4().hex,
        "iss": settings.JWT_ISSUER,
    }
    if claims:
        to_encode.update(claims)

    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_access_token(token: str) -> Optional[Dict[str, Any]]:
    try:
        return jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            issuer=settings.JWT_ISSUER,
            options={"require": ["exp", "sub", "iat"]},
        )
    except jwt.PyJWTError:
        return None


# A lease lets a device bill offline for a while (offline billing). It is never a login: see resolve_token.
OFFLINE_LEASE_TYPE = "offline_lease"


def decode_offline_lease(token: str) -> Optional[Dict[str, Any]]:
    """The claims of a valid, unexpired offline lease; None for anything else (a device or staff token included)."""
    payload = decode_access_token(token)
    if payload is None or payload.get("typ") != OFFLINE_LEASE_TYPE:
        return None
    return payload
