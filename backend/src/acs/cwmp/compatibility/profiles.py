"""Explicit precedence for reviewed profiles, with a conservative fallback."""

from datetime import datetime
from typing import Protocol

from acs.cwmp.compatibility.draft_profiles import DraftProfile, draft_from_export
from acs.cwmp.compatibility.generic import GenericInventoryNormalizer
from acs.cwmp.compatibility.huawei import HuaweiEG8145V5Profile, HuaweiEG8145X6Profile
from acs.domain.cpe.inform import Inform
from acs.domain.cpe.inventory import NormalizedInventory


class InventoryProfile(Protocol):
    profile_id: str
    profile_version: str
    priority: int

    def matches(self, inform: Inform, generic: NormalizedInventory) -> bool: ...

    def apply(
        self, inform: Inform, generic: NormalizedInventory
    ) -> NormalizedInventory: ...


class ProfileInventoryNormalizer:
    @staticmethod
    def draft_from_export(document: dict[str, object]) -> DraftProfile:
        """Review proposal only; never adds it to the active profile list."""
        return draft_from_export(document)

    def __init__(self, profiles: tuple[InventoryProfile, ...] | None = None) -> None:
        selected = (
            (HuaweiEG8145X6Profile(), HuaweiEG8145V5Profile())
            if profiles is None
            else profiles
        )
        if len({profile.profile_id for profile in selected}) != len(selected):
            raise ValueError("Inventory profile IDs must be unique")
        if len({profile.priority for profile in selected}) != len(selected):
            raise ValueError("Inventory profile priorities must be unique")
        self.profiles = tuple(
            sorted(selected, key=lambda profile: profile.priority, reverse=True)
        )
        self.generic = GenericInventoryNormalizer()

    def normalize(self, inform: Inform, received_at: datetime) -> NormalizedInventory:
        generic = self.generic.normalize(inform, received_at)
        for profile in self.profiles:
            if profile.matches(inform, generic):
                return profile.apply(inform, generic)
        return generic
