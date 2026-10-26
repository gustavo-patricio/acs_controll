"""Synthetic SOAP read replies: no real credentials, serials or session IDs."""

from xml.etree import ElementTree as ET

from acs.cwmp.compatibility.queries import NAMES
from acs.cwmp.soap.inform import ENC, XSI, envelope
from acs.cwmp.soap.queries import XSD
from acs.domain.operations.query import QueryKind


def reply_xml(
    rpc_id: str,
    kind: QueryKind,
    values: tuple[str, ...] = ("EG8145X6-10", "V5R022C00S364", "343D.D"),
    *,
    names: tuple[str, ...] = NAMES,
    fault: int | None = None,
) -> bytes:
    root, body = envelope(rpc_id)
    if fault is not None:
        soap_fault = ET.SubElement(body, "soap:Fault")
        ET.SubElement(soap_fault, "faultcode").text = "soap:Client"
        ET.SubElement(soap_fault, "faultstring").text = "SYNTHETIC-PRIVATE-FAULT"
        detail = ET.SubElement(ET.SubElement(soap_fault, "detail"), "cwmp:Fault")
        ET.SubElement(detail, "FaultCode").text = str(fault)
        ET.SubElement(detail, "FaultString").text = "SYNTHETIC-PRIVATE-FAULT"
    else:
        root.set("xmlns:enc", ENC)
        root.set("xmlns:xsi", XSI)
        root.set("xmlns:xsd", XSD)
        is_values = kind == "get_parameter_values"
        rpc = ET.SubElement(
            body,
            "cwmp:GetParameterValuesResponse"
            if is_values
            else "cwmp:GetParameterNamesResponse",
        )
        item_name = "ParameterValueStruct" if is_values else "ParameterInfoStruct"
        items = ET.SubElement(
            rpc, "ParameterList", {"enc:arrayType": f"cwmp:{item_name}[{len(names)}]"}
        )
        for index, name in enumerate(names):
            item = ET.SubElement(items, item_name)
            ET.SubElement(item, "Name").text = name
            if is_values:
                ET.SubElement(item, "Value", {"xsi:type": "xsd:string"}).text = values[
                    index
                ]
            else:
                ET.SubElement(item, "Writable").text = "false"
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
