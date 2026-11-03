from decimal import Decimal
from pathlib import Path
from typing import Annotated, List, Literal
from zoneinfo import ZoneInfo

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

SERVICE_DIR = Path(__file__).resolve().parents[2]

_DEV_SECRET_KEY = "dev-secret-key-change-in-production-min-32-chars"
_DEV_PIN_PEPPER = "dev-pin-pepper-change-in-production-min-32-chars"
_DEV_PLATFORM_SECRET = "dev-platform-secret-change-in-production-min-32-chars"
_DEV_PLATFORM_ENC = "dev-platform-enc-key-change-in-production-min-32-chars"


class Settings(BaseSettings):
    PROJECT_NAME: str = "Restaurant ERP API"
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: Literal["development", "test", "staging", "production"] = "development"
    DEBUG: bool = False
    DB_ECHO: bool = False

    # Security
    SECRET_KEY: str = _DEV_SECRET_KEY
    PIN_PEPPER: str = _DEV_PIN_PEPPER
    ALGORITHM: str = "HS256"
    JWT_ISSUER: str = "restaurant-erp"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 8  # 8 hours for shift workers
    LOGIN_MAX_ATTEMPTS: int = 5
    LOGIN_LOCKOUT_SECONDS: int = 300

    # Platform operator console (separate trust boundary from tenant staff auth)
    PLATFORM_SECRET_KEY: str = _DEV_PLATFORM_SECRET  # signs platform tokens; must differ from SECRET_KEY
    PLATFORM_ENCRYPTION_KEY: str = _DEV_PLATFORM_ENC  # encrypts TOTP secrets at rest
    PLATFORM_ENCRYPTION_KEYS_OLD: Annotated[List[str], NoDecode] = []  # previous keys still accepted while rotating
    PLATFORM_JWT_ISSUER: str = "restaurant-erp-platform"
    PLATFORM_ACCESS_TOKEN_MINUTES: int = 10
    PLATFORM_SESSION_IDLE_MINUTES: int = 15
    PLATFORM_SESSION_ABSOLUTE_HOURS: int = 8
    PLATFORM_STEP_UP_MINUTES: int = 5
    PLATFORM_COOKIE_SECURE: bool = True
    PLATFORM_MAX_BODY_BYTES: int = 256 * 1024  # platform requests are small JSON; anything bigger is refused with 413
    PLATFORM_IP_ALLOWLIST: Annotated[List[str], NoDecode] = []  # CIDRs; empty = no network restriction

    # Security alerts and four-eyes approval
    PLATFORM_ALERT_WEBHOOK_URL: str = ""  # optional; receives a signed JSON POST for every alert (https required in production)
    PLATFORM_ALERT_WEBHOOK_SECRET: str = ""  # HMAC key for the X-Alert-Signature header
    PLATFORM_FOUR_EYES: bool = True  # dangerous actions need a second super admin (when one exists)
    PLATFORM_APPROVAL_TTL_HOURS: int = 24
    # Optional role the API switches to after connecting (SET ROLE). Use a non-superuser role so row-level security applies.
    DB_SET_ROLE: str = ""

    # Subscriptions
    SUBSCRIPTION_GRACE_DAYS: int = 7  # past-due subscribers keep full access this long
    TRIAL_DAYS: int = 14
    ENTITLEMENT_CACHE_SECONDS: int = 60
    BILLING_INVOICE_LEAD_DAYS: int = 14  # renewal invoice is issued this many days before the period ends
    BILLING_DUE_DAYS: int = 7  # due date for activation / plan-change invoices
    BILLING_SWEEP_INTERVAL_SECONDS: int = 3600  # `run-billing --loop` cadence

    # Business rules
    DEFAULT_TIMEZONE: str = "Asia/Kolkata"
    DISCOUNT_APPROVAL_THRESHOLD_PERCENT: Decimal = Decimal("10")
    CASH_VARIANCE_ALERT_THRESHOLD: Decimal = Decimal("100.00")

    # PostgreSQL Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/restaurant_erp"
    DB_NULL_POOL: bool = False  # open a fresh connection per session (tests, serverless)

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # CORS
    ALLOWED_ORIGINS: Annotated[List[str], NoDecode] = [
        "http://localhost:3000",
        "http://localhost:8081",
        "http://localhost:19000",
        "http://localhost:19006",
    ]

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def assemble_cors_origins(cls, v: object) -> List[str]:
        if isinstance(v, str):
            return [i.strip() for i in v.split(",") if i.strip()]
        if isinstance(v, list):
            return [str(i) for i in v]
        raise ValueError(v)

    @field_validator("PLATFORM_ENCRYPTION_KEYS_OLD", mode="before")
    @classmethod
    def assemble_old_keys(cls, v: object) -> List[str]:
        if isinstance(v, str):
            return [i.strip() for i in v.split(",") if i.strip()]
        if isinstance(v, list):
            return [str(i) for i in v]
        raise ValueError(v)

    @field_validator("PLATFORM_IP_ALLOWLIST", mode="before")
    @classmethod
    def assemble_allowlist(cls, v: object) -> List[str]:
        if isinstance(v, str):
            return [i.strip() for i in v.split(",") if i.strip()]
        if isinstance(v, list):
            return [str(i) for i in v]
        raise ValueError(v)

    @field_validator("PLATFORM_IP_ALLOWLIST")
    @classmethod
    def validate_allowlist(cls, v: List[str]) -> List[str]:
        import ipaddress
        for cidr in v:
            ipaddress.ip_network(cidr, strict=False)
        return v

    @field_validator("DEFAULT_TIMEZONE")
    @classmethod
    def validate_timezone(cls, v: str) -> str:
        ZoneInfo(v)
        return v

    @model_validator(mode="after")
    def enforce_production_safety(self) -> "Settings":
        if self.ENVIRONMENT in ("development", "test"):
            return self
        for name, default in (("SECRET_KEY", _DEV_SECRET_KEY), ("PIN_PEPPER", _DEV_PIN_PEPPER)):
            value = getattr(self, name)
            if value == default or len(value) < 32:
                raise ValueError(f"{name} must be set to a unique value of at least 32 characters")
        for name, default in (("PLATFORM_SECRET_KEY", _DEV_PLATFORM_SECRET), ("PLATFORM_ENCRYPTION_KEY", _DEV_PLATFORM_ENC)):
            value = getattr(self, name)
            if value == default or len(value) < 32:
                raise ValueError(f"{name} must be set to a unique value of at least 32 characters")
        if len({self.SECRET_KEY, self.PLATFORM_SECRET_KEY, self.PLATFORM_ENCRYPTION_KEY, self.PIN_PEPPER}) < 4:
            raise ValueError("SECRET_KEY, PLATFORM_SECRET_KEY, PLATFORM_ENCRYPTION_KEY and PIN_PEPPER must all differ")
        if self.PLATFORM_ALERT_WEBHOOK_URL and not self.PLATFORM_ALERT_WEBHOOK_URL.startswith("https://"):
            raise ValueError("PLATFORM_ALERT_WEBHOOK_URL must use https outside development/test")
        if any(len(k) < 32 for k in self.PLATFORM_ENCRYPTION_KEYS_OLD) or self.PLATFORM_ENCRYPTION_KEY in self.PLATFORM_ENCRYPTION_KEYS_OLD:
            raise ValueError("PLATFORM_ENCRYPTION_KEYS_OLD must hold other keys of at least 32 characters")
        if not self.PLATFORM_COOKIE_SECURE:
            raise ValueError("PLATFORM_COOKIE_SECURE must be true outside development/test")
        if self.DEBUG:
            raise ValueError("DEBUG must be false outside development/test")
        return self

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    model_config = SettingsConfigDict(
        env_file=SERVICE_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


settings = Settings()
