"""PostgreSQL inventory queries with explicit field mapping."""

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from acs.application.services.cpe_inventory import (
    CpePage,
    CpeSummary,
    InventoryUnavailable,
)
from acs.infrastructure.database.cwmp_models import Cpe
from acs.infrastructure.database.database import Database


class PostgresCpeInventoryRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def list_cpes(
        self,
        limit: int,
        offset: int,
        manufacturer: str | None,
        serial_number: str | None,
    ) -> CpePage:
        query = select(Cpe)
        count = select(func.count()).select_from(Cpe)
        if manufacturer is not None:
            query = query.where(Cpe.manufacturer == manufacturer)
            count = count.where(Cpe.manufacturer == manufacturer)
        if serial_number is not None:
            query = query.where(Cpe.serial_number == serial_number)
            count = count.where(Cpe.serial_number == serial_number)
        try:
            async with self.database.session() as session:
                # Keep the count and page on one snapshot while Inform writes continue.
                await session.connection(
                    execution_options={"isolation_level": "REPEATABLE READ"}
                )
                total = await session.scalar(count)
                rows = (
                    await session.scalars(
                        query.order_by(Cpe.last_seen.desc(), Cpe.id)
                        .limit(limit)
                        .offset(offset)
                    )
                ).all()
                return CpePage(
                    items=tuple(
                        CpeSummary(
                            id=row.id,
                            manufacturer=row.manufacturer,
                            oui=row.oui,
                            product_class=row.product_class,
                            serial_number=row.serial_number,
                            model=row.model,
                            firmware=row.firmware,
                            hardware=row.hardware,
                            first_seen=row.first_seen,
                            last_seen=row.last_seen,
                        )
                        for row in rows
                    ),
                    total=total or 0,
                    limit=limit,
                    offset=offset,
                )
        except SQLAlchemyError:
            raise InventoryUnavailable from None
