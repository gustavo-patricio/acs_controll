"""Scoped query evidence preserves useful types and removes network identities."""

import json
import socket
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from acs.cwmp.compatibility.queries import NAMES, TARGET, HuaweiQueryPolicy
from acs.cwmp.compatibility.query_evidence import verify_query_evidence
from acs.cwmp.soap.queries import parse_query_reply
from acs.domain.cpe.inform import Parameter
from acs.domain.operations.evidence import InvalidEvidence
from acs.domain.operations.query import ParameterInfo, QueryKind, QueryReply
from acs.infrastructure.storage.evidence_cli import main
from query_support import reply_xml

WAN = "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1."
OPTICAL = "InternetGatewayDevice.WANDevice.1.X_GponInterafceConfig."


@pytest.mark.parametrize("kind", ["get_parameter_names", "get_parameter_values"])
def test_device_info_and_fault_roundtrip(kind: QueryKind) -> None:
    policy = HuaweiQueryPolicy()
    plan = policy.plan(TARGET)
    for fault in (None, 9005):
        reply = parse_query_reply(reply_xml("private-rpc", kind, fault=fault))
        payload = policy.evidence(reply, plan, kind, datetime.now(UTC))
        evidence = verify_query_evidence(payload.encode())
        assert "private-rpc" not in payload and "PRIVATE-FAULT" not in payload
        parsed = parse_query_reply(evidence.response.xml.encode())
        assert parsed.fault_code == fault
        assert evidence.manifest.partial


def test_discovery_filters_names_without_pretending_complete() -> None:
    policy = HuaweiQueryPolicy()
    plan = policy.plan(TARGET, "discovery", WAN)
    reply = QueryReply(
        "id",
        "get_parameter_names",
        names=(
            ParameterInfo(WAN + "ExternalIPAddress", False),
            ParameterInfo(WAN + "Password", True),
        ),
    )
    payload = policy.evidence(reply, plan, "get_parameter_names", datetime.now(UTC))
    evidence = verify_query_evidence(payload.encode())
    assert "Password" not in payload
    assert evidence.manifest.received_count == 2
    assert len(parse_query_reply(evidence.response.xml.encode()).names) == 1


def test_wan_substitution_and_invalid_omission() -> None:
    policy = HuaweiQueryPolicy()
    suffixes = (
        "TransportType",
        "X_HW_SERVICELIST",
        "ExternalIPAddress",
        "DNSServers",
        "MACAddress",
        "X_HW_IPv6.IPv6Address.1.IPAddress",
        "ConnectionStatus",
    )
    names = tuple(WAN + suffix for suffix in suffixes)
    plan = policy.plan(TARGET, "wan", WAN, names)
    values = (
        "PPPoE",
        "INTERNET",
        "198.51.100.10",
        "198.51.100.10,2001:db8:123::53",
        "02:ab:cd:12:34:56",
        "2001:db8:456::1",
        "PRIVATE-INVALID",
    )
    reply = QueryReply(
        "private",
        "get_parameter_values",
        parameters=tuple(
            Parameter(n, v, "xsd:string") for n, v in zip(names, values, strict=True)
        ),
    )
    payload = policy.evidence(reply, plan, "get_parameter_values", datetime.now(UTC))
    evidence = verify_query_evidence(payload.encode())
    for value in values[2:]:
        assert value not in payload
    assert len(evidence.manifest.substituted_names) == 4
    assert evidence.manifest.omitted_names == (WAN + "ConnectionStatus",)
    parameters = {
        p.name: p.value
        for p in parse_query_reply(evidence.response.xml.encode()).parameters
    }
    assert (
        parameters[WAN + "ExternalIPAddress"]
        == parameters[WAN + "DNSServers"].split(",")[0]
    )
    assert parameters[WAN + "MACAddress"] == "02:00:00:00:00:01"


def test_optical_preserves_raw_integer_type_and_no_unit_inference() -> None:
    policy = HuaweiQueryPolicy()
    names = (OPTICAL + "RXPower", OPTICAL + "TXPower")
    plan = policy.plan(TARGET, "optical", OPTICAL, names)
    reply = QueryReply(
        "private",
        "get_parameter_values",
        parameters=(
            Parameter(names[0], "-2114", "xsd:int"),
            Parameter(names[1], "222", "xsd:unsignedInt"),
        ),
    )
    evidence = verify_query_evidence(
        policy.evidence(reply, plan, "get_parameter_values", datetime.now(UTC)).encode()
    )
    parameters = parse_query_reply(evidence.response.xml.encode()).parameters
    assert [(p.value, p.value_type) for p in parameters] == [
        ("-2114", "xsd:int"),
        ("222", "xsd:unsignedInt"),
    ]
    assert evidence.manifest.optical_unit == "unverified"


@pytest.mark.parametrize(
    "case",
    ["xml", "hash", "profile", "unknown", "duplicate", "xxe", "size", "manifest"],
)
def test_tampered_evidence_rejected(case: str) -> None:
    policy = HuaweiQueryPolicy()
    reply = parse_query_reply(reply_xml("id", "get_parameter_values"))
    original = policy.evidence(
        reply, policy.plan(TARGET), "get_parameter_values", datetime.now(UTC)
    )
    data = json.loads(original)
    if case == "xml":
        data["response"]["xml"] = data["response"]["xml"].replace(
            "EG8145X6-10", "OTHER"
        )
    elif case == "hash":
        data["request"]["sha256"] = "0" * 64
    elif case == "profile":
        data["manifest"]["target"]["firmware"] = "UNREVIEWED"
    elif case == "unknown":
        data["unknown"] = True
    elif case == "xxe":
        data["response"]["xml"] = (
            '<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>'
        )
    elif case == "size":
        data["response"]["size_bytes"] += 1
    elif case == "manifest":
        data["manifest"]["substituted_names"] = [NAMES[0]]
    payload = json.dumps(data).encode()
    if case == "duplicate":
        payload = payload.replace(b"{", b'{"format_version":"query-evidence-1",', 1)
    with pytest.raises(InvalidEvidence):
        verify_query_evidence(payload)


def test_verify_query_cli_is_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    policy = HuaweiQueryPolicy()
    reply = parse_query_reply(reply_xml("id", "get_parameter_values"))
    path = tmp_path / "query.json"
    path.write_text(
        policy.evidence(
            reply, policy.plan(TARGET), "get_parameter_values", datetime.now(UTC)
        )
    )

    def no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("Network forbidden")

    monkeypatch.setattr(socket, "socket", no_network)
    monkeypatch.chdir(tmp_path)
    main(["verify-query", str(path)])
    assert '"verified"' in capsys.readouterr().out


def test_invalid_device_info_text_is_omitted() -> None:
    policy = HuaweiQueryPolicy()
    reply = parse_query_reply(reply_xml("id", "get_parameter_values"))
    reply = replace(
        reply,
        parameters=(
            replace(reply.parameters[0], value="PRIVATE" * 100),
            *reply.parameters[1:],
        ),
    )
    payload = policy.evidence(
        reply, policy.plan(TARGET), "get_parameter_values", datetime.now(UTC)
    )
    assert "PRIVATE" not in payload
    assert verify_query_evidence(payload.encode()).manifest.omitted_names == (NAMES[0],)
