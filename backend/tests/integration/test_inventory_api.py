"""Values and metadata are read from a consistent PostgreSQL snapshot."""

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from acs.api.app import create_app
from acs.application.services.cpe_inventory import CpeInventoryService
from acs.domain.cpe.inform import Inform, Parameter
from acs.infrastructure.database.cpe_inventory import PostgresCpeInventoryRepository
from acs.infrastructure.database.cwmp_models import (
    Cpe,
    CpeInventoryField,
    CwmpSessionRow,
)
from acs.infrastructure.database.database import Database
from auth_support import ADMIN, StubAuthenticator, authorized_client
from integration.test_inventory_provenance import NOW, accept, cwmp_database, huawei

__all__ = ["cwmp_database", "huawei"]
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("ACS_RUN_INTEGRATION_TESTS") != "1",
        reason="PostgreSQL integration is opt-in",
    ),
]


@pytest.mark.asyncio
async def test_real_inventory_api_valid_partial_invalid_legacy_and_missing(
    cwmp_database: Database, huawei: Inform
) -> None:
    first = await accept(cwmp_database, huawei, NOW)
    service = CpeInventoryService(PostgresCpeInventoryRepository(cwmp_database))
    app = create_app(service, StubAuthenticator())
    path = f"/api/v1/cpes/{first.cpe_id}/inventory"
    async with authorized_client(transport=ASGITransport(app=app)) as client:
        initial = (await client.get(path)).json()
        model = initial["fields"]["model"]
        assert model["value"] == "EG8145X6-10"
        assert model["provenance"]["source_parameter"] == "DeviceId.ProductClass"
        assert model["provenance"]["profile_id"] == "huawei-eg8145x6-10-v5r022c00s364"
        await accept(
            cwmp_database, replace(huawei, parameters=()), NOW + timedelta(seconds=10)
        )
        partial = (await client.get(path)).json()
        assert partial["fields"] == initial["fields"]
        assert partial["last_seen"] != initial["last_seen"]
        invalid = replace(
            huawei,
            parameters=(
                Parameter(
                    "Device.DeviceInfo.SoftwareVersion",
                    "PRIVATE-REJECTED" * 30,
                    "xsd:string",
                ),
            ),
        )
        await accept(cwmp_database, invalid, NOW + timedelta(seconds=20))
        response = await client.get(path)
        field = response.json()["fields"]["firmware"]
        assert field["value"] == "V5R022C00S364"
        assert field["provenance"] == initial["fields"]["firmware"]["provenance"]
        assert field["last_observation"]["state"] == "invalid"
        assert field["last_observation"]["invalid_reason"] == "too_long"
        assert (
            "PRIVATE-REJECTED" not in response.text
            and "ANONYMIZED0001" not in response.text
        )
        assert (
            await client.get(f"/api/v1/cpes/{uuid4()}/inventory")
        ).status_code == 404
        # Simulate pre-migration values without inventing a timestamp or profile.
        async with cwmp_database.session() as db, db.begin():
            await db.execute(
                delete(CpeInventoryField).where(
                    CpeInventoryField.cpe_id == first.cpe_id
                )
            )
            await db.execute(
                update(Cpe).where(Cpe.id == first.cpe_id).values(model=None)
            )
        legacy = (await client.get(path)).json()["fields"]
        assert legacy["firmware"]["value_status"] == "available"
        assert legacy["firmware"]["provenance"] is None
        assert legacy["firmware"]["last_observation"] is None
        assert legacy["model"]["value_status"] == "missing"
        assert (await client.get(f"/api/v1/cpes/{first.cpe_id}")).json()[
            "firmware"
        ] == "V5R022C00S364"
    async with cwmp_database.session() as db:
        assert await db.scalar(select(func.count()).select_from(CwmpSessionRow)) == 3


@pytest.mark.asyncio
async def test_inventory_view_uses_one_snapshot_during_concurrent_inform(
    cwmp_database: Database, huawei: Inform
) -> None:
    first = await accept(cwmp_database, huawei, NOW)

    class InterleavedRepository(PostgresCpeInventoryRepository):
        @asynccontextmanager
        async def _cpe_snapshot(self, cpe_id: UUID) -> AsyncGenerator[AsyncSession]:
            async with super()._cpe_snapshot(cpe_id) as session:
                # The existence check has already established the read snapshot.
                changed = replace(
                    huawei,
                    parameters=(
                        Parameter(
                            "Device.DeviceInfo.SoftwareVersion", "NEW-FW", "xsd:string"
                        ),
                    ),
                )
                await accept(cwmp_database, changed, NOW + timedelta(seconds=10))
                yield session

    view = await CpeInventoryService(
        InterleavedRepository(cwmp_database), ADMIN
    ).get_inventory(first.cpe_id)
    assert view.fields.firmware.value == "V5R022C00S364"
    assert view.fields.firmware.provenance is not None
    assert view.fields.firmware.provenance.received_at == NOW
    fresh = await PostgresCpeInventoryRepository(cwmp_database).get_inventory(
        first.cpe_id
    )
    assert fresh.fields.firmware.value == "NEW-FW"
    assert fresh.fields.firmware.provenance is not None
    assert fresh.fields.firmware.provenance.received_at == NOW + timedelta(seconds=10)


@pytest.mark.asyncio
async def test_inventory_storage_failure_returns_safe_http_error(
    cwmp_database: Database, huawei: Inform
) -> None:
    first = await accept(cwmp_database, huawei, NOW)
    async with cwmp_database.engine.begin() as connection:
        await connection.execute(text("DROP TABLE cpe_inventory_fields"))
    app = create_app(
        CpeInventoryService(PostgresCpeInventoryRepository(cwmp_database)),
        StubAuthenticator(),
    )
    async with authorized_client(transport=ASGITransport(app=app)) as client:
        response = await client.get(f"/api/v1/cpes/{first.cpe_id}/inventory")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "inventory_unavailable"
    assert "cpe_inventory_fields" not in response.text and "SELECT" not in response.text
