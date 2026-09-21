"""Synthetic protocol fixtures shared by unit and integration tests."""

from pathlib import Path

import pytest


@pytest.fixture
def inform_xml() -> bytes:
    return (Path(__file__).parent / "fixtures/cwmp/inform.xml").read_bytes()
