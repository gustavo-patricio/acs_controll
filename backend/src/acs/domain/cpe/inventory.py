"""Canonical inventory observations, without transport or storage dependencies."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID

ObservationState = Literal["valid", "not_reported", "invalid"]
InvalidReason = Literal["empty", "too_long", "control_character", "conflicting_values"]


@dataclass(frozen=True)
class TextObservation:
    state: ObservationState
    value: str | None = field(default=None, repr=False)
    source_parameter: str | None = None
    reason: InvalidReason | None = None

    def __post_init__(self) -> None:
        if self.state == "valid":
            if (
                self.value is None
                or not self.value.strip()
                or len(self.value) > 256
                or any(ord(char) < 32 or ord(char) == 127 for char in self.value)
                or self.source_parameter is None
                or self.reason is not None
            ):
                raise ValueError("Invalid canonical text observation")
        elif self.value is not None:
            raise ValueError("Only valid observations may carry a value")
        elif self.state == "not_reported":
            if self.source_parameter is not None or self.reason is not None:
                raise ValueError("Missing observations cannot claim a source")
        elif self.state == "invalid" and self.reason is None:
            raise ValueError("Invalid observations require a safe reason code")


@dataclass(frozen=True)
class NormalizedInventory:
    model: TextObservation
    firmware: TextObservation
    hardware: TextObservation
    profile_id: str
    profile_version: str
    received_at: datetime
    source_method: Literal["Inform", "GetParameterValues"] = "Inform"
    operation_id: UUID | None = None
