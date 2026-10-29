from decimal import Decimal
from pathlib import Path
from typing import Annotated, List, Literal
from zoneinfo import ZoneInfo

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

SERVICE_DIR = Path(__file__).resolve().parents[2]

_DEV_SECRET_KEY = "dev-secret-key-change-in-production-min-32-chars"
_DEV_PIN_PEPPER = "dev-pin-pepper-change-in-production-min-32-chars"


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
