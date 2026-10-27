"""Canonical WAN and optical observations, independent of wire and database types."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID

from acs.domain.cpe.inventory_view import ValueProvenance

TelemetryKind = Literal["wan", "optical"]
TelemetryName = Literal[
    "ipv4",
    "ipv6",
    "mac",
    "dns",
    "connection_status",
    "service_list",
    "transport_type",
    "rx_power",
    "tx_power",
]
TelemetryState = Literal["valid", "unavailable", "invalid", "unverified"]
TelemetryReason = Literal[
    "empty",
    "invalid_ip",
    "invalid_mac",
    "invalid_dns",
    "invalid_number",
    "invalid_type",
    "out_of_range",
    "unverified_unit",
    "conflicting_values",
]
TelemetryValue = str | float | tuple[str, ...]


@dataclass(frozen=True)
class TelemetryObservation:
    name: TelemetryName
    state: TelemetryState
    source_parameter: str
    value: TelemetryValue | None = field(default=None, repr=False)
    unit: Literal["dBm"] | None = None
    reason: TelemetryReason | None = None
    source_parameters: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (self.state == "valid") != (self.value is not None):
            raise ValueError("Only validated telemetry may carry canonical values")
        if self.state == "valid" and self.reason is not None:
            raise ValueError("Valid observations cannot carry rejection reasons")


@dataclass(frozen=True)
class TelemetryCollection:
    kind: TelemetryKind
    interface_path: str
    profile_id: str
    profile_version: str
    received_at: datetime
    operation_id: UUID
    fields: tuple[TelemetryObservation, ...]


@dataclass(frozen=True)
class TelemetryProvenance(ValueProvenance):
    source_parameters: tuple[str, ...] = ()


@dataclass(frozen=True)
class TelemetryFieldView:
    value: TelemetryValue | None = field(default=None, repr=False)
    unit: Literal["dBm"] | None = None
    provenance: TelemetryProvenance | None = None
    state: TelemetryState | Literal["not_collected"] = "not_collected"
    observed_at: datetime | None = None
    source_parameter: str | None = None
    operation_id: UUID | None = None
    reason: TelemetryReason | None = None
    source_parameters: tuple[str, ...] = ()


@dataclass(frozen=True)
class WanFields:
    ipv4: TelemetryFieldView = TelemetryFieldView()
    ipv6: TelemetryFieldView = TelemetryFieldView()
    mac: TelemetryFieldView = TelemetryFieldView()
    dns: TelemetryFieldView = TelemetryFieldView()
    connection_status: TelemetryFieldView = TelemetryFieldView()
    service_list: TelemetryFieldView = TelemetryFieldView()
    transport_type: TelemetryFieldView = TelemetryFieldView()


@dataclass(frozen=True)
class OpticalFields:
    rx_power: TelemetryFieldView = TelemetryFieldView()
    tx_power: TelemetryFieldView = TelemetryFieldView()


@dataclass(frozen=True)
class WanInterface:
    interface_path: str
    fields: WanFields


@dataclass(frozen=True)
class OpticalInterface:
    interface_path: str
    fields: OpticalFields


@dataclass(frozen=True)
class CpeTelemetryView:
    cpe_id: UUID
    wan: tuple[WanInterface, ...] = ()
    optical: tuple[OpticalInterface, ...] = ()
    schema_version: Literal["1"] = "1"
