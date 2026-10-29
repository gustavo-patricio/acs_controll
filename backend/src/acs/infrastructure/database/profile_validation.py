"""Explicit approval of the X6 draft using existing read operations."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from acs.cwmp.compatibility.draft_profiles import IDENTITY, ROOT
from acs.cwmp.compatibility.queries import HuaweiQueryPolicy
from acs.domain.events.session import StorageUnavailable
from acs.domain.operations.query import QueryConflict, QueryNotFound, QueryOperation
from acs.infrastructure.database.cwmp_models import Cpe
from acs.infrastructure.database.database import Database
from acs.infrastructure.database.queries import PostgresQueryRepository
from acs.infrastructure.database.query_models import (
    ParameterValueRow,
    ProfileValidationRow,
    QueryOperationRow,
)

NAMES = (ROOT + "HardwareVersion", ROOT + "SoftwareVersion")


class ProfileValidationRepository:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.queries = PostgresQueryRepository(database, HuaweiQueryPolicy())

    async def _identity(self, cpe_id: UUID) -> Cpe:
        try:
            async with self.database.session() as db:
                cpe = await db.get(Cpe, cpe_id)
                if cpe is None:
                    raise QueryNotFound
                return cpe
        except SQLAlchemyError:
            raise StorageUnavailable from None

    @staticmethod
    def _matches(cpe: Cpe) -> bool:
        return (
            cpe.manufacturer == IDENTITY["manufacturer"]
            and cpe.product_class == IDENTITY["product_class"]
            and cpe.firmware == IDENTITY["firmware"]
        )

    async def start(self, cpe_id: UUID, key: UUID, actor: UUID) -> dict[str, object]:
        cpe = await self._identity(cpe_id)
        if not self._matches(cpe):
            raise QueryConflict("draft_identity_or_firmware_changed")
        try:
            async with self.database.session() as db:
                existing = await db.get(ProfileValidationRow, cpe_id)
                if existing is not None and existing.idempotency_key == key:
                    return await self.status(cpe_id)
        except SQLAlchemyError:
            raise StorageUnavailable from None
        # Same persistent queue and session correlation as all other GPV reads.
        operation = await self.queries.create(
            cpe_id,
            "get_parameter_values",
            key,
            actor,
            "api",
            datetime.now(UTC),
            parameter_names=NAMES,
        )
        try:
            async with self.database.session() as db, db.begin():
                row = await db.get(ProfileValidationRow, cpe_id, with_for_update=True)
                if row is None:
                    db.add(
                        ProfileValidationRow(
                            cpe_id=cpe_id,
                            operation_id=operation.id,
                            idempotency_key=key,
                            expected_firmware=IDENTITY["firmware"],
                            approved_by=None,
                            approved_at=None,
                        )
                    )
                elif row.operation_id != operation.id:
                    row.operation_id, row.idempotency_key = operation.id, key
                    row.expected_firmware = IDENTITY["firmware"]
                    row.approved_by, row.approved_at = None, None
        except SQLAlchemyError:
            raise StorageUnavailable from None
        return await self.status(cpe_id)

    async def status(self, cpe_id: UUID) -> dict[str, object]:
        try:
            async with self.database.session() as db:
                current = await db.get(ProfileValidationRow, cpe_id)
                if current is None:
                    raise QueryNotFound
                operation_id = current.operation_id
            # Read through the queue so a timed-out operation becomes terminal.
            await self.queries.get(cpe_id, operation_id, datetime.now(UTC))
            async with self.database.session() as db:
                cpe = await db.get(Cpe, cpe_id)
                if cpe is None:
                    raise QueryNotFound
                row = await db.get(ProfileValidationRow, cpe_id)
                if row is None:
                    raise QueryNotFound
                operation = await db.get(QueryOperationRow, row.operation_id)
                assert operation is not None
                observations = (
                    await db.scalars(
                        select(ParameterValueRow).where(
                            ParameterValueRow.cpe_id == cpe_id,
                            ParameterValueRow.operation_id == row.operation_id,
                            ParameterValueRow.path.in_(NAMES),
                        )
                    )
                ).all()
                values = {item.path: item for item in observations}
                changed = (
                    not self._matches(cpe)
                    or row.expected_firmware != IDENTITY["firmware"]
                )
                fields: list[dict[str, object]] = []
                model_valid = (
                    cpe.manufacturer == IDENTITY["manufacturer"]
                    and cpe.product_class == IDENTITY["product_class"]
                    and cpe.model == IDENTITY["product_class"]
                )
                fields.append(
                    {
                        "field": "model",
                        "path_or_rule": "DeviceId.ProductClass when ModelName is absent",
                        "xml_type": None,
                        "conversion": "texto validado",
                        "required": True,
                        "status": "valid" if model_valid else "invalid",
                        "reason": None if model_valid else "identity_mismatch",
                    }
                )
                for field, path in (("firmware", NAMES[1]), ("hardware", NAMES[0])):
                    item = values.get(path)
                    if operation.state in ("pending", "dispatched"):
                        state, reason = "pending", "awaiting_cpe_response"
                    elif item is None:
                        state, reason = (
                            "invalid"
                            if operation.state in ("failed", "expired")
                            else "pending",
                            operation.failure_reason or "missing_typed_observation",
                        )
                    elif (
                        item.xml_type != "xsd:string"
                        or not item.value.strip()
                        or len(item.value) > 256
                        or any(
                            ord(char) < 32 or ord(char) == 127 for char in item.value
                        )
                    ):
                        state, reason = "invalid", "type_or_value_invalid"
                    elif (
                        field == "firmware"
                        and item.value.strip() != IDENTITY["firmware"]
                    ):
                        state, reason = "invalid", "firmware_mismatch"
                    else:
                        state, reason = "valid", None
                    fields.append(
                        {
                            "field": field,
                            "path_or_rule": path,
                            "xml_type": item.xml_type if item else None,
                            "conversion": "texto validado sem unidade",
                            "required": True,
                            "status": state,
                            "reason": reason,
                        }
                    )
                valid = operation.state == "succeeded" and all(
                    f["status"] == "valid" for f in fields
                )
                effective = (
                    "needs_revalidation"
                    if changed
                    else "approved"
                    if row.approved_at and valid
                    else "validated"
                    if valid
                    else "validating"
                    if operation.state in ("pending", "dispatched")
                    else "invalid"
                )
                return {
                    "cpe_id": cpe_id,
                    "profile_id": "huawei-eg8145x6-10-v5r022c00s364-draft",
                    "state": effective,
                    "operation_id": row.operation_id,
                    "firmware_changed": changed,
                    "fields": fields,
                    "approved_at": row.approved_at if effective == "approved" else None,
                }
        except SQLAlchemyError:
            raise StorageUnavailable from None

    async def approve(self, cpe_id: UUID, actor: UUID) -> dict[str, object]:
        state = await self.status(cpe_id)
        if state["state"] not in ("validated", "approved"):
            raise QueryConflict("required_fields_not_validated")
        try:
            async with self.database.session() as db, db.begin():
                cpe = await db.scalar(
                    select(Cpe).where(Cpe.id == cpe_id).with_for_update()
                )
                row = await db.get(ProfileValidationRow, cpe_id, with_for_update=True)
                assert cpe is not None and row is not None
                if (
                    not self._matches(cpe)
                    or row.expected_firmware != IDENTITY["firmware"]
                    or row.operation_id != state["operation_id"]
                ):
                    raise QueryConflict("revalidation_required")
                if row.approved_at is None:
                    row.approved_by, row.approved_at = actor, datetime.now(UTC)
        except SQLAlchemyError:
            raise StorageUnavailable from None
        return await self.status(cpe_id)

    async def collect(self, cpe_id: UUID, key: UUID, actor: UUID) -> QueryOperation:
        state = await self.status(cpe_id)
        if state["state"] != "approved":
            raise QueryConflict("profile_not_approved_or_firmware_changed")
        return await self.queries.create(
            cpe_id,
            "get_parameter_values",
            key,
            actor,
            "api",
            datetime.now(UTC),
            parameter_names=NAMES,
        )
