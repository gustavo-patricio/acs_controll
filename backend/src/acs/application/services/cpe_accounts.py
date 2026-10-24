"""Temporary CPE credential management, independent of HTTP and persistence."""

import re
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from acs.domain.security import Principal


class AccountConflict(Exception):
    pass


class AccountNotFound(Exception):
    pass


class AccountStoreUnavailable(Exception):
    pass


@dataclass(frozen=True)
class NewCpeAccount:
    username: str
    password: str = field(repr=False)
    shared: bool = False
    oui: str | None = None
    product_class: str | None = None
    serial_number: str | None = None

    def validate(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,64}", self.username):
            raise ValueError("invalid username")
        if not 14 <= len(self.password) or len(self.password.encode()) > 1024:
            raise ValueError("invalid password length")
        if any(ord(c) < 32 or ord(c) == 127 for c in self.password):
            raise ValueError("invalid password characters")
        identity = (self.oui, self.product_class, self.serial_number)
        if self.shared:
            if any(value is not None for value in identity):
                raise ValueError("shared accounts cannot specify identity")
        elif (
            not self.oui
            or not re.fullmatch(r"[A-F0-9]{6}", self.oui)
            or any(not value or len(value) > 64 for value in identity[1:])
        ):
            raise ValueError("individual accounts require identity")


class CpeAccountRepository(Protocol):
    async def create(self, account: NewCpeAccount) -> UUID: ...
    async def delete(self, account_id: UUID) -> None: ...


class CpeAccountService:
    def __init__(self, repository: CpeAccountRepository) -> None:
        self.repository = repository

    async def create(self, account: NewCpeAccount, principal: Principal) -> UUID:
        principal.require("cpe_accounts:write")
        account.validate()
        return await self.repository.create(account)

    async def delete(self, account_id: UUID, principal: Principal) -> None:
        principal.require("cpe_accounts:write")
        await self.repository.delete(account_id)
