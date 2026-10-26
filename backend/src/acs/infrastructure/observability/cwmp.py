"""Allowlisted JSON logs; never serialize requests or exception messages."""

import json
import logging
from uuid import UUID

from acs.domain.cpe.inventory import NormalizedInventory
from acs.domain.events.session import CwmpSession

logger = logging.getLogger("acs.cwmp")


def record_inventory(session: CwmpSession, inventory: NormalizedInventory) -> None:
    logger.info(
        json.dumps(
            {
                "event": "inventory_normalized",
                "session_id": str(session.id),
                "cpe_id": str(session.cpe_id),
                "profile_id": inventory.profile_id,
                "profile_version": inventory.profile_version,
            },
            separators=(",", ":"),
        )
    )


def configure_logging() -> None:
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.propagate = False


def record(
    event: str,
    request_id: UUID,
    status: int,
    session: CwmpSession | None = None,
    *,
    operation_id: UUID | None = None,
) -> None:
    data: dict[str, str | int] = {
        "event": event,
        "request_id": str(request_id),
        "http_status": status,
    }
    if session is not None:
        data.update(
            session_id=str(session.id),
            cpe_id=str(session.cpe_id),
            cwmp_version=session.version,
        )
    if operation_id is not None:
        data["operation_id"] = str(operation_id)
    logger.info(json.dumps(data, separators=(",", ":")))
