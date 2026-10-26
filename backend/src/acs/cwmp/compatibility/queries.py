"""Reviewed, allowlisted query plan for the laboratory Huawei profile."""

from dataclasses import replace
from datetime import datetime
from uuid import UUID

from acs.cwmp.compatibility.generic import GenericInventoryNormalizer
from acs.cwmp.compatibility.huawei import HuaweiEG8145X6Profile
from acs.domain.cpe.inventory import NormalizedInventory
from acs.domain.operations.query import (
    QueryConflict,
    QueryPlan,
    QueryReply,
    QueryTarget,
)

ROOT = "InternetGatewayDevice.DeviceInfo."
NAMES = tuple(
    ROOT + name for name in ("ModelName", "SoftwareVersion", "HardwareVersion")
)
TARGET = QueryTarget(
    "Huawei Technologies Co., Ltd",
    "00259E",
    "EG8145X6-10",
    "V5R022C00S364",
    "EG8145X6-10",
)


class HuaweiQueryPolicy:
    def restore(self, profile_id: str, version: str) -> QueryPlan:
        if (profile_id, version) != (HuaweiEG8145X6Profile.profile_id, "1"):
            raise QueryConflict("unsupported_profile")
        return self.plan(TARGET)

    def plan(self, target: QueryTarget) -> QueryPlan:
        if target != TARGET:
            raise QueryConflict("unsupported_profile")
        return QueryPlan(HuaweiEG8145X6Profile.profile_id, "1", TARGET, NAMES, ROOT)

    def normalize(
        self, reply: QueryReply, plan: QueryPlan, at: datetime, operation_id: UUID
    ) -> NormalizedInventory:
        # Query results are observations, never a synthetic Inform/DeviceId.
        result = GenericInventoryNormalizer().normalize_parameters(reply.parameters, at)
        if (
            result.firmware.state == "valid"
            and result.firmware.value == plan.target.firmware
            and (
                result.model.state != "valid"
                or result.model.value == plan.target.product_class
            )
        ):
            result = replace(
                result, profile_id=plan.profile_id, profile_version=plan.profile_version
            )
        return replace(
            result, source_method="GetParameterValues", operation_id=operation_id
        )
