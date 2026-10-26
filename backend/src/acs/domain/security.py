"""Authenticated identities and authorization independent of transports."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID


class AuthenticationFailed(Exception):
    """Credentials were not accepted."""


class AccessDenied(Exception):
    """An authenticated identity cannot perform this operation."""


AuthenticationUnavailableReason = Literal[
    "authentication_unavailable",
    "authentication_store_unavailable",
    "authentication_busy",
]


class AuthenticationUnavailable(Exception):
    """Authentication storage or processing is unavailable."""

    def __init__(
        self, reason: AuthenticationUnavailableReason = "authentication_unavailable"
    ) -> None:
        self.reason: AuthenticationUnavailableReason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class Principal:
    id: UUID
    kind: Literal["admin", "cpe"]
    permissions: frozenset[str]
    oui: str | None = None
    product_class: str | None = None
    serial_number: str | None = None
    shared_cpe: bool = False

    def require(self, permission: str) -> None:
        if self.kind != "admin" or permission not in self.permissions:
            raise AccessDenied

    def require_device(self, oui: str, product_class: str, serial_number: str) -> None:
        self.require_cpe()
        if self.shared_cpe:
            return
        if (self.oui, self.product_class, self.serial_number) != (
            oui,
            product_class,
            serial_number,
        ):
            raise AccessDenied

    def require_cpe(self) -> None:
        if self.kind != "cpe":
            raise AccessDenied
