"""Typed application configuration loaded from the environment."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn
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


@lru_cache
def get_settings() -> Settings:
    """Return one validated settings instance per process."""

    return Settings()  # pyright: ignore[reportCallIssue]
