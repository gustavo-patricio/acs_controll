"""Generic mapping, ambiguity handling and normalization orchestration."""

from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from acs.application.services.inform import InformService
from acs.cwmp.compatibility.generic import GenericInventoryNormalizer
from acs.cwmp.soap.inform import parse_inform
from acs.domain.cpe.inform import Inform, Parameter
from acs.domain.cpe.inventory import InvalidReason, NormalizedInventory, TextObservation
from acs.domain.security import AccessDenied
from auth_support import ADMIN, principal_for
from contract.test_cwmp import MemoryRepository

NOW = datetime(2026, 10, 24, tzinfo=UTC)
FIXTURE = Path(__file__).parents[1] / "fixtures/cwmp/inform_device_inventory.xml"


@pytest.mark.parametrize("root", ["Device", "InternetGatewayDevice"])
def test_generic_roots_have_the_same_canonical_values(root: str) -> None:
    payload = FIXTURE.read_bytes().replace(b"Device.", f"{root}.".encode())
    inform = parse_inform(payload)
    normalized = GenericInventoryNormalizer().normalize(inform, NOW)
    assert normalized.profile_id == "generic-device-info"
    assert normalized.profile_version == "1"
    assert normalized.received_at == NOW
    for observation, value, suffix in (
        (normalized.model, "TEST-MODEL", "ModelName"),
        (normalized.firmware, "LAB-1.0", "SoftwareVersion"),
        (normalized.hardware, "TEST-HW", "HardwareVersion"),
    ):
        assert observation.state == "valid"
        assert observation.value == value
        assert observation.source_parameter == f"{root}.DeviceInfo.{suffix}"
    different_device = replace(
        inform, device=replace(inform.device, manufacturer="Another Lab", oui="D4E5F6")
    )
    assert GenericInventoryNormalizer().normalize(different_device, NOW) == normalized


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("", "empty"),
        ("   ", "empty"),
        ("x" * 257, "too_long"),
        ("line\nbreak", "control_character"),
        ("\x00", "control_character"),
    ],
)
def test_invalid_values_are_not_exported(
    inform_xml: bytes,
    value: str,
    reason: InvalidReason,
) -> None:
    inform = replace(
        parse_inform(inform_xml),
        parameters=(
            Parameter("Device.DeviceInfo.SoftwareVersion", value, "xsd:string"),
        ),
    )
    result = GenericInventoryNormalizer().normalize(inform, NOW)
    assert result.firmware.state == "invalid"
    assert result.firmware.reason == reason
    assert result.firmware.value is None
    assert result.model == TextObservation("not_reported")
    assert result.hardware == TextObservation("not_reported")


@pytest.mark.parametrize("duplicate_path", [False, True])
def test_conflicting_values_are_rejected_independently_of_order(
    inform_xml: bytes,
    duplicate_path: bool,
) -> None:
    first = Parameter("Device.DeviceInfo.SoftwareVersion", "A", "xsd:string")
    second = Parameter(
        first.name
        if duplicate_path
        else "InternetGatewayDevice.DeviceInfo.SoftwareVersion",
        "B",
        "xsd:string",
    )
    inform = replace(parse_inform(inform_xml), parameters=(first, second))
    normalizer = GenericInventoryNormalizer()
    result = normalizer.normalize(inform, NOW)
    assert result == normalizer.normalize(
        replace(inform, parameters=(second, first)), NOW
    )
    assert result.firmware == TextObservation("invalid", reason="conflicting_values")


def test_equal_values_use_a_deterministic_source(inform_xml: bytes) -> None:
    inform = replace(
        parse_inform(inform_xml),
        parameters=(
            Parameter(
                "InternetGatewayDevice.DeviceInfo.ModelName", " MODEL ", "xsd:string"
            ),
            Parameter("Device.DeviceInfo.ModelName", "MODEL", "xsd:string"),
        ),
    )
    result = GenericInventoryNormalizer().normalize(inform, NOW)
    assert result.model == TextObservation(
        "valid", "MODEL", "Device.DeviceInfo.ModelName"
    )


def test_invalid_alias_does_not_silently_fall_back(inform_xml: bytes) -> None:
    inform = replace(
        parse_inform(inform_xml),
        parameters=(
            Parameter(
                "InternetGatewayDevice.DeviceInfo.ModelName", "MODEL", "xsd:string"
            ),
            Parameter("Device.DeviceInfo.ModelName", "", "xsd:string"),
        ),
    )
    result = GenericInventoryNormalizer().normalize(inform, NOW)
    assert result.model.state == "invalid"
    assert result.model.value is None


def test_only_allowlisted_paths_enter_inventory(inform_xml: bytes) -> None:
    secret = "SYNTHETIC-SECRET-DO-NOT-LOG"
    inform = replace(
        parse_inform(inform_xml),
        parameters=tuple(
            Parameter(path, secret, "xsd:string")
            for path in (
                "Device.ManagementServer.Password",
                "Device.DeviceInfo.X_LAB_Secret",
                "Device.DeviceInfo.ModelName.Extra",
                "Device.Other.ModelName",
            )
        ),
    )
    result = GenericInventoryNormalizer().normalize(inform, NOW)
    assert all(
        field.state == "not_reported"
        for field in (result.model, result.firmware, result.hardware)
    )
    assert secret not in repr(result) and secret not in str(asdict(result))
    assert "TEST-MODEL" not in repr(
        GenericInventoryNormalizer().normalize(parse_inform(FIXTURE.read_bytes()), NOW)
    )


def test_invalid_observation_cannot_carry_a_value() -> None:
    with pytest.raises(ValueError, match="Only valid"):
        TextObservation("invalid", "do-not-store", reason="empty")


@pytest.mark.asyncio
async def test_service_normalizes_after_authorization_and_passes_result(
    inform_xml: bytes,
) -> None:
    class ObservedNormalizer(GenericInventoryNormalizer):
        calls = 0

        def normalize(
            self, inform: Inform, received_at: datetime
        ) -> NormalizedInventory:
            self.calls += 1
            return super().normalize(inform, received_at)

    repository = MemoryRepository()
    normalizer = ObservedNormalizer()
    service = InformService(repository, normalizer)
    inform = parse_inform(inform_xml)
    with pytest.raises(AccessDenied):
        await service.receive(inform, None, principal=ADMIN)
    assert normalizer.calls == 0 and repository.inventory is None
    before = datetime.now(UTC)
    await service.receive(inform, None, principal=principal_for(inform))
    result = repository.inventory
    assert result is not None
    assert before <= result.received_at <= datetime.now(UTC)
    assert result.firmware.value == "LAB-1.0"
    assert normalizer.calls == 1
