"""Session state independent of transport and persistence."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID


class InvalidSession(Exception):
    """The continuation does not identify an active session."""


class StorageUnavailable(Exception):
    """Persistence failed; no protocol acknowledgement may be sent."""


@dataclass(frozen=True)
class CwmpSession:
    id: UUID
    cpe_id: UUID
    rpc_id: str
    version: str
    state: Literal["awaiting_empty", "completed"]
    expires_at: datetime

    def require_active(self, now: datetime) -> None:
        if self.state != "awaiting_empty" or now >= self.expires_at:
            raise InvalidSession
