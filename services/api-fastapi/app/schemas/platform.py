import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, model_validator

from app.core.enums import PlatformRole


class LoginRequest(BaseModel):
    email: EmailStr
    password: SecretStr = Field(min_length=1, max_length=128)


class MfaChallenge(BaseModel):
    mfa_token: str
    expires_in: int


class TwoFactorRequest(BaseModel):
    mfa_token: str
    code: Optional[str] = Field(default=None, max_length=10)
    recovery_code: Optional[str] = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def exactly_one(self) -> "TwoFactorRequest":
        if (self.code is None) == (self.recovery_code is None):
            raise ValueError("provide either code or recovery_code")
        return self


class StepUpRequest(BaseModel):
    password: SecretStr = Field(min_length=1, max_length=128)
    code: str = Field(max_length=10)


class PlatformAdminRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str
    role: PlatformRole
    is_active: bool
    last_login_at: Optional[datetime]
    created_at: datetime


class PlatformTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    csrf_token: str
    admin: PlatformAdminRead


class PlatformAdminCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=100)
    role: PlatformRole
    password: SecretStr = Field(min_length=1, max_length=128)


class PlatformAdminCreated(BaseModel):
    """Secrets are shown once, here. Hand them to the new admin over a secure channel."""
    admin: PlatformAdminRead
    totp_secret: str
    provisioning_uri: str
    recovery_codes: List[str]


class PlatformAdminUpdate(BaseModel):
    full_name: Optional[str] = Field(default=None, min_length=2, max_length=100)
    role: Optional[PlatformRole] = None
    is_active: Optional[bool] = None


class PlatformSessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    admin_id: uuid.UUID
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    revoked_at: Optional[datetime]
    ip: Optional[str]
    user_agent: Optional[str]
    current: bool = False


class PlatformAuditRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    seq: int
    created_at: datetime
    admin_id: Optional[uuid.UUID]
    action: str
    target_type: Optional[str]
    target_id: Optional[str]
    before: Optional[Dict[str, Any]]
    after: Optional[Dict[str, Any]]
    reason: Optional[str]
    ip: Optional[str]
    request_id: Optional[str]
    hash: str


class AuditVerifyResult(BaseModel):
    ok: bool
    checked: int
    head_hash: str
    first_bad_seq: Optional[int]
