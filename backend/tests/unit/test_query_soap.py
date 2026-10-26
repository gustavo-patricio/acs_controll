"""Read serializers, strict response parsing and namespace/XXE defenses."""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4
from xml.etree import ElementTree as ET

import pytest

from acs.cwmp.compatibility.queries import NAMES, TARGET, HuaweiQueryPolicy
from acs.cwmp.soap.inform import CWMP, SOAP, BadEnvelope, parse_envelope
from acs.cwmp.soap.queries import XSD, parse_query_reply, serialize_query
from acs.domain.operations.query import QueryCommand, QueryConflict, QueryKind
from query_support import reply_xml


@pytest.mark.parametrize("kind", ["names", "values"])
def test_reviewed_lab_query_projection(kind: str) -> None:
    payload = (
        Path(__file__).parents[1]
        / "fixtures"
        / "cwmp"
        / f"huawei_device_info_{kind}.xml"
    ).read_bytes()
    reply = parse_query_reply(payload)
    assert reply.kind == f"get_parameter_{kind}"
    if kind == "names":
        assert {item.name for item in reply.names} == set(NAMES)
        assert all(not item.writable for item in reply.names)
    else:
        inventory = HuaweiQueryPolicy().normalize(
            reply, HuaweiQueryPolicy().plan(TARGET), datetime.now(UTC), uuid4()
        )
        assert inventory.model.value == "EG8145X6-10"
        assert inventory.firmware.value == "V5R022C00S364"
        assert inventory.hardware.value == "343D.D"
        assert inventory.profile_id == HuaweiQueryPolicy().plan(TARGET).profile_id


@pytest.mark.parametrize("kind", ["get_parameter_names", "get_parameter_values"])
def test_query_serialization_and_response(kind: QueryKind) -> None:
    command = QueryCommand(
        uuid4(), "synthetic-rpc", kind, HuaweiQueryPolicy().plan(TARGET)
    )
    rpc_id, rpc = parse_envelope(serialize_query(command))
    assert rpc_id == command.rpc_id
    if kind == "get_parameter_names":
        assert rpc.tag == f"{{{CWMP}}}GetParameterNames"
        assert rpc.findtext("ParameterPath") == "InternetGatewayDevice.DeviceInfo."
        assert rpc.findtext("NextLevel") == "true"
    else:
        assert rpc.tag == f"{{{CWMP}}}GetParameterValues"
        assert tuple(x.text for x in rpc.findall("ParameterNames/string")) == NAMES
    response = parse_query_reply(reply_xml(command.rpc_id, kind))
    assert response.kind == kind and response.rpc_id == command.rpc_id
    if kind == "get_parameter_values":
        assert [p.value for p in response.parameters] == [
            "EG8145X6-10",
            "V5R022C00S364",
            "343D.D",
        ]
    else:
        assert [p.name for p in response.names] == list(NAMES)
        assert all(p.writable is False for p in response.names)


@pytest.mark.parametrize("code", [9001, 9002, 9003, 9004, 9005, 9801])
def test_received_fault_codes_preserved_without_fault_strings(code: int) -> None:
    reply = parse_query_reply(reply_xml("rpc", "get_parameter_values", fault=code))
    assert reply.fault_code == code and reply.kind == "fault"
    assert "PRIVATE" not in repr(reply)


@pytest.mark.parametrize(
    "payload",
    [
        b"<broken>",
        b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>',
        b"x" * 1_048_577,
    ],
)
def test_query_responses_reject_unsafe_xml(payload: bytes) -> None:
    with pytest.raises(BadEnvelope):
        parse_query_reply(payload)


def test_types_are_resolved_by_namespace_not_prefix() -> None:
    payload = reply_xml("rpc", "get_parameter_values")
    assert (
        parse_query_reply(
            payload.replace(b"xsd:", b"t:").replace(b"xmlns:xsd", b"xmlns:t")
        ).kind
        == "get_parameter_values"
    )
    with pytest.raises(BadEnvelope):
        parse_query_reply(payload.replace(XSD.encode(), b"urn:untrusted"))
    with pytest.raises(BadEnvelope):
        parse_query_reply(
            payload.replace(b'xsi:type="xsd:string"', b'xsi:type="xsd:int"')
        )


@pytest.mark.parametrize("kind", ["get_parameter_names", "get_parameter_values"])
def test_duplicate_names_rejected(kind: QueryKind) -> None:
    with pytest.raises(BadEnvelope):
        parse_query_reply(reply_xml("rpc", kind, names=(NAMES[0], NAMES[0], NAMES[2])))


def test_discovery_boolean_and_count_limits() -> None:
    payload = reply_xml("rpc", "get_parameter_names")
    with pytest.raises(BadEnvelope):
        parse_query_reply(payload.replace(b"false", b"maybe"))
    with pytest.raises(BadEnvelope):
        parse_query_reply(
            reply_xml(
                "rpc",
                "get_parameter_names",
                names=tuple(
                    f"InternetGatewayDevice.DeviceInfo.X_{i}" for i in range(129)
                ),
            )
        )
    tree = ET.fromstring(payload)
    assert tree.tag == f"{{{SOAP}}}Envelope"


@pytest.mark.parametrize(
    "field", ["manufacturer", "oui", "product_class", "firmware", "model"]
)
def test_only_reviewed_target_is_eligible(field: str) -> None:
    with pytest.raises(QueryConflict):
        HuaweiQueryPolicy().plan(replace(TARGET, **{field: "unreviewed"}))


@pytest.mark.parametrize(
    "values", [("OTHER", "V5R022C00S364", "343D.D"), ("EG8145X6-10", "OTHER", "343D.D")]
)
def test_response_profile_changes_use_generic_provenance(
    values: tuple[str, str, str],
) -> None:
    policy = HuaweiQueryPolicy()
    operation_id = uuid4()
    result = policy.normalize(
        parse_query_reply(reply_xml("rpc", "get_parameter_values", values=values)),
        policy.plan(TARGET),
        datetime.now(UTC),
        operation_id,
    )
    assert result.profile_id == "generic-device-info"
    assert (
        result.operation_id == operation_id
        and result.source_method == "GetParameterValues"
    )
