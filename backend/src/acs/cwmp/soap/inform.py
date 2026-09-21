"""Bounded SOAP 1.1 / CWMP 1.0 parsing and serialization."""

import re
from datetime import datetime
from enum import IntEnum
from xml.etree import ElementTree as ET

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import fromstring

from acs.domain.cpe.inform import DeviceId, Event, Inform, Parameter

SOAP = "http://schemas.xmlsoap.org/soap/envelope/"
CWMP = "urn:dslforum-org:cwmp-1-0"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
ENC = "http://schemas.xmlsoap.org/soap/encoding/"
MAX_BODY = 1_048_576


class BadEnvelope(ValueError):
    """XML cannot be safely interpreted as a supported CWMP envelope."""


class FaultCode(IntEnum):
    METHOD_NOT_SUPPORTED = 8000
    INVALID_ARGUMENTS = 8003


class RpcFault(Exception):
    def __init__(self, rpc_id: str, code: FaultCode) -> None:
        self.rpc_id = rpc_id
        self.code = code


def child(parent: ET.Element, name: str) -> ET.Element:
    found = parent.findall(name)
    if len(found) != 1:
        raise ValueError("Invalid structure")
    return found[0]


def scalar(element: ET.Element, limit: int = 4096, empty: bool = False) -> str:
    value = element.text or ""
    if len(element) or len(value) > limit or (not empty and not value):
        raise ValueError("Invalid scalar")
    return value


def fields(element: ET.Element, names: tuple[str, ...]) -> None:
    if tuple(c.tag for c in element) != names:
        raise ValueError("Invalid arguments")


def unsigned(element: ET.Element) -> int:
    value = scalar(element, 16).strip()
    if not re.fullmatch(r"\+?[0-9]+", value):
        raise ValueError("Invalid unsigned integer")
    result = int(value)
    if result > 4_294_967_295:
        raise ValueError("Invalid unsigned integer")
    return result


def array(element: ET.Element, item: str, limit: int) -> list[ET.Element]:
    result = list(element)
    if len(result) > limit or any(c.tag != item for c in result):
        raise ValueError("Invalid array")
    declared = element.get(f"{{{ENC}}}arrayType")
    if declared is not None:
        match = re.fullmatch(r"[^\[\]]+\[([0-9]+)\]", declared)
        if not match or int(match[1]) != len(result):
            raise ValueError("Invalid array length")
    return result


def parse_inform(payload: bytes) -> Inform:
    if len(payload) > MAX_BODY:
        raise BadEnvelope
    try:
        root = fromstring(
            payload, forbid_dtd=True, forbid_entities=True, forbid_external=True
        )
    except (ET.ParseError, DefusedXmlException, ValueError) as exc:
        raise BadEnvelope from exc
    pending = [(root, 1)]
    while pending:
        node, depth = pending.pop()
        if depth > 32:
            raise BadEnvelope
        pending.extend((c, depth + 1) for c in node)
    try:
        if root.tag != f"{{{SOAP}}}Envelope":
            raise ValueError
        fields(root, (f"{{{SOAP}}}Header", f"{{{SOAP}}}Body"))
        header = child(root, f"{{{SOAP}}}Header")
        rpc_id = scalar(child(header, f"{{{CWMP}}}ID"), 256)
        for h in header:
            if h.tag != f"{{{CWMP}}}ID" and h.get(f"{{{SOAP}}}mustUnderstand") in (
                "1",
                "true",
            ):
                raise ValueError
        body = child(root, f"{{{SOAP}}}Body")
        if len(body) != 1 or not body[0].tag.startswith(f"{{{CWMP}}}"):
            raise ValueError
    except ValueError as exc:
        raise BadEnvelope from exc
    if body[0].tag != f"{{{CWMP}}}Inform":
        raise RpcFault(rpc_id, FaultCode.METHOD_NOT_SUPPORTED)
    try:
        rpc = body[0]
        fields(
            rpc,
            (
                "DeviceId",
                "Event",
                "MaxEnvelopes",
                "CurrentTime",
                "RetryCount",
                "ParameterList",
            ),
        )
        device = child(rpc, "DeviceId")
        fields(device, ("Manufacturer", "OUI", "ProductClass", "SerialNumber"))
        oui = scalar(child(device, "OUI"), 6)
        if not re.fullmatch(r"[0-9A-Fa-f]{6}", oui):
            raise ValueError
        identity = DeviceId(
            scalar(child(device, "Manufacturer"), 64),
            oui.upper(),
            scalar(child(device, "ProductClass"), 64, empty=True),
            scalar(child(device, "SerialNumber"), 64),
        )
        events: list[Event] = []
        for event in array(child(rpc, "Event"), "EventStruct", 64):
            fields(event, ("EventCode", "CommandKey"))
            events.append(
                Event(
                    scalar(child(event, "EventCode"), 64),
                    scalar(child(event, "CommandKey"), 32, empty=True),
                )
            )
        if not events:
            raise ValueError
        current_time = datetime.fromisoformat(scalar(child(rpc, "CurrentTime"), 64))
        if current_time.tzinfo is None:
            raise ValueError
        parameters: list[Parameter] = []
        names: set[str] = set()
        for parameter in array(
            child(rpc, "ParameterList"), "ParameterValueStruct", 4096
        ):
            fields(parameter, ("Name", "Value"))
            name = scalar(child(parameter, "Name"), 256)
            value = child(parameter, "Value")
            value_type = value.get(f"{{{XSI}}}type", "")
            if name in names or not value_type or len(value_type) > 128:
                raise ValueError
            names.add(name)
            parameters.append(
                Parameter(name, scalar(value, 65536, empty=True), value_type)
            )
        return Inform(
            rpc_id,
            "1.0",
            identity,
            tuple(events),
            unsigned(child(rpc, "MaxEnvelopes")),
            current_time,
            unsigned(child(rpc, "RetryCount")),
            tuple(parameters),
        )
    except ValueError as exc:
        raise RpcFault(rpc_id, FaultCode.INVALID_ARGUMENTS) from exc


def envelope(rpc_id: str) -> tuple[ET.Element, ET.Element]:
    root = ET.Element("soap:Envelope", {"xmlns:soap": SOAP, "xmlns:cwmp": CWMP})
    header = ET.SubElement(root, "soap:Header")
    ET.SubElement(header, "cwmp:ID", {"soap:mustUnderstand": "1"}).text = rpc_id
    return root, ET.SubElement(root, "soap:Body")


def serialize_inform_response(rpc_id: str) -> bytes:
    root, body = envelope(rpc_id)
    response = ET.SubElement(body, "cwmp:InformResponse")
    ET.SubElement(response, "MaxEnvelopes").text = "1"
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def serialize_fault(rpc_id: str, code: FaultCode) -> bytes:
    root, body = envelope(rpc_id)
    fault = ET.SubElement(body, "soap:Fault")
    ET.SubElement(fault, "faultcode").text = (
        "soap:Client" if code == FaultCode.INVALID_ARGUMENTS else "soap:Server"
    )
    ET.SubElement(fault, "faultstring").text = "CWMP fault"
    detail = ET.SubElement(ET.SubElement(fault, "detail"), "cwmp:Fault")
    ET.SubElement(detail, "FaultCode").text = str(int(code))
    ET.SubElement(detail, "FaultString").text = (
        "Invalid arguments"
        if code == FaultCode.INVALID_ARGUMENTS
        else "Method not supported"
    )
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
