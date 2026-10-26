"""Privileged local laboratory CLI; records OS uid instead of impersonating an API user."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from acs.cwmp.compatibility.queries import HuaweiQueryPolicy
from acs.domain.events.session import StorageUnavailable
from acs.domain.operations.query import QueryConflict, QueryKind, QueryNotFound
from acs.infrastructure.database.database import Database
from acs.infrastructure.database.queries import PostgresQueryRepository
from acs.settings import get_settings


async def run(args: argparse.Namespace) -> None:
    settings = get_settings()
    if settings.environment not in ("development", "test") or settings.database_echo:
        raise SystemExit("Consultas locais exigem laboratorio com SQL echo desativado.")
    db = Database(settings)
    try:
        repo = PostgresQueryRepository(db, HuaweiQueryPolicy())
        if args.operation_id:
            result = await repo.get(args.cpe_id, args.operation_id, datetime.now(UTC))
        else:
            result = await repo.create(
                args.cpe_id,
                cast(QueryKind, args.kind),
                args.key or uuid4(),
                None,
                "local",
                datetime.now(UTC),
            )
        print(
            json.dumps(
                {
                    "id": str(result.id),
                    "state": result.state,
                    "kind": result.kind,
                    "failure_reason": result.failure_reason,
                    "fault_code": result.fault_code,
                    "attempt_count": result.attempt_count,
                }
            )
        )
    except (QueryConflict, QueryNotFound, StorageUnavailable) as exc:
        raise SystemExit(type(exc).__name__) from None
    finally:
        await db.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Agendar/acompanhar consulta local de bancada; requer acesso ao banco."
    )
    parser.add_argument("cpe_id", type=UUID)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument(
        "--kind", choices=("get_parameter_names", "get_parameter_values")
    )
    choice.add_argument("--operation-id", type=UUID)
    parser.add_argument("--key", type=UUID, help="Chave de idempotencia para criacao")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
