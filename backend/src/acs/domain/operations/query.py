"""Bounded read operations, independent of SOAP, HTTP and storage."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID

from acs.domain.cpe.inform import Parameter

QueryKind = Literal["get_parameter_names", "get_parameter_values"]
QueryScope = Literal["device_info", "discovery", "wan", "optical"]
QueryState = Literal["pending", "dispatched", "succeeded", "failed", "expired"]


class QueryNotFound(Exception):
    pass


class QueryConflict(Exception):
    pass


@dataclass(frozen=True)
class QueryTarget:
    manufacturer: str
    oui: str
    product_class: str
    firmware: str | None
    model: str | None


@dataclass(frozen=True)
class QueryPlan:
    profile_id: str
    profile_version: str
    target: QueryTarget
    names: tuple[str, ...]
    parameter_path: str
    next_level: bool = True
    scope: QueryScope = "device_info"


@dataclass(frozen=True)
class ParameterInfo:
    name: str
    writable: bool


@dataclass(frozen=True)
class QueryReply:
    rpc_id: str
    kind: QueryKind | Literal["fault"]
    parameters: tuple[Parameter, ...] = field(default=(), repr=False)
    names: tuple[ParameterInfo, ...] = ()
    fault_code: int | None = None


@dataclass(frozen=True)
class QueryCommand:
    operation_id: UUID
    rpc_id: str
    kind: QueryKind
    plan: QueryPlan


@dataclass(frozen=True)
class QueryResultItem:
    name: str
    value: str | None = field(default=None, repr=False)
    writable: bool | None = None


@dataclass(frozen=True)
class QueryOperation:
    id: UUID
    cpe_id: UUID
    kind: QueryKind
    state: QueryState
    profile_id: str
    profile_version: str
    created_at: datetime
    expires_at: datetime
    finished_at: datetime | None
    failure_reason: str | None
    fault_code: int | None
    result: tuple[QueryResultItem, ...] = field(repr=False)
    attempt_count: int = 0
    scope: QueryScope = "device_info"
    parameter_path: str | None = None
    capture_evidence: bool = False
    evidence_available: bool = False


@dataclass(frozen=True)
class CatalogEntry:
    cpe_id: UUID
    path: str
    is_object: bool
    writable: bool
    observed_at: datetime
    operation_id: UUID
