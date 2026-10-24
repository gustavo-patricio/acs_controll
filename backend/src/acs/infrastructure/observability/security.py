"""Allowlisted access logs; never log identities supplied by clients or headers."""

import json
import logging
from uuid import UUID

logger = logging.getLogger("acs.security")


def configure_logging() -> None:
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.propagate = False


def record(
    event: str, request_id: str, status: int, account_id: UUID | None = None
) -> None:
    data: dict[str, str | int] = {
        "event": event,
        "request_id": request_id,
        "http_status": status,
    }
    if account_id is not None:
        data["account_id"] = str(account_id)
    logger.info(json.dumps(data, separators=(",", ":")))
