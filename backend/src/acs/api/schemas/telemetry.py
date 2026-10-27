"""Closed read schema for WAN Internet/PPPoE and optical measurements."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from acs.api.schemas.inventory import ReadModel, ValueProvenanceResponse
from acs.domain.cpe.telemetry import TelemetryReason, TelemetryState


class TelemetryProvenanceResponse(ValueProvenanceResponse):
    source_parameters: tuple[str, ...]


class TelemetryFieldResponse(ReadModel):
    value: str | float | tuple[str, ...] | None = Field(repr=False)
    unit: Literal["dBm"] | None
    provenance: TelemetryProvenanceResponse | None
    state: TelemetryState | Literal["not_collected"]
    observed_at: AwareDatetime | None
    source_parameter: str | None
    source_parameters: tuple[str, ...]
    operation_id: UUID | None
    reason: TelemetryReason | None


class WanFieldsResponse(ReadModel):
    ipv4: TelemetryFieldResponse
    ipv6: TelemetryFieldResponse
    mac: TelemetryFieldResponse
    dns: TelemetryFieldResponse
    connection_status: TelemetryFieldResponse
    service_list: TelemetryFieldResponse
    transport_type: TelemetryFieldResponse


class OpticalFieldsResponse(ReadModel):
    rx_power: TelemetryFieldResponse
    tx_power: TelemetryFieldResponse


class WanInterfaceResponse(ReadModel):
    interface_path: str
    fields: WanFieldsResponse


class OpticalInterfaceResponse(ReadModel):
    interface_path: str
    fields: OpticalFieldsResponse


class CpeTelemetryResponse(ReadModel):
    cpe_id: UUID
    schema_version: Literal["1"]
    wan: tuple[WanInterfaceResponse, ...]
    optical: tuple[OpticalInterfaceResponse, ...]
