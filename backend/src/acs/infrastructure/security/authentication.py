"""Database-backed authentication with bounded off-loop password verification."""

import asyncio
from typing import Literal

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from acs.domain.security import (
    AuthenticationFailed,
    AuthenticationUnavailable,
    Principal,
)
from acs.infrastructure.database.auth_models import AuthAccount
from acs.infrastructure.database.database import Database
from acs.infrastructure.security.passwords import verify_password

# Unknown accounts still perform the same password derivation.
DUMMY_HASH = "scrypt-v1$" + "00" * 16 + "$" + "00" * 32


class PostgresAuthenticator:
    def __init__(self, database: Database, *, allow_shared_cpe: bool = False) -> None:
        self.database = database
        self.allow_shared_cpe = allow_shared_cpe
        self._slots = asyncio.Semaphore(4)

    async def authenticate(
        self,
        username: str,
        password: str,
        kind: Literal["admin", "cpe"],
    ) -> Principal:
        if not username or len(username) > 64 or len(password.encode("utf-8")) > 1024:
            raise AuthenticationFailed
        try:
            async with asyncio.timeout(1):
                await self._slots.acquire()
        except TimeoutError:
            raise AuthenticationUnavailable("authentication_busy") from None
        try:
            async with self.database.session() as db:
                account = await db.scalar(
                    select(AuthAccount).where(
                        AuthAccount.username == username,
                        AuthAccount.kind == kind,
                    )
                )
                encoded = account.password_hash if account else DUMMY_HASH
                # Keep the slot until the worker actually finishes, even if the caller disconnects.
                task = asyncio.create_task(
                    asyncio.to_thread(verify_password, password, encoded)
                )
                try:
                    valid = await asyncio.shield(task)
                except asyncio.CancelledError:
                    await task
                    raise
                if not valid or account is None or not account.enabled:
                    raise AuthenticationFailed
                if account.shared_cpe and (kind != "cpe" or not self.allow_shared_cpe):
                    raise AuthenticationFailed
                permissions = (
                    frozenset({"inventory:read"})
                    if (
                        kind == "admin" and account.role in ("administrator", "support")
                    )
                    else frozenset[str]()
                )
                if kind == "admin" and account.role == "administrator":
                    permissions = permissions | {"cpe_accounts:write", "cpe:query"}
                return Principal(
                    account.id,
                    kind,
                    permissions,
                    account.oui,
                    account.product_class,
                    account.serial_number,
                    account.shared_cpe,
                )
        except SQLAlchemyError:
            raise AuthenticationUnavailable(
                "authentication_store_unavailable"
            ) from None
        finally:
            self._slots.release()
