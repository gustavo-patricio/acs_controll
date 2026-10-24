"""PostgreSQL adapter for temporary CPE account management."""

import asyncio
from uuid import UUID, uuid4

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from acs.application.services.cpe_accounts import (
    AccountConflict,
    AccountNotFound,
    AccountStoreUnavailable,
    NewCpeAccount,
)
from acs.infrastructure.database.auth_models import AuthAccount
from acs.infrastructure.database.database import Database
from acs.infrastructure.security.passwords import hash_password


class PostgresCpeAccountRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def create(self, account: NewCpeAccount) -> UUID:
        encoded = await asyncio.to_thread(hash_password, account.password)
        identifier = uuid4()
        try:
            async with self.database.session() as db, db.begin():
                db.add(
                    AuthAccount(
                        id=identifier,
                        kind="cpe",
                        username=account.username,
                        password_hash=encoded,
                        enabled=True,
                        role="device",
                        shared_cpe=account.shared,
                        oui=account.oui,
                        product_class=account.product_class,
                        serial_number=account.serial_number,
                    )
                )
            return identifier
        except IntegrityError:
            raise AccountConflict from None
        except SQLAlchemyError:
            raise AccountStoreUnavailable from None

    async def delete(self, account_id: UUID) -> None:
        try:
            async with self.database.session() as db, db.begin():
                removed = await db.scalar(
                    delete(AuthAccount)
                    .where(AuthAccount.id == account_id, AuthAccount.kind == "cpe")
                    .returning(AuthAccount.id)
                )
                if removed is None:
                    raise AccountNotFound
        except SQLAlchemyError:
            raise AccountStoreUnavailable from None
