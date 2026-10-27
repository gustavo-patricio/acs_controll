"""Candidate Huawei telemetry mappings, gated by per-device discovery.

Real WAN/optical homologation is pending; optical units remain unverified.
"""

import ipaddress
import json
import math
import re
from dataclasses import replace
from datetime import datetime
from uuid import UUID

from acs.domain.cpe.inform import Parameter
from acs.domain.cpe.telemetry import (
    TelemetryCollection,
    TelemetryName,
    TelemetryObservation,
    TelemetryReason,
    TelemetryValue,
)
from acs.domain.operations.query import (
    QueryConflict,
    QueryPlan,
    QueryReply,
    QueryResultItem,
)

WAN_PATH = re.compile(
    r"InternetGatewayDevice\.WANDevice\.[1-9][0-9]{0,3}\.WANConnectionDevice\.[1-9][0-9]{0,3}\.WANPPPConnection\.[1-9][0-9]{0,3}\."
)
OPTICAL_PATH = re.compile(
    r"InternetGatewayDevice\.WANDevice\.[1-9][0-9]{0,3}\.X_GponInterafceConfig\."
)
WAN_FIELDS: dict[str, TelemetryName] = {
    "ExternalIPAddress": "ipv4",
    "MACAddress": "mac",
    "DNSServers": "dns",
    "ConnectionStatus": "connection_status",
    "TransportType": "transport_type",
    "X_HW_SERVICELIST": "service_list",
}
# Unverified for both eligible firmwares. A future verified conversion must be
# selected by profile/revision, never enabled globally for all Huawei devices.
OPTICAL_DBM_SCALE: float | None = None


def field_name(path: str, parameter: str, scope: str) -> TelemetryName | None:
    if not parameter.startswith(path):
        return None
    relative = parameter[len(path) :]
    if scope == "optical":
        optical_fields: dict[str, TelemetryName] = {
            "RXPower": "rx_power",
            "TXPower": "tx_power",
        }
        return optical_fields.get(relative)
    if relative in WAN_FIELDS:
        return WAN_FIELDS[relative]
    if re.fullmatch(r"X_HW_IPv6\.IPv6Address\.[1-9][0-9]{0,3}\.IPAddress", relative):
        return "ipv6"
    return None


def select_names(path: str, scope: str, available: tuple[str, ...]) -> tuple[str, ...]:
    if not (WAN_PATH if scope == "wan" else OPTICAL_PATH).fullmatch(path):
        raise QueryConflict("unsupported_collection_path")
    names = tuple(
        sorted(
            {name for name in available if field_name(path, name, scope) is not None}
        )
    )
    if not names or len(names) > 16:
        raise QueryConflict("discovery_required")
    if scope == "wan" and not {
        path + "X_HW_SERVICELIST",
        path + "TransportType",
    }.issubset(names):
        raise QueryConflict("internet_classification_required")
    return names


def observe(name: TelemetryName, parameter: Parameter) -> TelemetryObservation:
    raw = parameter.value.strip()
    if (
        not raw
        or (name == "ipv4" and raw == "0.0.0.0")
        or (name == "ipv6" and raw == "::")
    ):
        return TelemetryObservation(name, "unavailable", parameter.name, reason="empty")
    value: TelemetryValue
    reason: TelemetryReason = "invalid_type"
    try:
        if name in ("rx_power", "tx_power"):
            reason = "invalid_number"
            if parameter.value_type not in (
                "xsd:int",
                "xsd:unsignedInt",
                "xsd:string",
            ) or not re.fullmatch(r"[+-]?[0-9]{1,10}(?:\.[0-9]{1,4})?", raw):
                raise ValueError
            number = float(raw)
            if OPTICAL_DBM_SCALE is None:
                return TelemetryObservation(
                    name, "unverified", parameter.name, reason="unverified_unit"
                )
            value = round(number * OPTICAL_DBM_SCALE, 4)
            reason = "out_of_range"
            if not math.isfinite(value) or not -80 <= value <= 20:
                raise ValueError
            return TelemetryObservation(name, "valid", parameter.name, value, "dBm")
        if parameter.value_type != "xsd:string" or len(raw) > 1024:
            raise ValueError
        if name in ("ipv4", "ipv6"):
            reason = "invalid_ip"
            if "%" in raw:
                raise ValueError
            ip = ipaddress.ip_address(raw)
            if (
                ip.version != (4 if name == "ipv4" else 6)
                or ip.is_unspecified
                or ip.is_multicast
            ):
                raise ValueError
            value = (str(ip),)
        elif name == "dns":
            reason = "invalid_dns"
            parts = raw.split(",")
            if len(parts) > 8 or "%" in raw:
                raise ValueError
            ips = tuple(ipaddress.ip_address(part.strip()) for part in parts)
            if any(ip.is_unspecified or ip.is_multicast for ip in ips):
                raise ValueError
            value = tuple(dict.fromkeys(str(ip) for ip in ips))
        elif name == "mac":
            reason = "invalid_mac"
            normalized = raw.replace("-", ":").lower()
            if (
                not re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", normalized)
                or normalized == "00:00:00:00:00:00"
                or int(normalized[:2], 16) & 1
            ):
                raise ValueError
            value = normalized
        else:
            # Only known enums/service labels, never user-created connection names.
            choices = {
                "connection_status": {
                    "Unconfigured",
                    "Connecting",
                    "Authenticating",
                    "Connected",
                    "PendingDisconnect",
                    "Disconnecting",
                    "Disconnected",
                },
                "transport_type": {"PPPoE"},
                "service_list": {"INTERNET", "TR069", "VOIP", "IPTV", "OTHER"},
            }
            values = tuple(raw.split(","))
            if name != "service_list" and len(values) != 1:
                raise ValueError
            if any(v not in choices[name] for v in values):
                raise ValueError
            value = values if name == "service_list" else raw
        return TelemetryObservation(name, "valid", parameter.name, value)
    except ValueError:
        return TelemetryObservation(name, "invalid", parameter.name, reason=reason)


def collect(
    reply: QueryReply, plan: QueryPlan, at: datetime, operation_id: UUID
) -> TelemetryCollection:
    fields: dict[TelemetryName, TelemetryObservation] = {}
    for parameter in reply.parameters:
        name = field_name(plan.parameter_path, parameter.name, plan.scope)
        if name is None:
            raise QueryConflict("unexpected_parameters")
        observation = observe(name, parameter)
        observation = replace(observation, source_parameters=(parameter.name,))
        previous = fields.get(name)
        if previous is not None and name == "ipv6":
            sources = tuple(
                sorted(previous.source_parameters + observation.source_parameters)
            )
            if previous.state == "invalid" or observation.state == "invalid":
                observation = TelemetryObservation(
                    name,
                    "invalid",
                    sources[0],
                    reason="conflicting_values",
                    source_parameters=sources,
                )
            else:
                values = tuple(
                    sorted(
                        set(
                            (
                                previous.value
                                if isinstance(previous.value, tuple)
                                else ()
                            )
                            + (
                                observation.value
                                if isinstance(observation.value, tuple)
                                else ()
                            )
                        )
                    )
                )
                observation = TelemetryObservation(
                    name,
                    "valid" if values else "unavailable",
                    sources[0],
                    values or None,
                    reason=None if values else "empty",
                    source_parameters=sources,
                )
        fields[name] = observation
    if plan.scope == "wan":
        service = fields.get("service_list")
        transport = fields.get("transport_type")
        if (
            service is None
            or not isinstance(service.value, tuple)
            or "INTERNET" not in service.value
            or transport is None
            or transport.value != "PPPoE"
        ):
            raise QueryConflict("not_internet_pppoe")
    return TelemetryCollection(
        "wan" if plan.scope == "wan" else "optical",
        plan.parameter_path,
        plan.profile_id,
        plan.profile_version,
        at,
        operation_id,
        tuple(fields.values()),
    )


def result_items(
    reply: QueryReply, plan: QueryPlan, collection: TelemetryCollection
) -> tuple[QueryResultItem, ...]:
    if plan.scope == "optical":
        # A numeric calibration result is explicitly raw; canonical optics remain
        # unverified until their unit/scale is confirmed. No malformed raw value survives.
        states = {field.source_parameter: field.state for field in collection.fields}
        return tuple(
            QueryResultItem(
                p.name,
                p.value.strip()
                if states.get(p.name) in ("valid", "unverified")
                else None,
            )
            for p in reply.parameters
        )
    items: list[QueryResultItem] = []
    for parameter in reply.parameters:
        name = field_name(plan.parameter_path, parameter.name, plan.scope)
        assert name is not None
        observation = observe(name, parameter)
        value = observation.value
        items.append(
            QueryResultItem(
                parameter.name,
                json.dumps(value)
                if isinstance(value, tuple)
                else str(value)
                if value is not None
                else None,
            )
        )
    return tuple(items)
