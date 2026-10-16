"""Explicit public read contracts; internal records remain plain dataclasses."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class CpeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    manufacturer: str
    oui: str
    product_class: str
    serial_number: str
    model: str | None
    firmware: str | None
    hardware: str | None
    first_seen: AwareDatetime
    last_seen: AwareDatetime


class SessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cpe_id: UUID
    version: str
    state: Literal["awaiting_empty", "completed"]
    started_at: AwareDatetime
    expires_at: AwareDatetime
    completed_at: AwareDatetime | None


class EventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    session_id: UUID
    position: int = Field(ge=0)
    code: str
    received_at: AwareDatetime = Field(
        description="ACS session start time, not the time the event occurred on the CPE."
    )


class SessionListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    items: list[SessionResponse]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


class EventListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    items: list[EventResponse]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
