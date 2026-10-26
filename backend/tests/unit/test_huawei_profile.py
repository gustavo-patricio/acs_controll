"""Evidence-bound Huawei selection, precedence and canonical observations."""

import json
import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from acs.application.services.inform import InformService
from acs.cwmp.compatibility.generic import GenericInventoryNormalizer
from acs.cwmp.compatibility.huawei import HuaweiEG8145X6Profile
from acs.cwmp.compatibility.profiles import ProfileInventoryNormalizer
from acs.cwmp.soap.inform import parse_inform
from acs.domain.cpe.inform import Inform, Parameter
from acs.domain.cpe.inventory import NormalizedInventory, TextObservation
from acs.domain.events.session import CwmpSession, StorageUnavailable
from acs.domain.security import AccessDenied
from acs.infrastructure.observability.cwmp import record_inventory
from auth_support import ADMIN, principal_for
from contract.test_cwmp import BrokenRepository, MemoryRepository

NOW = datetime(2026, 10, 25, tzinfo=UTC)
FIXTURE = (
    Path(__file__).parents[1] / "fixtures/cwmp/huawei_eg8145x6_10_v5r022c00s364.xml"
)


@pytest.fixture
def huawei() -> Inform:
    return parse_inform(FIXTURE.read_bytes())


def test_reviewed_fixture_uses_product_class_only_for_missing_model(
    huawei: Inform,
) -> None:
    result = ProfileInventoryNormalizer().normalize(huawei, NOW)
    assert result.profile_id == HuaweiEG8145X6Profile.profile_id
    assert result.profile_version == "1" and result.received_at == NOW
    assert result.model == TextObservation(
        "valid", "EG8145X6-10", "DeviceId.ProductClass"
    )
    generic = GenericInventoryNormalizer().normalize(huawei, NOW)
    assert result.firmware == generic.firmware
    assert result.hardware == generic.hardware
    assert generic.model.state == "not_reported"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("manufacturer", "Huawei"),
        ("manufacturer", "Other Manufacturer"),
        ("oui", "A1B2C3"),
        ("product_class", "EG8145V5"),
        ("product_class", "eg8145x6-10"),
    ],
)
def test_unreviewed_identity_stays_generic(
    huawei: Inform, field: str, value: str
) -> None:
    inform = replace(huawei, device=replace(huawei.device, **{field: value}))
    assert ProfileInventoryNormalizer().normalize(inform, NOW) == (
        GenericInventoryNormalizer().normalize(inform, NOW)
    )


@pytest.mark.parametrize("firmware", ["", " ", "V5R022C00S365", "x" * 257])
def test_unknown_or_invalid_firmware_does_not_select_huawei(
    huawei: Inform, firmware: str
) -> None:
    inform = replace(
        huawei,
        parameters=(
            Parameter(
                "InternetGatewayDevice.DeviceInfo.SoftwareVersion",
                firmware,
                "xsd:string",
            ),
        ),
    )
    result = ProfileInventoryNormalizer().normalize(inform, NOW)
    assert result.profile_id == "generic-device-info"
    assert result.model.state == "not_reported"


def test_partial_inform_does_not_reuse_previous_profile(huawei: Inform) -> None:
    normalizer = ProfileInventoryNormalizer()
    assert (
        normalizer.normalize(huawei, NOW).profile_id == HuaweiEG8145X6Profile.profile_id
    )
    result = normalizer.normalize(replace(huawei, parameters=()), NOW)
    assert result.profile_id == "generic-device-info"
    assert result.model.state == "not_reported"


@pytest.mark.parametrize("mode", ["different_root", "cwmp_version", "conflict"])
def test_unreviewed_protocol_or_conflicting_firmware_stays_generic(
    huawei: Inform, mode: str
) -> None:
    if mode == "different_root":
        inform = replace(
            huawei,
            parameters=tuple(
                replace(p, name=p.name.replace("InternetGatewayDevice.", "Device."))
                for p in huawei.parameters
            ),
        )
    elif mode == "cwmp_version":
        inform = replace(huawei, version="1.2")
    else:
        inform = replace(
            huawei,
            parameters=huawei.parameters
            + (Parameter("Device.DeviceInfo.SoftwareVersion", "OTHER", "xsd:string"),),
        )
    assert ProfileInventoryNormalizer().normalize(inform, NOW).profile_id == (
        "generic-device-info"
    )


@pytest.mark.parametrize("model", ["EG8145X6-10", "OTHER-MODEL", "", "x" * 257])
def test_reported_model_is_not_silently_replaced(huawei: Inform, model: str) -> None:
    inform = replace(
        huawei,
        parameters=huawei.parameters
        + (
            Parameter(
                "InternetGatewayDevice.DeviceInfo.ModelName", model, "xsd:string"
            ),
        ),
    )
    result = ProfileInventoryNormalizer().normalize(inform, NOW)
    assert result.model == GenericInventoryNormalizer().normalize(inform, NOW).model
    if model == "OTHER-MODEL":
        assert result.profile_id == "generic-device-info"


def test_conflicts_and_parameter_order_are_preserved(huawei: Inform) -> None:
    inform = replace(
        huawei,
        parameters=huawei.parameters
        + (
            Parameter("Device.DeviceInfo.ModelName", "EG8145X6-10", "xsd:string"),
            Parameter(
                "InternetGatewayDevice.DeviceInfo.ModelName", "OTHER", "xsd:string"
            ),
            Parameter("Device.DeviceInfo.HardwareVersion", "OTHER", "xsd:string"),
        ),
    )
    normalizer = ProfileInventoryNormalizer()
    result = normalizer.normalize(inform, NOW)
    assert result.model == TextObservation("invalid", reason="conflicting_values")
    assert result.hardware == TextObservation("invalid", reason="conflicting_values")
    assert result == normalizer.normalize(
        replace(inform, parameters=tuple(reversed(inform.parameters))), NOW
    )


@dataclass
class SyntheticProfile:
    profile_id: str
    priority: int
    profile_version: str = "1"

    def matches(self, inform: Inform, generic: NormalizedInventory) -> bool:
        return True

    def apply(
        self, inform: Inform, generic: NormalizedInventory
    ) -> NormalizedInventory:
        return replace(generic, profile_id=self.profile_id)


def test_precedence_is_explicit_and_independent_of_registration_order(
    huawei: Inform,
) -> None:
    general = SyntheticProfile("synthetic-general", 10)
    specific = HuaweiEG8145X6Profile()
    for profiles in ((general, specific), (specific, general)):
        assert ProfileInventoryNormalizer(profiles).normalize(
            huawei, NOW
        ).profile_id == (specific.profile_id)
    assert ProfileInventoryNormalizer(()).normalize(huawei, NOW).profile_id == (
        "generic-device-info"
    )


@pytest.mark.parametrize("duplicate", ["id", "priority"])
def test_ambiguous_registry_fails_at_startup(duplicate: str) -> None:
    first = SyntheticProfile("first", 10)
    second = SyntheticProfile(
        "first" if duplicate == "id" else "second",
        10 if duplicate == "priority" else 20,
    )
    with pytest.raises(ValueError, match="unique"):
        ProfileInventoryNormalizer((first, second))


def test_profile_logs_are_correlated_and_exclude_values(
    huawei: Inform, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="acs.cwmp")
    session = CwmpSession(
        uuid4(),
        uuid4(),
        "secret-rpc",
        "1.0",
        "awaiting_empty",
        NOW + timedelta(seconds=60),
    )
    result = ProfileInventoryNormalizer().normalize(huawei, NOW)
    result = replace(
        result,
        model=TextObservation("valid", "SECRET-VALUE", "Device.DeviceInfo.ModelName"),
    )
    record_inventory(session, result)
    data = json.loads(caplog.records[-1].message)
    assert data == {
        "event": "inventory_normalized",
        "session_id": str(session.id),
        "cpe_id": str(session.cpe_id),
        "profile_id": result.profile_id,
        "profile_version": "1",
    }
    for secret in ("SECRET-VALUE", "secret-rpc", huawei.device.serial_number):
        assert secret not in caplog.text


@pytest.mark.asyncio
async def test_inventory_observer_only_runs_after_successful_authorized_commit(
    huawei: Inform,
) -> None:
    observations: list[NormalizedInventory] = []

    def observe(session: CwmpSession, inventory: NormalizedInventory) -> None:
        observations.append(inventory)

    service = InformService(
        MemoryRepository(), ProfileInventoryNormalizer(), on_inventory_updated=observe
    )
    with pytest.raises(AccessDenied):
        await service.receive(huawei, None, principal=ADMIN)
    assert observations == []
    broken = InformService(
        BrokenRepository(), ProfileInventoryNormalizer(), on_inventory_updated=observe
    )
    with pytest.raises(StorageUnavailable):
        await broken.receive(huawei, None, principal=principal_for(huawei))
    assert observations == []
    await service.receive(huawei, None, principal=principal_for(huawei))
    assert len(observations) == 1
    assert observations[0].profile_id == HuaweiEG8145X6Profile.profile_id
