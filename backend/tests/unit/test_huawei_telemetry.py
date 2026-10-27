"""Synthetic values and strict firmware boundaries for WAN/optical collection."""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from acs.cwmp.compatibility import huawei_telemetry as telemetry
from acs.cwmp.compatibility.profiles import ProfileInventoryNormalizer
from acs.cwmp.compatibility.queries import V5_PROFILE, V5_TARGET, HuaweiQueryPolicy
from acs.cwmp.soap.inform import BadEnvelope, parse_inform
from acs.cwmp.soap.queries import parse_query_reply
from acs.domain.cpe.inform import Parameter
from acs.domain.cpe.telemetry import TelemetryName, TelemetryState, TelemetryValue
from acs.domain.operations.query import QueryConflict, QueryReply
from query_support import reply_xml

WAN = "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1."
OPTICAL = "InternetGatewayDevice.WANDevice.1.X_GponInterafceConfig."


@pytest.mark.parametrize(
    ("name", "raw", "state", "value"),
    [
        ("ipv4", "192.0.2.10", "valid", ("192.0.2.10",)),
        ("ipv4", "0.0.0.0", "unavailable", None),
        ("ipv4", "not-an-address", "invalid", None),
        ("ipv6", "2001:0db8::1", "valid", ("2001:db8::1",)),
        ("ipv6", "fe80::1%eth0", "invalid", None),
        (
            "dns",
            "192.0.2.53, 2001:db8::53,192.0.2.53",
            "valid",
            ("192.0.2.53", "2001:db8::53"),
        ),
        ("dns", "192.0.2.53,password", "invalid", None),
        ("mac", "02-AA-BB-CC-DD-EE", "valid", "02:aa:bb:cc:dd:ee"),
        ("mac", "ff:ff:ff:ff:ff:ff", "invalid", None),
        ("mac", "00:00:00:00:00:00", "invalid", None),
        ("connection_status", "Connected,Disconnected", "invalid", None),
        ("transport_type", "PPPoE,PPPoE", "invalid", None),
        ("connection_status", "Connected", "valid", "Connected"),
        ("service_list", "INTERNET,TR069", "valid", ("INTERNET", "TR069")),
        ("rx_power", "-2050", "unverified", None),
        ("rx_power", "NaN", "invalid", None),
        ("tx_power", "private-password", "invalid", None),
    ],
)
def test_values(
    name: TelemetryName, raw: str, state: TelemetryState, value: TelemetryValue | None
) -> None:
    result = telemetry.observe(name, Parameter("synthetic.path", raw, "xsd:string"))
    assert (result.state, result.value) == (state, value)
    assert raw not in repr(result)


def test_optical_conversion_requires_verified_scale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(telemetry, "OPTICAL_DBM_SCALE", 0.01)
    result = telemetry.observe(
        "rx_power", Parameter(OPTICAL + "RXPower", "-2050", "xsd:int")
    )
    assert (result.value, result.unit) == (-20.5, "dBm")
    assert (
        telemetry.observe(
            "tx_power", Parameter(OPTICAL + "TXPower", "99999", "xsd:int")
        ).state
        == "invalid"
    )


def test_profile_and_discovery_gate() -> None:
    policy = HuaweiQueryPolicy()
    names = tuple(
        WAN + s
        for s in (
            "X_HW_SERVICELIST",
            "TransportType",
            "ExternalIPAddress",
            "Password",
            "Username",
        )
    )
    plan = policy.plan(V5_TARGET, "wan", WAN, names)
    assert len(plan.names) == 3
    assert not any(name.endswith(("Password", "Username")) for name in plan.names)
    for target, path, available in (
        (replace(V5_TARGET, firmware="unreviewed"), WAN, names),
        (V5_TARGET, WAN, ()),
        (V5_TARGET, WAN.replace("WANPPPConnection", "WANIPConnection"), names),
    ):
        with pytest.raises(QueryConflict):
            policy.plan(target, "wan", path, available)


def test_multiple_ipv6_sources_and_internet_classification() -> None:
    names = tuple(
        WAN + s
        for s in (
            "X_HW_SERVICELIST",
            "TransportType",
            "X_HW_IPv6.IPv6Address.1.IPAddress",
            "X_HW_IPv6.IPv6Address.2.IPAddress",
        )
    )
    plan = HuaweiQueryPolicy().plan(V5_TARGET, "wan", WAN, names)
    reply = QueryReply(
        "test",
        "get_parameter_values",
        parameters=tuple(
            Parameter(n, v, "xsd:string")
            for n, v in zip(
                names,
                ("INTERNET,TR069", "PPPoE", "2001:db8::2", "2001:db8::1"),
                strict=True,
            )
        ),
    )
    result = telemetry.collect(reply, plan, datetime.now(UTC), uuid4())
    ipv6 = next(f for f in result.fields if f.name == "ipv6")
    assert ipv6.value == ("2001:db8::1", "2001:db8::2")
    assert ipv6.source_parameters == names[2:]
    with pytest.raises(QueryConflict, match="not_internet_pppoe"):
        telemetry.collect(
            replace(
                reply,
                parameters=(
                    replace(reply.parameters[0], value="TR069"),
                    *reply.parameters[1:],
                ),
            ),
            plan,
            datetime.now(UTC),
            uuid4(),
        )


@pytest.mark.parametrize(
    ("kind", "value", "valid"),
    [
        ("int", "-2050", True),
        ("unsignedInt", "210", True),
        ("int", "2147483648", False),
        ("unsignedInt", "-1", False),
        ("int", "x", False),
        ("boolean", "true", True),
        ("double", "1.0", False),
    ],
)
def test_typed_response_parser(kind: str, value: str, valid: bool) -> None:
    xml = reply_xml(
        "test", "get_parameter_values", (value,), names=(OPTICAL + "RXPower",)
    ).replace(b'xsi:type="xsd:string"', f'xsi:type="xsd:{kind}"'.encode())
    if valid:
        assert parse_query_reply(xml).parameters[0].value_type == f"xsd:{kind}"
    else:
        with pytest.raises(BadEnvelope):
            parse_query_reply(xml)


def test_v5_fixture_exact_profile() -> None:
    inform = parse_inform(
        (
            Path(__file__).parents[1]
            / "fixtures/cwmp/huawei_eg8145v5_v2_v5r021c00s210.xml"
        ).read_bytes()
    )
    normalizer = ProfileInventoryNormalizer()
    result = normalizer.normalize(inform, datetime.now(UTC))
    assert result.profile_id == V5_PROFILE
    assert result.model.value == "EG8145V5-V2"
    changed = replace(
        inform,
        parameters=tuple(
            replace(p, value="other-firmware")
            if p.name.endswith("SoftwareVersion")
            else p
            for p in inform.parameters
        ),
    )
    assert (
        normalizer.normalize(changed, datetime.now(UTC)).profile_id
        == "generic-device-info"
    )
