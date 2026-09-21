"""Protocol-neutral observations reported by a device."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class DeviceId:
    manufacturer: str
    oui: str
    product_class: str
    serial_number: str


@dataclass(frozen=True)
class Event:
    code: str
    command_key: str = field(repr=False)


@dataclass(frozen=True)
class Parameter:
    name: str
    value: str = field(repr=False)
    value_type: str


@dataclass(frozen=True)
class Inform:
    rpc_id: str
    version: str
    device: DeviceId
    events: tuple[Event, ...]
    max_envelopes: int
    current_time: datetime
    retry_count: int
    parameters: tuple[Parameter, ...] = field(repr=False)
