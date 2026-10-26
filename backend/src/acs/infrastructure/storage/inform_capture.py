"""Bounded, opt-in storage of already anonymized laboratory evidence."""

import asyncio
import json
import logging
import os
import tempfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from stat import S_IMODE
from time import monotonic
from uuid import UUID

from acs.domain.cpe.inform import Inform
from acs.domain.events.session import CwmpSession

logger = logging.getLogger("acs.cwmp")


class FileInformCapture:
    def __init__(
        self,
        cpe_id: UUID,
        output: Path,
        seconds: int,
        serialize: Callable[[Inform, datetime], bytes],
    ) -> None:
        self.cpe_id = cpe_id
        self.output = output
        self.serialize = serialize
        self.deadline = monotonic() + seconds
        self.done = False
        self.lock = asyncio.Lock()
        # Linux filesystem required: drvfs may ignore the requested permissions.
        output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if S_IMODE(output.parent.stat().st_mode) != 0o700:
            raise ValueError("Capture directory must have permissions 0700")

    def _publish(self, payload: bytes) -> None:
        # A hard link publishes a complete file atomically without overwriting an
        # existing capture, including when multiple workers receive the Inform.
        descriptor, name = tempfile.mkstemp(prefix=".capture-", dir=self.output.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, self.output)
        finally:
            temporary.unlink(missing_ok=True)

    async def capture(
        self, inform: Inform, session: CwmpSession, received_at: datetime
    ) -> None:
        if session.cpe_id != self.cpe_id:
            return
        async with self.lock:
            if self.done:
                return
            self.done = True
            if monotonic() >= self.deadline:
                outcome = "expired"
            else:
                try:
                    payload = self.serialize(inform, received_at)
                    await asyncio.to_thread(self._publish, payload)
                    outcome = "saved"
                except FileExistsError:
                    outcome = "already_exists"
                except (OSError, ValueError):
                    # Diagnostic collection must not turn a committed Inform
                    # into a failure. Never log payloads, paths or exceptions.
                    outcome = "failed"
            logger.info(
                json.dumps(
                    {
                        "event": "inform_capture",
                        "outcome": outcome,
                        "session_id": str(session.id),
                        "cpe_id": str(session.cpe_id),
                    },
                    separators=(",", ":"),
                )
            )
