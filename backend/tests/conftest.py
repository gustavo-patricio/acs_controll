"""Synthetic protocol fixtures shared by unit and integration tests."""

from collections.abc import Generator
from pathlib import Path

import pytest

from acs.settings import get_settings


@pytest.fixture(autouse=True)
def require_inventory_auth_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[None]:
    # A developer's local opt-out must not silently disable authentication tests.
    monkeypatch.setenv("ACS_API_ALLOW_ANONYMOUS_INVENTORY", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def inform_xml() -> bytes:
    return (Path(__file__).parent / "fixtures/cwmp/inform.xml").read_bytes()
