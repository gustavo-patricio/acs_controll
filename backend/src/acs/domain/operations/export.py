"""Read-only export source, independent of persistence and serialization."""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class ExportName:
    path: str
    is_object: bool
    writable: bool
    observed_at: datetime
    operation_id: UUID


@dataclass(frozen=True)
class ExportValue:
    path: str
    xml_type: str
    value: str = field(repr=False)
    observed_at: datetime
    operation_id: UUID


@dataclass(frozen=True)
class ExportOperation:
    id: UUID
    kind: str
    scope: str
    path: str | None
    names: tuple[str, ...]
    result_names: tuple[str, ...]
    state: str
    failure_reason: str | None
    fault_code: int | None
    finished_at: datetime | None


@dataclass(frozen=True)
class ExportSnapshot:
    manufacturer: str
    model: str | None
    product_class: str
    firmware: str | None
    serial: str = field(repr=False)
    prefix: str
    catalog: tuple[ExportName, ...]
    values: tuple[ExportValue, ...] = field(repr=False)
    operations: tuple[ExportOperation, ...]
    history_truncated: bool
