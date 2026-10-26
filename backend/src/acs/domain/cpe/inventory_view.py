"""Canonical read snapshot: values, provenance and evidence are separate."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID

from acs.domain.cpe.inventory import InvalidReason


@dataclass(frozen=True)
class ValueProvenance:
    source_parameter: str
    profile_id: str
    profile_version: str
    received_at: datetime
    source_method: Literal["Inform", "GetParameterValues"] = "Inform"
    operation_id: UUID | None = None


@dataclass(frozen=True)
class LastObservation:
    state: Literal["valid", "invalid"]
    source_parameter: str | None
    profile_id: str
    profile_version: str
    received_at: datetime
    invalid_reason: InvalidReason | None
    source_method: Literal["Inform", "GetParameterValues"] = "Inform"
    operation_id: UUID | None = None


@dataclass(frozen=True)
class InventoryFieldView:
    value: str | None = field(repr=False)
    provenance: ValueProvenance | None = None
    last_observation: LastObservation | None = None

    @property
    def value_status(self) -> Literal["available", "missing"]:
        return "available" if self.value is not None else "missing"


@dataclass(frozen=True)
class InventoryFields:
    model: InventoryFieldView
    firmware: InventoryFieldView
    hardware: InventoryFieldView


@dataclass(frozen=True)
class CapabilityView:
    device_support: Literal["unknown", "supported", "not_supported"] = "unknown"
    acs_implemented: bool = False
    profile_validated: bool = False


@dataclass(frozen=True)
class InventoryCapabilities:
    # No supported/unsupported claims may be inferred from missing Inform fields.
    wifi: CapabilityView = CapabilityView()
    wan: CapabilityView = CapabilityView()
    optical: CapabilityView = CapabilityView()
    voice: CapabilityView = CapabilityView()


@dataclass(frozen=True)
class CpeInventoryView:
    cpe_id: UUID
    last_seen: datetime
    fields: InventoryFields
    schema_version: Literal["1"] = "1"
    capabilities: InventoryCapabilities = InventoryCapabilities()
    # Closed until a vendor extension has evidence and its own validated schema.
    extensions: tuple[()] = ()
    remote_queries_available: bool = True
