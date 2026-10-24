"""Authentication boundary; implementations never return stored secrets."""

from typing import Literal, Protocol

from acs.domain.security import Principal


class Authenticator(Protocol):
    async def authenticate(
        self,
        username: str,
        password: str,
        kind: Literal["admin", "cpe"],
    ) -> Principal: ...
