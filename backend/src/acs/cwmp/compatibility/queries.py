"""Reviewed, allowlisted query plan for the laboratory Huawei profile."""

import re
from dataclasses import replace
from datetime import datetime
from uuid import UUID

from acs.cwmp.compatibility import huawei_telemetry
from acs.cwmp.compatibility.generic import GenericInventoryNormalizer
from acs.cwmp.compatibility.huawei import HuaweiEG8145X6Profile
from acs.domain.cpe.inventory import NormalizedInventory
from acs.domain.cpe.telemetry import TelemetryCollection
from acs.domain.operations.query import (
    ParameterInfo,
    QueryConflict,
    QueryPlan,
    QueryReply,
    QueryResultItem,
    QueryScope,
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
V5_TARGET = QueryTarget(
    "Huawei Technologies Co., Ltd",
    "00259E",
    "EG8145V5-V2",
    "V5R021C00S210",
    "EG8145V5-V2",
)
V5_PROFILE = "huawei-eg8145v5-v2-v5r021c00s210"
# Structural discovery only. No values are read outside the separate reviewed plan.
DISCOVERY_PATH = re.compile(
    r"InternetGatewayDevice\.(?:(?:WANDevice|X_HW_DEBUG)\.(?:(?:[A-Za-z_][A-Za-z0-9_]{0,63}|[1-9][0-9]{0,3})\.){0,8})?"
)
DISCOVERY_FIELDS = frozenset(
    {
        "ExternalIPAddress",
        "MACAddress",
        "DNSServers",
        "ConnectionStatus",
        "ConnectionType",
        "TransportType",
        "X_HW_SERVICELIST",
        "X_HW_IPv6Address",
        "X_HW_IPv6Prefix",
        "X_HW_IPv6Enable",
        "RXPower",
        "TXPower",
        "RxPower",
        "TxPower",
        "ReceivePower",
        "TransmitPower",
        "IPAddress",
        "Origin",
        "Prefix",
        "AddressingType",
        "Enable",
    }
)


class HuaweiQueryPolicy:
    def restore(
        self,
        profile_id: str,
        version: str,
        scope: QueryScope = "device_info",
        parameter_path: str | None = None,
        names: tuple[str, ...] = (),
    ) -> QueryPlan:
        targets = {HuaweiEG8145X6Profile.profile_id: TARGET, V5_PROFILE: V5_TARGET}
        if version != "1" or profile_id not in targets:
            raise QueryConflict("unsupported_profile")
        return self.plan(targets[profile_id], scope, parameter_path, names)

    def plan(
        self,
        target: QueryTarget,
        scope: QueryScope = "device_info",
        parameter_path: str | None = None,
        names: tuple[str, ...] = (),
    ) -> QueryPlan:
        profile = HuaweiEG8145X6Profile.profile_id
        if target == V5_TARGET or (
            target.model is None and replace(target, model=V5_TARGET.model) == V5_TARGET
        ):
            profile = V5_PROFILE
        elif target != TARGET:
            raise QueryConflict("unsupported_profile")
        if scope in ("wan", "optical"):
            if parameter_path is None:
                raise QueryConflict("unsupported_collection_profile")
            selected = huawei_telemetry.select_names(parameter_path, scope, names)
            return QueryPlan(
                profile, "1", target, selected, parameter_path, scope=scope
            )
        if scope == "discovery":
            if (
                parameter_path is None
                or len(parameter_path) > 256
                or not DISCOVERY_PATH.fullmatch(parameter_path)
            ):
                raise QueryConflict("unsupported_discovery_path")
            return QueryPlan(profile, "1", target, (), parameter_path, scope=scope)
        if parameter_path is not None:
            raise QueryConflict("unsupported_path")
        return QueryPlan(profile, "1", target, NAMES, ROOT)

    def collect(
        self, reply: QueryReply, plan: QueryPlan, at: datetime, operation_id: UUID
    ) -> TelemetryCollection:
        return huawei_telemetry.collect(reply, plan, at, operation_id)

    def collection_results(
        self, reply: QueryReply, plan: QueryPlan, collection: TelemetryCollection
    ) -> tuple[QueryResultItem, ...]:
        return huawei_telemetry.result_items(reply, plan, collection)

    def discovery_names(
        self, reply: QueryReply, plan: QueryPlan
    ) -> tuple[ParameterInfo, ...]:
        if plan.scope == "device_info":
            return tuple(item for item in reply.names if item.name in plan.names)
        return tuple(
            item
            for item in reply.names
            if (
                DISCOVERY_PATH.fullmatch(item.name)
                or item.name.rsplit(".", 1)[-1] in DISCOVERY_FIELDS
            )
        )

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
