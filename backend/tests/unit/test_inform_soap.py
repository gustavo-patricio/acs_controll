"""SOAP extraction, serialization and hostile-input checks."""

from datetime import timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from acs.cwmp.soap.inform import (
    CWMP,
    MAX_BODY,
    SOAP,
    BadEnvelope,
    FaultCode,
    RpcFault,
    parse_inform,
    serialize_fault,
    serialize_inform_response,
)


def test_extracts_inform(inform_xml: bytes) -> None:
    inform = parse_inform(inform_xml)
    assert (inform.rpc_id, inform.version) == ("lab-inform-001", "1.0")
    assert (
        inform.device.manufacturer,
        inform.device.oui,
        inform.device.product_class,
        inform.device.serial_number,
    ) == ("Huawei", "A1B2C3", "LAB-ONT", "LAB000001")
    assert [e.code for e in inform.events] == ["0 BOOTSTRAP", "1 BOOT"]
    assert inform.events[1].command_key == ""
    assert (inform.max_envelopes, inform.retry_count) == (1, 0)
    assert inform.current_time.utcoffset() == timedelta(hours=-4)
    assert inform.parameters[2].value == "120"
    assert inform.parameters[2].value_type == "xsd:unsignedInt"
    assert "SYNTHETIC-SECRET" not in repr(inform)


def test_prefixes_are_not_semantic(inform_xml: bytes) -> None:
    assert parse_inform(
        inform_xml.replace(b"cwmp:", b"c:").replace(b"xmlns:cwmp=", b"xmlns:c=")
    ) == parse_inform(inform_xml)


def test_response_correlates_id_and_escapes_xml() -> None:
    root = ET.fromstring(serialize_inform_response("id<&>"))
    assert root.tag == f"{{{SOAP}}}Envelope"
    identifier = root.find(f"{{{SOAP}}}Header/{{{CWMP}}}ID")
    assert identifier is not None and identifier.text == "id<&>"
    assert identifier.get(f"{{{SOAP}}}mustUnderstand") == "1"
    assert root.findtext(f"{{{SOAP}}}Body/{{{CWMP}}}InformResponse/MaxEnvelopes") == "1"


@pytest.mark.parametrize("filename", ["malformed.xml", "xxe.xml"])
def test_rejects_hostile_fixtures(filename: str) -> None:
    payload = (Path(__file__).parents[1] / "fixtures/cwmp" / filename).read_bytes()
    with pytest.raises(BadEnvelope):
        parse_inform(payload)


@pytest.mark.parametrize(
    "payload",
    [
        b'<!DOCTYPE x SYSTEM "https://example.invalid/external.dtd"><x/>',
        b'<!DOCTYPE x [<!ENTITY a "abc"><!ENTITY b "&a;&a;">]><x>&b;</x>',
        b"<x>" * 40 + b"</x>" * 40,
        b"x" * (MAX_BODY + 1),
    ],
)
def test_rejects_dtd_entities_depth_and_size(payload: bytes) -> None:
    with pytest.raises(BadEnvelope):
        parse_inform(payload)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (b"<RetryCount>0</RetryCount>", b"<RetryCount>-1</RetryCount>"),
        (b"<RetryCount>0</RetryCount>", b"<RetryCount>4294967296</RetryCount>"),
        (b"<RetryCount>0</RetryCount>", b""),
        (b"<OUI>A1B2C3</OUI>", b"<OUI>invalid</OUI>"),
        (b"2026-09-19T10:00:00-04:00", b"not-a-time"),
        (b"EventStruct[2]", b"EventStruct[1]"),
    ],
)
def test_invalid_arguments_use_defined_8003(
    inform_xml: bytes, old: bytes, new: bytes
) -> None:
    with pytest.raises(RpcFault) as error:
        parse_inform(inform_xml.replace(old, new))
    assert error.value.rpc_id == "lab-inform-001"
    assert error.value.code == FaultCode.INVALID_ARGUMENTS


def test_rejects_unapproved_namespace(inform_xml: bytes) -> None:
    with pytest.raises(BadEnvelope):
        parse_inform(inform_xml.replace(b"cwmp-1-0", b"cwmp-1-4"))


def test_unsupported_method_fault(inform_xml: bytes) -> None:
    with pytest.raises(RpcFault) as error:
        parse_inform(inform_xml.replace(b"cwmp:Inform", b"cwmp:UnknownMethod"))
    assert error.value.code == FaultCode.METHOD_NOT_SUPPORTED
    root = ET.fromstring(serialize_fault(error.value.rpc_id, error.value.code))
    assert (
        root.findtext(
            f"{{{SOAP}}}Body/{{{SOAP}}}Fault/detail/{{{CWMP}}}Fault/FaultCode"
        )
        == "8000"
    )
