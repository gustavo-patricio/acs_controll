"""Versioned, closed read schema for canonical inventory and provenance."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from acs.domain.cpe.inventory import InvalidReason


class ReadModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class ValueProvenanceResponse(ReadModel):
    source_parameter: str
    profile_id: str
    profile_version: str
    received_at: AwareDatetime
    source_method: Literal["Inform", "GetParameterValues"]
    operation_id: UUID | None


class LastObservationResponse(ReadModel):
    state: Literal["valid", "invalid"]
    source_parameter: str | None
    profile_id: str
    profile_version: str
    received_at: AwareDatetime
    invalid_reason: InvalidReason | None
    source_method: Literal["Inform", "GetParameterValues"]
    operation_id: UUID | None


class InventoryFieldResponse(ReadModel):
    value: str | None = Field(repr=False)
    value_status: Literal["available", "missing"]
    provenance: ValueProvenanceResponse | None
    last_observation: LastObservationResponse | None


class InventoryFieldsResponse(ReadModel):
    model: InventoryFieldResponse
    firmware: InventoryFieldResponse
    hardware: InventoryFieldResponse


class CapabilityResponse(ReadModel):
    device_support: Literal["unknown", "supported", "not_supported"]
    acs_implemented: bool
    profile_validated: bool


class InventoryCapabilitiesResponse(ReadModel):
    wifi: CapabilityResponse
    wan: CapabilityResponse
    optical: CapabilityResponse
    voice: CapabilityResponse


class CpeInventoryResponse(ReadModel):
    schema_version: Literal["1"]
    cpe_id: UUID
    last_seen: AwareDatetime = Field(
        description="Último Inform aceito; não comprova que a CPE esteja online agora."
    )
    fields: InventoryFieldsResponse
    capabilities: InventoryCapabilitiesResponse
    extensions: tuple[()] = Field(
        description="Sem extensões homologadas nesta versão. Parâmetros arbitrários não são expostos."
    )
    remote_queries_available: bool = Field(
        description="Se o ACS disponibiliza consultas remotas. Esta leitura não dispara RPCs."
    )
