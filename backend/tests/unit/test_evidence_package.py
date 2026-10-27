"""Portable evidence must remain anonymous, bounded and independently verifiable."""

import hashlib
import json
import os
import socket
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

import pytest

from acs.cwmp.compatibility.evidence_package import (
    build_package,
    manifest_bytes,
    verify_package,
)
from acs.cwmp.soap.inform import MAX_BODY
from acs.domain.operations.evidence import InvalidEvidence
from acs.infrastructure.storage import evidence_package as storage
from acs.infrastructure.storage.evidence_cli import main

FIXTURES = Path(__file__).parents[1] / "fixtures/cwmp"


def test_existing_capture_roundtrip_and_declared_time() -> None:
    package = build_package(
        (FIXTURES / "huawei_eg8145x6_10_v5r022c00s364.xml").read_bytes()
    )
    manifest = package.manifest
    assert manifest.device.product_class == "EG8145X6-10"
    assert manifest.device.firmware == "V5R022C00S364"
    assert manifest.device.model is None
    assert manifest.received_at == "2026-10-25T19:22:42.405471+00:00"
    assert (
        manifest.timestamp_source == "input-declared"
        and not manifest.authenticity_verified
    )
    assert manifest.partial and manifest.retained_parameters == 2
    assert manifest.artifact.size_bytes == len(package.xml)
    assert manifest.artifact.sha256 == hashlib.sha256(package.xml).hexdigest()
    assert verify_package(package.xml, manifest_bytes(manifest)) == manifest
    assert build_package(package.xml) == package


def test_raw_input_is_reconstructed_and_unknown_time_is_not_invented() -> None:
    package = build_package((FIXTURES / "inform_device_inventory.xml").read_bytes())
    assert package.manifest.received_at is None
    assert package.manifest.timestamp_source == "unknown"
    assert package.manifest.retained_parameters == 3
    assert package.manifest.xml_types == "reconstructed-as-xsd-string"
    for marker in (
        b"SYNTHETIC-SECRET",
        b"LAB000001",
        b"ManagementServer.Password",
        b"synthetic-inventory-001",
    ):
        assert marker not in package.xml + manifest_bytes(package.manifest)
    assert b"received_at=unknown" in package.xml
    assert (
        verify_package(package.xml, manifest_bytes(package.manifest))
        == package.manifest
    )


@pytest.mark.parametrize(
    "payload",
    [
        b"<broken>",
        b"x" * (MAX_BODY + 1),
        b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>',
    ],
)
def test_invalid_xml_and_xxe(payload: bytes) -> None:
    with pytest.raises(InvalidEvidence) as error:
        build_package(payload)
    assert not str(error.value)


@pytest.mark.parametrize(
    "alteration",
    [
        "hash",
        "metadata",
        "version",
        "extra",
        "duplicate",
        "boolean",
        "oversized",
        "unknown_xml",
    ],
)
def test_tampering_rejected(inform_xml: bytes, alteration: str) -> None:
    package = build_package(inform_xml)
    document = asdict(package.manifest)
    xml = package.xml
    if alteration == "hash":
        document["artifact"]["sha256"] = "0" * 64
    elif alteration == "metadata":
        document["device"]["firmware"] = "fabricated"
    elif alteration == "version":
        document["format_version"] = "2"
    elif alteration == "extra":
        document["new_field"] = "unknown"
    elif alteration == "boolean":
        document["partial"] = 1
    elif alteration == "unknown_xml":
        xml = xml.replace(b"<soap:Header>", b'<soap:Header injected="private">')
        document["artifact"]["sha256"] = hashlib.sha256(xml).hexdigest()
        document["artifact"]["size_bytes"] = len(xml)
    manifest = json.dumps(document).encode()
    if alteration == "duplicate":
        manifest = manifest.replace(b"{", b'{"partial": true,', 1)
    if alteration == "oversized":
        manifest = b" " * 16_385
    with pytest.raises(InvalidEvidence):
        verify_package(xml, manifest)


def test_private_publication_and_no_overwrite(
    tmp_path: Path, inform_xml: bytes
) -> None:
    output = tmp_path / "package"
    package = build_package(inform_xml)
    storage.publish_package(output, package)
    assert storage.load_package(output) == package.manifest
    assert output.stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in output.iterdir())
    before = (output / "manifest.json").read_bytes()
    with pytest.raises(FileExistsError):
        storage.publish_package(output, package)
    assert (output / "manifest.json").read_bytes() == before


def test_concurrent_publication_has_one_winner(
    tmp_path: Path, inform_xml: bytes
) -> None:
    package = build_package(inform_xml)
    output = tmp_path / "package"

    def publish(_: int) -> bool:
        try:
            storage.publish_package(output, package)
            return True
        except FileExistsError:
            return False

    with ThreadPoolExecutor(max_workers=2) as workers:
        assert sorted(workers.map(publish, (0, 1))) == [False, True]
    assert storage.load_package(output) == package.manifest


def test_write_failure_cleans_incomplete_output(
    tmp_path: Path, inform_xml: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = os.fsync
    calls = 0

    def fail(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("private error")
        original(descriptor)

    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(OSError):
        storage.publish_package(tmp_path / "package", build_package(inform_xml))
    assert not (tmp_path / "package").exists()


@pytest.mark.parametrize(
    "change", ["missing_xml", "missing_manifest", "extra", "permissions", "symlink"]
)
def test_incomplete_or_unsafe_storage_rejected(
    tmp_path: Path, inform_xml: bytes, change: str
) -> None:
    output = tmp_path / "package"
    storage.publish_package(output, build_package(inform_xml))
    if change == "missing_xml":
        (output / "inform.xml").unlink()
    elif change == "missing_manifest":
        (output / "manifest.json").unlink()
    elif change == "extra":
        (output / "unexpected").touch()
    elif change == "permissions":
        (output / "inform.xml").chmod(0o644)
    else:
        (output / "inform.xml").unlink()
        (output / "inform.xml").symlink_to(FIXTURES / "inform.xml")
    with pytest.raises((InvalidEvidence, OSError)):
        storage.load_package(output)


def test_destination_permissions_and_symlink(tmp_path: Path, inform_xml: bytes) -> None:
    package = build_package(inform_xml)
    public = tmp_path / "public"
    public.mkdir(mode=0o755)
    with pytest.raises(InvalidEvidence):
        storage.publish_package(public / "package", package)
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(FileExistsError):
        storage.publish_package(link, package)
    assert not list(target.iterdir())
    with pytest.raises(OSError):
        storage.publish_package(link / "package", package)


def test_cli_offline_and_safe_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("Network forbidden")

    monkeypatch.setattr(socket, "socket", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.delenv("ACS_DATABASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "package"
    main(
        [
            "create",
            "--input",
            str(FIXTURES / "inform_device_inventory.xml"),
            "--output",
            str(output),
        ]
    )
    main(["verify", str(output)])
    messages = capsys.readouterr().out
    assert '"created"' in messages and '"verified"' in messages
    assert "SYNTHETIC" not in messages and str(output) not in messages
    with pytest.raises(SystemExit, match="evidence_already_exists"):
        main(
            ["create", "--input", str(FIXTURES / "inform.xml"), "--output", str(output)]
        )
    (output / "inform.xml").write_bytes(b"private-invalid-xml")
    with pytest.raises(SystemExit, match="evidence_invalid_or_unavailable"):
        main(["verify", str(output)])
    assert "private" not in capsys.readouterr().out


def test_fifo_input_does_not_block(tmp_path: Path) -> None:
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(InvalidEvidence):
        storage.read_bounded(fifo, MAX_BODY)
