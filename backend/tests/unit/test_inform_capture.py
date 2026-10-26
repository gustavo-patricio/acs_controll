"""Evidence privacy, selection, lifetime and atomic publication."""

import asyncio
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from stat import S_IMODE
from uuid import uuid4

import pytest
from pydantic import PostgresDsn, ValidationError

from acs.cwmp.compatibility.evidence import anonymized_inform
from acs.cwmp.compatibility.generic import GenericInventoryNormalizer
from acs.cwmp.soap.inform import parse_inform
from acs.domain.cpe.inform import Event, Parameter
from acs.domain.events.session import CwmpSession
from acs.infrastructure.storage import inform_capture as capture_module
from acs.infrastructure.storage.inform_capture import FileInformCapture
from acs.settings import Settings

NOW = datetime(2026, 10, 25, tzinfo=UTC)


def test_reviewed_huawei_capture_preserves_observed_paths_without_inventing_model() -> (
    None
):
    fixture = (
        Path(__file__).parents[1] / "fixtures/cwmp/huawei_eg8145x6_10_v5r022c00s364.xml"
    )
    inform = parse_inform(fixture.read_bytes())
    assert inform.device.serial_number == "ANONYMIZED0001"
    assert inform.device.product_class == "EG8145X6-10"
    assert inform.device.oui == "00259E"
    result = GenericInventoryNormalizer().normalize(inform, NOW)
    assert result.model.state == "not_reported" and result.model.value is None
    assert result.firmware.value == "V5R022C00S364"
    assert result.hardware.value == "343D.D"
    assert {parameter.name for parameter in inform.parameters} == {
        "InternetGatewayDevice.DeviceInfo.SoftwareVersion",
        "InternetGatewayDevice.DeviceInfo.HardwareVersion",
    }


def session() -> CwmpSession:
    return CwmpSession(
        uuid4(), uuid4(), "rpc", "1.0", "awaiting_empty", NOW + timedelta(seconds=60)
    )


@pytest.mark.parametrize("root", ["Device", "InternetGatewayDevice"])
def test_capture_reconstructs_parseable_inventory_without_secrets(
    inform_xml: bytes, root: str
) -> None:
    secret = "DO-NOT-EXPORT"
    original = parse_inform(inform_xml)
    original = replace(
        original,
        rpc_id=secret,
        device=replace(original.device, serial_number=secret),
        events=(Event(secret, secret),),
        parameters=(
            Parameter(f"{root}.DeviceInfo.ModelName", "MODEL<&>", secret),
            Parameter(f"{root}.DeviceInfo.SoftwareVersion", "  FW-1  ", "string"),
            Parameter(f"{root}.DeviceInfo.HardwareVersion", "HW-1", "string"),
            Parameter(f"{root}.DeviceInfo.SerialNumber", secret, "string"),
            Parameter(f"{root}.ManagementServer.Password", secret, "string"),
            Parameter(f"{root}.WAN.PPP.Password", secret, "string"),
            Parameter(f"{root}.X_SECRET_{secret}", secret, "string"),
            Parameter(f"{root}.DeviceInfo.ModelName.Extra", secret, "string"),
        ),
    )
    payload = anonymized_inform(original, NOW)
    assert secret.encode() not in payload
    parsed = parse_inform(payload)
    assert parsed.device == replace(original.device, serial_number="ANONYMIZED0001")
    assert [parameter.name for parameter in parsed.parameters] == [
        parameter.name for parameter in original.parameters[:3]
    ]
    assert [parameter.value for parameter in parsed.parameters] == [
        "MODEL<&>",
        "  FW-1  ",
        "HW-1",
    ]
    assert all(parameter.value_type == "xsd:string" for parameter in parsed.parameters)
    assert b"parameters_received=8; parameters_retained=3" in payload
    assert b"source=accepted-inform" in payload


def test_capture_drops_unbounded_or_control_values_without_inventing_model(
    inform_xml: bytes,
) -> None:
    original = replace(
        parse_inform(inform_xml),
        parameters=(
            Parameter("Device.DeviceInfo.SoftwareVersion", "x" * 257, "string"),
            Parameter("Device.DeviceInfo.HardwareVersion", "secret\nvalue", "string"),
        ),
    )
    payload = anonymized_inform(original, NOW)
    assert parse_inform(payload).parameters == ()
    assert b"secret" not in payload and b"x" * 257 not in payload
    assert b"parameters_received=2; parameters_retained=0" in payload


@pytest.mark.asyncio
async def test_capture_is_targeted_private_and_only_published_once(
    tmp_path: Path, inform_xml: bytes, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="acs.cwmp")
    target = session()
    output = tmp_path / "private" / "inform.xml"
    first = FileInformCapture(target.cpe_id, output, 300, anonymized_inform)
    second = FileInformCapture(target.cpe_id, output, 300, anonymized_inform)
    inform = parse_inform(inform_xml)
    await first.capture(inform, session(), NOW)
    assert not output.exists() and not first.done
    await asyncio.gather(
        first.capture(inform, target, NOW),
        second.capture(inform, target, NOW),
    )
    saved = output.read_bytes()
    await first.capture(replace(inform, parameters=()), target, NOW)
    assert output.read_bytes() == saved
    assert S_IMODE(output.stat().st_mode) == 0o600
    assert S_IMODE(output.parent.stat().st_mode) == 0o700
    assert list(output.parent.iterdir()) == [output]
    assert '"outcome":"saved"' in caplog.text
    assert '"outcome":"already_exists"' in caplog.text
    assert str(target.id) in caplog.text
    for secret in ("SYNTHETIC-SECRET", "LAB000001", str(output)):
        assert secret not in caplog.text


@pytest.mark.asyncio
async def test_expired_capture_never_serializes(
    tmp_path: Path, inform_xml: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = 100.0
    monkeypatch.setattr(capture_module, "monotonic", lambda: now)
    target = session()
    output = tmp_path / "private" / "inform.xml"
    capture = FileInformCapture(target.cpe_id, output, 1, anonymized_inform)
    now = 101.0
    await capture.capture(parse_inform(inform_xml), target, NOW)
    assert not output.exists() and capture.done


@pytest.mark.asyncio
async def test_existing_file_or_symlink_is_never_overwritten(
    tmp_path: Path, inform_xml: bytes
) -> None:
    target = session()
    output = tmp_path / "private" / "inform.xml"
    capture = FileInformCapture(target.cpe_id, output, 300, anonymized_inform)
    existing = tmp_path / "existing.xml"
    existing.write_bytes(b"existing evidence")
    output.symlink_to(existing)
    await capture.capture(parse_inform(inform_xml), target, NOW)
    assert existing.read_bytes() == b"existing evidence"
    assert output.is_symlink()


@pytest.mark.asyncio
async def test_storage_failure_does_not_leak_details_or_raise(
    tmp_path: Path,
    inform_xml: bytes,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="acs.cwmp")
    target = session()
    capture = FileInformCapture(
        target.cpe_id, tmp_path / "private" / "inform.xml", 300, anonymized_inform
    )

    def fail(payload: bytes) -> None:
        raise OSError("DO-NOT-LOG-exception-path")

    monkeypatch.setattr(capture, "_publish", fail)
    await capture.capture(parse_inform(inform_xml), target, NOW)
    assert '"outcome":"failed"' in caplog.text
    assert "DO-NOT-LOG" not in caplog.text
    assert not capture.output.exists()


def test_capture_rejects_public_directory(tmp_path: Path) -> None:
    directory = tmp_path / "public"
    directory.mkdir(mode=0o755)
    with pytest.raises(ValueError, match="0700"):
        FileInformCapture(uuid4(), directory / "inform.xml", 300, anonymized_inform)


@pytest.mark.parametrize(
    "overrides",
    [
        {"cwmp_capture_cpe_id": None},
        {"cwmp_capture_output": None},
        {"cwmp_capture_output": "relative.xml"},
        {"environment": "production"},
        {"environment": "staging"},
        {"database_echo": True},
        {"cwmp_capture_seconds": 0},
        {"cwmp_capture_seconds": 601},
    ],
)
def test_capture_settings_reject_unsafe_configuration(
    tmp_path: Path, overrides: dict[str, object]
) -> None:
    values: dict[str, object] = {
        "database_url": PostgresDsn("postgresql+psycopg://test:local@localhost/test"),
        "cwmp_capture_cpe_id": uuid4(),
        "cwmp_capture_output": tmp_path / "inform.xml",
        **overrides,
    }
    with pytest.raises(ValidationError):
        Settings.model_validate(values)
