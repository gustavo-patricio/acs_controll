"""Application port implemented by protocol compatibility profiles."""

from datetime import datetime
from typing import Protocol

from acs.domain.cpe.inform import Inform
from acs.domain.cpe.inventory import NormalizedInventory


class InventoryNormalizer(Protocol):
    def normalize(
        self, inform: Inform, received_at: datetime
    ) -> NormalizedInventory: ...
