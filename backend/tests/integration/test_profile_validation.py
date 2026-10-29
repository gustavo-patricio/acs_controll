"""Explicit validation and approval against the existing CWMP queue."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from acs.api.app import create_app
from acs.application.services.cpe_inventory import CpeInventoryService
from acs.application.services.inform import InformService
from acs.application.services.profile_validations import ProfileValidationService
from acs.application.services.queries import QueryService
from acs.cwmp.compatibility.evidence import anonymized_inform
from acs.cwmp.compatibility.profiles import ProfileInventoryNormalizer
from acs.cwmp.compatibility.queries import HuaweiQueryPolicy
from acs.cwmp.http.app import create_app as receiver_app
from acs.cwmp.soap.inform import CWMP, parse_envelope
from acs.domain.cpe.inform import Inform
from acs.infrastructure.database.cpe_inventory import PostgresCpeInventoryRepository
from acs.infrastructure.database.database import Database
from acs.infrastructure.database.inform_repository import PostgresInformRepository
from acs.infrastructure.database.profile_validation import ProfileValidationRepository
from acs.infrastructure.database.queries import PostgresQueryRepository
from auth_support import PASSWORD
from integration.test_inventory_provenance import accept, cwmp_database, huawei
from integration.test_read_queries import QueryAuth, pytestmark
from query_support import reply_xml

__all__ = ["cwmp_database", "huawei", "pytestmark"]
ROOT = "InternetGatewayDevice.DeviceInfo."
NAMES = (ROOT + "HardwareVersion", ROOT + "SoftwareVersion")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome", ["valid", "fault", "invalid_type", "wrong_firmware"]
)
async def test_profile_validation_approval_and_profile_collection(
    cwmp_database: Database, huawei: Inform, outcome: str
) -> None:
    now = datetime.now(UTC)
    first = await accept(cwmp_database, huawei, now)
    auth = QueryAuth(huawei)
    queries = QueryService(PostgresQueryRepository(cwmp_database, HuaweiQueryPolicy()))
    profiles = ProfileValidationService(ProfileValidationRepository(cwmp_database))
    api = create_app(
        CpeInventoryService(PostgresCpeInventoryRepository(cwmp_database)),
        auth,
        queries=queries,
        profile_validations=profiles,
    )
    receiver = receiver_app(
        InformService(
            PostgresInformRepository(cwmp_database), ProfileInventoryNormalizer()
        ),
        auth,
        queries,
    )
    path = f"/api/v1/cpes/{first.cpe_id}/profiles/draft"
    async with (
        AsyncClient(transport=ASGITransport(app=api), base_url="https://test") as admin,
        AsyncClient(
            transport=ASGITransport(app=receiver),
            base_url="https://test",
            auth=("device", PASSWORD),
        ) as cpe,
    ):
        assert (
            await admin.post(path + "/validate", json={"idempotency_key": str(uuid4())})
        ).status_code == 401
        assert (
            await admin.post(
                path + "/validate",
                json={"idempotency_key": str(uuid4())},
                auth=("support", PASSWORD),
            )
        ).status_code == 403
        admin.auth = ("admin", PASSWORD)
        key = str(uuid4())
        created = await admin.post(path + "/validate", json={"idempotency_key": key})
        assert created.status_code == 202 and created.json()["state"] == "validating"
        assert (
            await admin.post(path + "/validate", json={"idempotency_key": key})
        ).json()["operation_id"] == created.json()["operation_id"]
        assert (await admin.post(path + "/approve")).status_code == 409
        assert (
            await admin.post(path + "/collect", json={"idempotency_key": str(uuid4())})
        ).status_code == 409
        inform = anonymized_inform(huawei, now)
        assert (
            await cpe.post(
                "/cwmp", content=inform, headers={"Content-Type": "text/xml"}
            )
        ).status_code == 200
        sent = await cpe.post("/cwmp", content=b"")
        assert sent.status_code == 200
        rpc_id, rpc = parse_envelope(sent.content)
        assert rpc.tag == f"{{{CWMP}}}GetParameterValues"
        assert (
            tuple(item.text for item in rpc.findall("ParameterNames/string")) == NAMES
        )
        values = ("343D.D", "V5R022C00S364" if outcome != "wrong_firmware" else "OTHER")
        reply = reply_xml(rpc_id, "get_parameter_values", names=NAMES, values=values)
        if outcome == "fault":
            reply = reply_xml(rpc_id, "get_parameter_values", fault=9005)
        if outcome == "invalid_type":
            reply = reply_xml(
                rpc_id, "get_parameter_values", names=NAMES, values=("1", "2")
            ).replace(b'xsi:type="xsd:string"', b'xsi:type="xsd:int"')
        assert (
            await cpe.post("/cwmp", content=reply, headers={"Content-Type": "text/xml"})
        ).status_code == 204
        state = (await admin.get(path)).json()
        assert [field["field"] for field in state["fields"]] == [
            "model",
            "firmware",
            "hardware",
        ]
        assert state["state"] == ("validated" if outcome == "valid" else "invalid")
        if outcome != "valid":
            assert (await admin.post(path + "/approve")).status_code == 409
            return
        assert all(field["status"] == "valid" for field in state["fields"])
        approved = await admin.post(path + "/approve")
        assert approved.status_code == 200 and approved.json()["state"] == "approved"
        collected = await admin.post(
            path + "/collect", json={"idempotency_key": str(uuid4())}
        )
        assert (
            collected.status_code == 202
            and collected.json()["kind"] == "get_parameter_values"
        )
        # The profile collection reads only reviewed leaves, not the catalog tree.
        assert (
            await cpe.post(
                "/cwmp", content=inform, headers={"Content-Type": "text/xml"}
            )
        ).status_code == 200
        followup = await cpe.post("/cwmp", content=b"")
        assert followup.status_code == 200
        _, rpc = parse_envelope(followup.content)
        assert (
            tuple(item.text for item in rpc.findall("ParameterNames/string")) == NAMES
        )
        changed_inform = replace(
            huawei,
            parameters=tuple(
                replace(item, value="NEW-FIRMWARE")
                if item.name.endswith("SoftwareVersion")
                else item
                for item in huawei.parameters
            ),
        )
        await accept(cwmp_database, changed_inform, now + timedelta(seconds=2))
        assert (await admin.get(path)).json()["state"] == "needs_revalidation"
        await accept(cwmp_database, huawei, now + timedelta(seconds=3))
        assert (await admin.get(path)).json()["state"] == "needs_revalidation"
        assert (
            await admin.post(path + "/collect", json={"idempotency_key": str(uuid4())})
        ).status_code == 409
        assert (await admin.post(path + "/approve")).status_code == 409


@pytest.mark.asyncio
async def test_validation_timeout_is_visible_and_cannot_be_approved(
    cwmp_database: Database, huawei: Inform
) -> None:
    from sqlalchemy import update

    from acs.domain.operations.query import QueryConflict
    from acs.infrastructure.database.query_models import QueryOperationRow

    now = datetime.now(UTC)
    cpe = await accept(cwmp_database, huawei, now)
    repository = ProfileValidationRepository(cwmp_database)
    state = await repository.start(cpe.cpe_id, uuid4(), uuid4())
    async with cwmp_database.session() as db, db.begin():
        await db.execute(
            update(QueryOperationRow)
            .where(QueryOperationRow.id == state["operation_id"])
            .values(expires_at=now - timedelta(seconds=1))
        )
    current = await repository.status(cpe.cpe_id)
    assert current["state"] == "invalid"
    fields = cast(list[dict[str, object]], current["fields"])
    assert all(item["status"] == "invalid" for item in fields[1:])
    with pytest.raises(QueryConflict):
        await repository.approve(cpe.cpe_id, uuid4())
