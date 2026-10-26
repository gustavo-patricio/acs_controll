"""Typed application configuration loaded from the environment."""

from functools import lru_cache
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field, PostgresDsn, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings shared by backend processes."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="ACS_",
        case_sensitive=False,
        extra="ignore",
    )

    environment: Literal["development", "test", "staging", "production"] = "development"
    database_url: PostgresDsn
    database_echo: bool = False
    database_connect_timeout: int = Field(default=5, ge=1)
    database_pool_size: int = Field(default=5, ge=1)
    database_max_overflow: int = Field(default=10, ge=0)
    cwmp_allow_shared_credentials: bool = False
    enable_test_cpe_accounts: bool = False
    api_allow_anonymous_inventory: bool = False
    cwmp_capture_cpe_id: UUID | None = None
    cwmp_capture_output: Path | None = None
    cwmp_capture_seconds: int = Field(default=300, ge=1, le=600)

    @model_validator(mode="after")
    def validate_capture(self) -> "Settings":
        if (self.cwmp_capture_cpe_id is None) != (self.cwmp_capture_output is None):
            raise ValueError("Capture requires both CPE ID and output path")
        if self.cwmp_capture_output is not None:
            if self.environment not in ("development", "test") or self.database_echo:
                raise ValueError("Capture requires development/test with SQL echo off")
            if not self.cwmp_capture_output.is_absolute():
                raise ValueError("Capture output path must be absolute")
        return self


@lru_cache
def get_settings() -> Settings:
    """Return one validated settings instance per process."""

    return Settings()  # pyright: ignore[reportCallIssue]
