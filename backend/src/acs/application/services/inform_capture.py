"""Optional evidence collection after an Inform has been accepted."""

from datetime import datetime
from typing import Protocol

from acs.domain.cpe.inform import Inform
from acs.domain.events.session import CwmpSession


class InformCapture(Protocol):
    async def capture(
        self, inform: Inform, session: CwmpSession, received_at: datetime
    ) -> None: ...
