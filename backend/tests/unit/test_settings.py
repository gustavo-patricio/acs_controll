"""Tests for environment-based application settings."""

from pydantic import PostgresDsn

from acs.settings import Settings


def test_database_settings_are_validated() -> None:
    settings = Settings(
        database_url=PostgresDsn("postgresql+psycopg://acs:local@localhost:5432/acs"),
        database_connect_timeout=2,
        database_pool_size=3,
        database_max_overflow=4,
    )

    assert settings.database_url.scheme == "postgresql+psycopg"
    assert settings.database_connect_timeout == 2
    assert settings.database_pool_size == 3
    assert settings.database_max_overflow == 4
