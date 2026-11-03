"""
Security primitives for the platform operator console.

Everything here is deliberately separate from tenant staff auth: its own signing key, token audience and
issuer, and its own secret-encryption key, so a leaked tenant key or token never unlocks the console.
"""

import base64
import hashlib
import hmac
import secrets
import struct
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote

import jwt
from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import settings

PLATFORM_AUDIENCE = "platform"
TOKEN_ACCESS = "access"
TOKEN_MFA = "mfa"

TOTP_STEP_SECONDS = 30
TOTP_DIGITS = 6
TOTP_WINDOW = 1  # accept the previous and next 30s step to tolerate clock skew
MFA_TOKEN_TTL_SECONDS = 300
MIN_PASSWORD_LENGTH = 14


# --- password policy ---------------------------------------------------------------


def validate_admin_password(password: str, email: str = "") -> None:
    """Raise ValueError describing the first rule the password breaks."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > 128:
        raise ValueError("password must be at most 128 characters")
    classes = sum([
        any(c.islower() for c in password),
        any(c.isupper() for c in password),
        any(c.isdigit() for c in password),
        any(not c.isalnum() for c in password),
    ])
    if classes < 3:
        raise ValueError("password must mix at least three of: lowercase, uppercase, digits, symbols")
    local = email.split("@", 1)[0].lower()
    if len(local) >= 4 and local in password.lower():
        raise ValueError("password must not contain your email name")
    if len(set(password)) < 6:
        raise ValueError("password is too repetitive")


# --- TOTP (RFC 6238, SHA-1, 6 digits, 30s) --------------------------------------------


def generate_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _hotp(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** TOTP_DIGITS)
    return str(value).zfill(TOTP_DIGITS)


def totp_code(secret: str, at: Optional[float] = None) -> str:
    return _hotp(secret, int((at if at is not None else time.time()) // TOTP_STEP_SECONDS))


def verify_totp(secret: str, code: str, last_step: int = 0, at: Optional[float] = None) -> Optional[int]:
    """Return the matched time step, or None. Steps at or before `last_step` are rejected (no code replay)."""
    code = code.strip().replace(" ", "")
    if len(code) != TOTP_DIGITS or not code.isdigit():
        return None
    now_step = int((at if at is not None else time.time()) // TOTP_STEP_SECONDS)
    matched: Optional[int] = None
    for step in range(now_step - TOTP_WINDOW, now_step + TOTP_WINDOW + 1):
        # Compare every candidate so timing does not reveal which step matched.
        if hmac.compare_digest(_hotp(secret, step), code) and step > last_step:
            matched = step
    return matched


def provisioning_uri(email: str, secret: str, issuer: str = "Restaurant ERP Platform") -> str:
    return f"otpauth://totp/{quote(issuer)}:{quote(email)}?secret={secret}&issuer={quote(issuer)}&digits=6&period=30"


# --- secret encryption at rest ---------------------------------------------------------


def _fernet_for(secret: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


def _fernet() -> MultiFernet:
    """Encrypts with the current key; decrypts with the current key or any key still listed as old (rotation window)."""
    return MultiFernet([_fernet_for(k) for k in (settings.PLATFORM_ENCRYPTION_KEY, *settings.PLATFORM_ENCRYPTION_KEYS_OLD)])


def reencrypt_secret(token: str) -> str:
    """Re-encrypt a stored secret under the current key (used by `rotate-encryption-key`)."""
    try:
        return _fernet().rotate(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("stored secret cannot be decrypted with any configured key") from exc


def encrypt_secret(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_secret(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("stored secret cannot be decrypted (wrong PLATFORM_ENCRYPTION_KEY?)") from exc


# --- recovery codes / refresh tokens ------------------------------------------------------

_RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def generate_recovery_codes(count: int = 10) -> list[str]:
    def one() -> str:
        raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(10))
        return f"{raw[:5]}-{raw[5:]}"
    return [one() for _ in range(count)]


def _recovery_hash(code: str, key: str) -> str:
    normalized = code.strip().lower().replace(" ", "")
    return hmac.new(key.encode(), normalized.encode(), hashlib.sha256).hexdigest()


def hash_recovery_code(code: str) -> str:
    return _recovery_hash(code, settings.PLATFORM_ENCRYPTION_KEY)


def recovery_code_hashes(code: str) -> list[str]:
    """Hashes to look up: under the current key and any old key, so codes issued before a rotation keep working."""
    return [_recovery_hash(code, k) for k in (settings.PLATFORM_ENCRYPTION_KEY, *settings.PLATFORM_ENCRYPTION_KEYS_OLD)]


def new_refresh_token() -> Tuple[str, str]:
    """Return (token for the cookie, hash to store). The raw token is never persisted."""
    token = secrets.token_urlsafe(48)
    return token, hash_refresh_token(token)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


# --- JWTs ------------------------------------------------------------------------------------


def create_platform_token(subject: uuid.UUID | str, token_type: str, ttl: timedelta,
                          claims: Optional[Dict[str, Any]] = None) -> str:
    now = datetime.now(timezone.utc)
    payload: Dict[str, Any] = {
        "sub": str(subject), "typ": token_type, "aud": PLATFORM_AUDIENCE, "iss": settings.PLATFORM_JWT_ISSUER,
        "iat": now, "exp": now + ttl, "jti": uuid.uuid4().hex,
    }
    if claims:
        payload.update(claims)
    return jwt.encode(payload, settings.PLATFORM_SECRET_KEY, algorithm="HS256")


def decode_platform_token(token: str, expected_type: str) -> Optional[Dict[str, Any]]:
    """Validate signature, audience, issuer, expiry and token type. Tenant tokens never validate here."""
    try:
        payload = jwt.decode(
            token, settings.PLATFORM_SECRET_KEY, algorithms=["HS256"], audience=PLATFORM_AUDIENCE,
            issuer=settings.PLATFORM_JWT_ISSUER, options={"require": ["exp", "sub", "iat", "aud", "iss"]},
        )
    except jwt.PyJWTError:
        return None
    return payload if payload.get("typ") == expected_type else None
