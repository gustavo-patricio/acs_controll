"""Bounded collection requests and progress, independent of transports/storage."""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from acs.domain.operations.query import QueryConflict

CONCRETE_OBJECT = re.compile(
    r"(?:InternetGatewayDevice|Device)\.(?:[A-Za-z0-9_][A-Za-z0-9_-]{0,63}\.){0,16}"
)


@dataclass(frozen=True)
class CollectionSpec:
    kind: Literal["discovery", "values"]
    root: str | None = None
    parameter_names: tuple[str, ...] = ()
    max_depth: int = 3
    max_rpcs: int = 20
    max_entries: int = 1000
    batch_size: int = 8

    def validate(self) -> None:
        if not (
            0 <= self.max_depth <= 16
            and 1 <= self.max_rpcs <= 128
            and 1 <= self.max_entries <= 4096
            and 1 <= self.batch_size <= 16
        ):
            raise QueryConflict("invalid_collection_limits")
        if self.kind == "discovery":
            if (
                self.parameter_names
                or self.root is None
                or len(self.root) > 256
                or not CONCRETE_OBJECT.fullmatch(self.root)
            ):
                raise QueryConflict("invalid_discovery_root")
        elif self.kind == "values":
            if (
                self.root is not None
                or not self.parameter_names
                or len(self.parameter_names) > 4096
                or len(set(self.parameter_names)) != len(self.parameter_names)
            ):
                raise QueryConflict("invalid_selection")
        else:
            raise QueryConflict("invalid_collection_kind")


@dataclass(frozen=True)
class CollectionProgress:
    id: UUID
    cpe_id: UUID
    kind: str
    state: str
    spec: CollectionSpec
    rpc_count: int
    completed_count: int
    entry_count: int
    pending_count: int
    failures: tuple[dict[str, str], ...]
    limits_reached: tuple[str, ...]
    operation_ids: tuple[UUID, ...]
    created_at: datetime
    finished_at: datetime | None
