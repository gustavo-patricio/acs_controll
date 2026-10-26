"""PostgreSQL provenance, ordering, atomicity and non-destructive migration."""

import asyncio
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text, update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from acs.cwmp.compatibility.profiles import ProfileInventoryNormalizer
from acs.cwmp.soap.inform import parse_inform
from acs.domain.cpe.inform import Event, Inform, Parameter
from acs.domain.events.session import CwmpSession, StorageUnavailable
from acs.infrastructure.database.cwmp_models import (
    Cpe,
    CpeInventoryField,
    CwmpEvent,
    CwmpSessionRow,
)
from acs.infrastructure.database.database import Database
from acs.infrastructure.database.inform_repository import PostgresInformRepository
from integration.test_cwmp_persistence import cwmp_database

__all__ = ["cwmp_database"]

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("ACS_RUN_INTEGRATION_TESTS") != "1",
        reason="PostgreSQL integration is opt-in",
    ),
]
NOW = datetime(2026, 10, 25, tzinfo=UTC)


@pytest.fixture
def huawei() -> Inform:
    return parse_inform(
        (
            Path(__file__).parents[1]
            / "fixtures/cwmp/huawei_eg8145x6_10_v5r022c00s364.xml"
        ).read_bytes()
    )


async def accept(
    database: Database,
    inform: Inform,
    at: datetime,
    *,
    token: str | None = None,
    continuing: bool = False,
    profile_version: str | None = None,
) -> CwmpSession:
    inventory = ProfileInventoryNormalizer().normalize(inform, at)
    if profile_version is not None:
        inventory = replace(inventory, profile_version=profile_version)
    return await PostgresInformRepository(database).accept(
        inform,
        token or uuid4().hex,
        at,
        at + timedelta(minutes=1),
        continuing,
        inventory=inventory,
    )


@pytest.mark.asyncio
async def test_valid_provenance_and_invalid_partial_recovery(
    cwmp_database: Database, huawei: Inform
) -> None:
    first = await accept(cwmp_database, huawei, NOW)
    async with cwmp_database.session() as db:
        rows = (await db.scalars(select(CpeInventoryField))).all()
        assert len(rows) == 3
        for row in rows:
            assert row.state == "valid" and row.invalid_reason is None
            assert row.value_received_at == row.observed_at == NOW
            assert (
                row.value_profile_id
                == row.profile_id
                == "huawei-eg8145x6-10-v5r022c00s364"
            )
            assert row.value_profile_version == row.profile_version == "1"
            assert row.source_parameter == row.value_source_parameter
        model = await db.get(CpeInventoryField, (first.cpe_id, "model"))
        assert (
            model is not None
            and model.value_source_parameter == "DeviceId.ProductClass"
        )
    # Last communication alone must not refresh any field.
    await accept(
        cwmp_database, replace(huawei, parameters=()), NOW + timedelta(seconds=10)
    )
    async with cwmp_database.session() as db:
        rows = (await db.scalars(select(CpeInventoryField))).all()
        assert all(row.observed_at == row.value_received_at == NOW for row in rows)
        cpe = await db.get(Cpe, first.cpe_id)
        assert cpe is not None and cpe.last_seen == NOW + timedelta(seconds=10)
    secret = "PRIVATE-INVALID-" + "x" * 256
    invalid = replace(
        huawei,
        parameters=(
            Parameter("Device.DeviceInfo.SoftwareVersion", secret, "xsd:string"),
        ),
    )
    invalid_at = NOW + timedelta(seconds=20)
    await accept(cwmp_database, invalid, invalid_at, profile_version="2")
    async with cwmp_database.session() as db:
        row = await db.get(CpeInventoryField, (first.cpe_id, "firmware"))
        assert (
            row is not None
            and row.state == "invalid"
            and row.invalid_reason == "too_long"
        )
        assert row.observed_at == invalid_at and row.profile_id == "generic-device-info"
        assert row.profile_version == "2" and row.value_profile_version == "1"
        assert row.value_received_at == NOW
        assert row.value_profile_id == "huawei-eg8145x6-10-v5r022c00s364"
        assert row.source_parameter == "Device.DeviceInfo.SoftwareVersion"
        assert (
            row.value_source_parameter
            == "InternetGatewayDevice.DeviceInfo.SoftwareVersion"
        )
        assert secret not in str(row.__dict__)
        cpe = await db.get(Cpe, first.cpe_id)
        assert cpe is not None and cpe.firmware == "V5R022C00S364"
    # A new valid reading, even with unchanged value, renews its observation time.
    recovered_at = NOW + timedelta(seconds=30)
    await accept(cwmp_database, huawei, recovered_at)
    async with cwmp_database.session() as db:
        row = await db.get(CpeInventoryField, (first.cpe_id, "firmware"))
        assert row is not None and row.state == "valid" and row.invalid_reason is None
        assert row.value_received_at == row.observed_at == recovered_at


@pytest.mark.asyncio
async def test_first_invalid_observation_has_no_invented_valid_provenance(
    cwmp_database: Database, huawei: Inform
) -> None:
    inform = replace(
        huawei, parameters=(Parameter("Device.DeviceInfo.ModelName", "", "xsd:string"),)
    )
    session = await accept(cwmp_database, inform, NOW)
    async with cwmp_database.session() as db:
        row = (await db.scalars(select(CpeInventoryField))).one()
        assert row.field_name == "model" and row.state == "invalid"
        assert row.value_received_at is None and row.value_profile_id is None
        assert row.value_source_parameter is None and row.value_profile_version is None
        cpe = await db.get(Cpe, session.cpe_id)
        assert cpe is not None and cpe.model is None


@pytest.mark.asyncio
async def test_older_and_equal_observations_cannot_replace_newer_metadata(
    cwmp_database: Database, huawei: Inform
) -> None:
    first = await accept(cwmp_database, huawei, NOW)
    later = NOW + timedelta(seconds=20)
    invalid = replace(
        huawei,
        parameters=(
            Parameter("Device.DeviceInfo.SoftwareVersion", "A", "xsd:string"),
            Parameter(
                "InternetGatewayDevice.DeviceInfo.SoftwareVersion", "B", "xsd:string"
            ),
        ),
    )
    await accept(cwmp_database, invalid, later)
    for at in (NOW + timedelta(seconds=10), later):
        await accept(cwmp_database, huawei, at)
    async with cwmp_database.session() as db:
        row = await db.get(CpeInventoryField, (first.cpe_id, "firmware"))
        assert row is not None and row.state == "invalid"
        assert (
            row.invalid_reason == "conflicting_values" and row.source_parameter is None
        )
        assert row.observed_at == later and row.value_received_at == NOW
        cpe = await db.get(Cpe, first.cpe_id)
        assert (
            cpe is not None
            and cpe.firmware == "V5R022C00S364"
            and cpe.last_seen == later
        )


@pytest.mark.asyncio
async def test_concurrent_informs_keep_latest_value_and_its_provenance(
    cwmp_database: Database, huawei: Inform
) -> None:
    initial = await accept(cwmp_database, huawei, NOW)

    async def send(index: int) -> CwmpSession:
        inform = replace(
            huawei,
            parameters=(
                Parameter(
                    "Device.DeviceInfo.SoftwareVersion", f"FW-{index}", "xsd:string"
                ),
            ),
        )
        return await accept(cwmp_database, inform, NOW + timedelta(seconds=index))

    sessions = await asyncio.gather(*(send(i) for i in (3, 1, 4, 2)))
    assert {session.cpe_id for session in sessions} == {initial.cpe_id}
    async with cwmp_database.session() as db:
        cpe = await db.get(Cpe, initial.cpe_id)
        row = await db.get(CpeInventoryField, (initial.cpe_id, "firmware"))
        assert cpe is not None and cpe.firmware == "FW-4"
        assert (
            row is not None
            and row.value_received_at == row.observed_at == NOW + timedelta(seconds=4)
        )
        assert row.value_profile_id == "generic-device-info"
        assert row.value_source_parameter == "Device.DeviceInfo.SoftwareVersion"
        model = await db.get(CpeInventoryField, (initial.cpe_id, "model"))
        assert model is not None and model.value_received_at == NOW
        assert await db.scalar(select(func.count()).select_from(CpeInventoryField)) == 3


@pytest.mark.asyncio
async def test_ordering_is_per_field_and_retry_does_not_refresh_provenance(
    cwmp_database: Database, huawei: Inform
) -> None:
    first = await accept(cwmp_database, huawei, NOW, token="first-session")
    await accept(
        cwmp_database,
        huawei,
        NOW + timedelta(seconds=1),
        token="first-session",
        continuing=True,
    )
    firmware = replace(
        huawei,
        parameters=(
            Parameter("Device.DeviceInfo.SoftwareVersion", "NEW-FW", "xsd:string"),
        ),
    )
    await accept(cwmp_database, firmware, NOW + timedelta(seconds=20))
    hardware = replace(
        huawei,
        parameters=(
            Parameter("Device.DeviceInfo.HardwareVersion", "NEW-HW", "xsd:string"),
        ),
    )
    await accept(cwmp_database, hardware, NOW + timedelta(seconds=10))
    async with cwmp_database.session() as db:
        model = await db.get(CpeInventoryField, (first.cpe_id, "model"))
        hw = await db.get(CpeInventoryField, (first.cpe_id, "hardware"))
        cpe = await db.get(Cpe, first.cpe_id)
        assert model is not None and model.observed_at == NOW
        assert hw is not None and hw.value_received_at == NOW + timedelta(seconds=10)
        assert cpe is not None and cpe.hardware == "NEW-HW" and cpe.firmware == "NEW-FW"
        assert cpe.last_seen == NOW + timedelta(seconds=20)
        assert await db.scalar(select(func.count()).select_from(CwmpSessionRow)) == 3
        assert await db.scalar(select(func.count()).select_from(CwmpEvent)) == 3


@pytest.mark.asyncio
async def test_metadata_rolls_back_together_with_existing_inventory(
    cwmp_database: Database, huawei: Inform
) -> None:
    first = await accept(cwmp_database, huawei, NOW)
    invalid = replace(
        huawei,
        events=(Event("x" * 65, ""),),
        parameters=(
            Parameter(
                "Device.DeviceInfo.SoftwareVersion", "SHOULD-ROLL-BACK", "xsd:string"
            ),
        ),
    )
    with pytest.raises(StorageUnavailable):
        await accept(cwmp_database, invalid, NOW + timedelta(seconds=20))
    async with cwmp_database.session() as db:
        cpe = await db.get(Cpe, first.cpe_id)
        row = await db.get(CpeInventoryField, (first.cpe_id, "firmware"))
        assert (
            cpe is not None and cpe.firmware == "V5R022C00S364" and cpe.last_seen == NOW
        )
        assert row is not None and row.observed_at == row.value_received_at == NOW
        assert row.profile_id == "huawei-eg8145x6-10-v5r022c00s364"
        assert await db.scalar(select(func.count()).select_from(CwmpSessionRow)) == 1


def migrate_to(
    connection: Connection, revision: str, *, downgrade: bool = False
) -> None:
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.attributes["connection"] = connection
    (command.downgrade if downgrade else command.upgrade)(config, revision)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_values",
    [
        {"field_name": "password"},
        {"state": "not_reported"},
        {"state": "invalid", "invalid_reason": "raw-private-value"},
        {"value_profile_version": None},
        {"value_received_at": NOW + timedelta(seconds=1)},
    ],
)
async def test_database_rejects_inconsistent_or_unlisted_metadata(
    cwmp_database: Database, huawei: Inform, invalid_values: dict[str, object]
) -> None:
    first = await accept(cwmp_database, huawei, NOW)
    with pytest.raises(IntegrityError):
        async with cwmp_database.session() as db, db.begin():
            await db.execute(
                update(CpeInventoryField)
                .where(
                    CpeInventoryField.cpe_id == first.cpe_id,
                    CpeInventoryField.field_name == "model",
                )
                .values(**invalid_values)
            )


@pytest.mark.asyncio
async def test_migration_preserves_legacy_values_without_guessing_provenance(
    cwmp_database: Database, huawei: Inform
) -> None:
    first = await accept(cwmp_database, huawei, NOW)
    async with cwmp_database.engine.begin() as connection:
        # Drop only the new metadata in this disposable schema to simulate 0003.
        await connection.run_sync(
            lambda c: migrate_to(c, "0003_shared_cpe_credentials", downgrade=True)
        )
        before = (await connection.execute(text("SELECT * FROM cpes"))).mappings().one()
        await connection.run_sync(lambda c: migrate_to(c, "head"))
        after = (await connection.execute(text("SELECT * FROM cpes"))).mappings().one()
        assert dict(after) == dict(before)
        assert (
            await connection.scalar(select(func.count()).select_from(CpeInventoryField))
            == 0
        )
        assert (
            await connection.scalar(select(func.count()).select_from(CwmpSessionRow))
            == 1
        )
        assert await connection.scalar(select(func.count()).select_from(CwmpEvent)) == 1
    # An invalid reading does not fabricate provenance for the legacy valid value.
    invalid = replace(
        huawei,
        parameters=(Parameter("Device.DeviceInfo.SoftwareVersion", "", "xsd:string"),),
    )
    await accept(cwmp_database, invalid, NOW + timedelta(seconds=10))
    async with cwmp_database.session() as db:
        row = await db.get(CpeInventoryField, (first.cpe_id, "firmware"))
        cpe = await db.get(Cpe, first.cpe_id)
        assert (
            row is not None
            and row.value_received_at is None
            and row.value_profile_id is None
        )
        assert cpe is not None and cpe.firmware == "V5R022C00S364"
    await accept(cwmp_database, huawei, NOW + timedelta(seconds=20))
    async with cwmp_database.session() as db:
        rows = (await db.scalars(select(CpeInventoryField))).all()
        assert len(rows) == 3
        assert all(row.value_received_at == NOW + timedelta(seconds=20) for row in rows)
