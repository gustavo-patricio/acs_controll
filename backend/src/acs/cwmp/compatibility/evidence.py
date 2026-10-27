"""Allowlisted reconstruction of an Inform, never a copy of the request XML."""

from datetime import UTC, datetime
from typing import Literal
from xml.etree import ElementTree as ET

from acs.cwmp.soap.inform import CWMP, ENC, SOAP, XSI
from acs.domain.cpe.inform import Inform

INVENTORY_PATHS = frozenset(
    f"{root}.DeviceInfo.{field}"
    for root in ("Device", "InternetGatewayDevice")
    for field in ("ModelName", "SoftwareVersion", "HardwareVersion")
)


def safe_text(value: str, limit: int) -> bool:
    return len(value) <= limit and all(char.isprintable() for char in value)


def anonymized_inform(
    inform: Inform,
    received_at: datetime | None,
    *,
    source: Literal["accepted-inform", "offline-reconstruction"] = "accepted-inform",
) -> bytes:
    """Retain basic inventory evidence; replace all session/device identifiers.

    The original xsi:type spelling is intentionally not retained: its namespace
    context is not available in the protocol-neutral parsed representation.
    """
    parameters = tuple(
        parameter
        for parameter in inform.parameters
        if parameter.name in INVENTORY_PATHS and safe_text(parameter.value, 256)
    )
    root = ET.Element(
        "soap:Envelope",
        {
            "xmlns:soap": SOAP,
            "xmlns:cwmp": CWMP,
            "xmlns:enc": ENC,
            "xmlns:xsi": XSI,
            "xmlns:xsd": "http://www.w3.org/2001/XMLSchema",
        },
    )
    root.append(
        ET.Comment(
            f" ACS evidence v1; source={source}; "
            f"received_at={received_at.astimezone(UTC).isoformat() if received_at else 'unknown'}; "
            f"parameters_received={len(inform.parameters)}; "
            f"parameters_retained={len(parameters)}; "
            "serial, RPC ID, events, time, counters and types are synthetic. "
        )
    )
    header = ET.SubElement(root, "soap:Header")
    ET.SubElement(
        header, "cwmp:ID", {"soap:mustUnderstand": "1"}
    ).text = "anonymized-inform-001"
    rpc = ET.SubElement(ET.SubElement(root, "soap:Body"), "cwmp:Inform")
    device = ET.SubElement(rpc, "DeviceId")
    for name, value, limit in (
        ("Manufacturer", inform.device.manufacturer, 64),
        ("OUI", inform.device.oui, 6),
        ("ProductClass", inform.device.product_class, 64),
    ):
        ET.SubElement(device, name).text = (
            value if safe_text(value, limit) else "REDACTED"
        )
    ET.SubElement(device, "SerialNumber").text = "ANONYMIZED0001"
    events = ET.SubElement(rpc, "Event", {"enc:arrayType": "cwmp:EventStruct[1]"})
    event = ET.SubElement(events, "EventStruct")
    ET.SubElement(event, "EventCode").text = "2 PERIODIC"
    ET.SubElement(event, "CommandKey")
    for name, value in (
        ("MaxEnvelopes", "1"),
        ("CurrentTime", "2000-01-01T00:00:00Z"),
        ("RetryCount", "0"),
    ):
        ET.SubElement(rpc, name).text = value
    parameter_list = ET.SubElement(
        rpc,
        "ParameterList",
        {"enc:arrayType": f"cwmp:ParameterValueStruct[{len(parameters)}]"},
    )
    for parameter in parameters:
        item = ET.SubElement(parameter_list, "ParameterValueStruct")
        ET.SubElement(item, "Name").text = parameter.name
        ET.SubElement(item, "Value", {"xsi:type": "xsd:string"}).text = parameter.value
    ET.indent(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
