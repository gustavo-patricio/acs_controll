"""Synthetic CPE client; deliberately independent of server parsers and services."""

from datetime import UTC, datetime
from uuid import uuid4
from xml.etree.ElementTree import Element, ParseError, SubElement, tostring

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import fromstring
from httpx import AsyncClient

SOAP = "http://schemas.xmlsoap.org/soap/envelope/"
CWMP = "urn:dslforum-org:cwmp-1-0"
ENC = "http://schemas.xmlsoap.org/soap/encoding/"
COOKIE = "acs_cwmp_session"


def build_inform(serial: str, rpc_id: str) -> bytes:
    envelope = Element(f"{{{SOAP}}}Envelope")
    header = SubElement(envelope, f"{{{SOAP}}}Header")
    SubElement(
        header, f"{{{CWMP}}}ID", {f"{{{SOAP}}}mustUnderstand": "1"}
    ).text = rpc_id
    inform = SubElement(SubElement(envelope, f"{{{SOAP}}}Body"), f"{{{CWMP}}}Inform")
    device = SubElement(inform, "DeviceId")
    for name, value in (
        ("Manufacturer", "Example"),
        ("OUI", "A1B2C3"),
        ("ProductClass", "SIMULATOR"),
        ("SerialNumber", serial),
    ):
        SubElement(device, name).text = value
    event = SubElement(inform, "Event", {f"{{{ENC}}}arrayType": "cwmp:EventStruct[1]"})
    # Bind the prefix used in arrayType's QName value, independently of serialization.
    event.set("xmlns:cwmp", CWMP)
    item = SubElement(event, "EventStruct")
    SubElement(item, "EventCode").text = "1 BOOT"
    SubElement(item, "CommandKey")
    SubElement(inform, "MaxEnvelopes").text = "1"
    SubElement(inform, "CurrentTime").text = datetime.now(UTC).isoformat()
    SubElement(inform, "RetryCount").text = "0"
    parameters = SubElement(
        inform, "ParameterList", {f"{{{ENC}}}arrayType": "cwmp:ParameterValueStruct[0]"}
    )
    parameters.set("xmlns:cwmp", CWMP)
    return tostring(envelope, encoding="utf-8", xml_declaration=True)


def validate_response(payload: bytes, rpc_id: str) -> None:
    try:
        root = fromstring(
            payload, forbid_dtd=True, forbid_entities=True, forbid_external=True
        )
    except (ParseError, DefusedXmlException):
        raise AssertionError("InformResponse is not safe, valid XML") from None
    assert root.tag == f"{{{SOAP}}}Envelope", "Expected SOAP 1.1 Envelope"
    assert root.findtext(f"{{{SOAP}}}Header/{{{CWMP}}}ID") == rpc_id, "CWMP ID mismatch"
    body = root.find(f"{{{SOAP}}}Body")
    assert body is not None and len(body) == 1, "Expected exactly one SOAP response"
    response = body[0]
    assert response.tag == f"{{{CWMP}}}InformResponse", (
        "Expected CWMP 1.0 InformResponse"
    )
    assert response.findtext("MaxEnvelopes") == "1", "Expected MaxEnvelopes=1"


async def simulate_session(client: AsyncClient, url: str, serial: str) -> None:
    """Use the caller's cookie jar; never print response bodies or cookies."""
    rpc_id = f"sim-{uuid4().hex}"
    response = await client.post(
        url, content=build_inform(serial, rpc_id), headers={"Content-Type": "text/xml"}
    )
    assert response.status_code == 200, f"Inform returned HTTP {response.status_code}"
    assert response.headers.get("content-type", "").split(";", 1)[0] == "text/xml"
    validate_response(response.content, rpc_id)
    assert COOKIE in client.cookies, "Missing session cookie"
    response = await client.post(url, content=b"")
    assert response.status_code == 204, (
        f"Empty POST returned HTTP {response.status_code}"
    )
    assert response.content == b"", "Expected an empty completion response"
    assert COOKIE not in client.cookies, "Session cookie was not cleared"
