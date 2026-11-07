"""Explicit repository configuration, independent of the current working directory."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

REPO_ROOT = Path(__file__).resolve().parents[4]
SERVER_ROOT = REPO_ROOT / "apps" / "server"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_env: Literal["development", "test", "production"] = "development"
    public_app_url: str = "http://localhost:3000"
    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://hackjunction:local-dev-only@127.0.0.1:5432/hackjunction"
    )
    test_database_url: SecretStr = SecretStr(
        "postgresql+psycopg://hackjunction:local-dev-only@127.0.0.1:5432/hackjunction_test"
    )
    database_connect_timeout_seconds: int = Field(default=3, ge=1, le=30)
    mimo_api_key: SecretStr | None = None
    mimo_base_url: str = "https://api.xiaomimimo.com/v1"
    mimo_model: str = "mimo-v2.6-pro"
    mimo_mode: Literal["live", "fake"] = "live"
    publish_mode: Literal["simulated", "sandbox", "production"] = "simulated"
    request_timeout_seconds: float = Field(default=60, gt=0, le=600)
    storage_backend: Literal["local"] = "local"
    asset_dir: Path = REPO_ROOT / "var" / "assets"
    worker_heartbeat_seconds: float = Field(default=15, ge=1, le=300)
    order_token_budget: int = Field(default=100000, ge=1)
    order_candidate_budget: int = Field(default=12, ge=1)
    session_secret: SecretStr | None = None
    admin_password_hash: SecretStr | None = None
    seller_session_hours: int = Field(default=168, ge=1, le=720)
    owner_session_hours: int = Field(default=12, ge=1, le=168)
    order_link_days: int = Field(default=30, ge=1, le=365)
    mail_mode: Literal["smtp", "test"] = "smtp"
    smtp_host: str = "smtp.qq.com"
    smtp_port: int = Field(default=465, ge=1, le=65535)
    smtp_username: str = ""
    smtp_password: SecretStr | None = None
    smtp_from_email: str = ""
    smtp_timeout_seconds: int = Field(default=12, ge=1, le=30)
    mail_test_dir: Path | None = None

    @field_validator(
        "mimo_api_key", "session_secret", "admin_password_hash", "smtp_password", mode="before"
    )
    @classmethod
    def blank_secret_is_unconfigured(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("database_url", "test_database_url")
    @classmethod
    def require_async_postgres(cls, value: SecretStr) -> SecretStr:
        try:
            url = make_url(value.get_secret_value())
        except Exception:
            raise ValueError("Use a valid PostgreSQL SQLAlchemy connection URL") from None
        if url.drivername != "postgresql+psycopg" or not url.database:
            raise ValueError("Use postgresql+psycopg and specify a database")
        return value

    @field_validator("asset_dir", mode="after")
    @classmethod
    def resolve_asset_directory(cls, value: Path) -> Path:
        return value if value.is_absolute() else REPO_ROOT / value

    @model_validator(mode="after")
    def fake_model_requires_test_environment(self) -> "Settings":
        if self.mimo_mode == "fake" and self.app_env != "test":
            raise ValueError("MIMO_MODE=fake is allowed only with APP_ENV=test")
        if self.mail_mode == "test" and self.app_env != "test":
            raise ValueError("MAIL_MODE=test is allowed only with APP_ENV=test")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
