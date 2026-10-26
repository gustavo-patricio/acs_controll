"""SOAP 1.1/CWMP 1.0 read requests and bounded response parsing."""

from io import BytesIO
from typing import cast
from xml.etree import ElementTree as ET

from defusedxml.ElementTree import iterparse

from acs.cwmp.soap.inform import (
    CWMP,
    ENC,
    SOAP,
    XSI,
    BadEnvelope,
    FaultCode,
    RpcFault,
    array,
    child,
    envelope,
    fields,
    parse_envelope,
    scalar,
    unsigned,
)
from acs.domain.cpe.inform import Parameter
from acs.domain.operations.query import ParameterInfo, QueryCommand, QueryReply

XSD = "http://www.w3.org/2001/XMLSchema"


def serialize_query(command: QueryCommand) -> bytes:
    root, body = envelope(command.rpc_id)
    if command.kind == "get_parameter_names":
        rpc = ET.SubElement(body, "cwmp:GetParameterNames")
        ET.SubElement(rpc, "ParameterPath").text = command.plan.parameter_path
        ET.SubElement(rpc, "NextLevel").text = (
            "true" if command.plan.next_level else "false"
        )
    else:
        root.set("xmlns:enc", ENC)
        root.set("xmlns:xsd", XSD)
        rpc = ET.SubElement(body, "cwmp:GetParameterValues")
        names = ET.SubElement(
            rpc,
            "ParameterNames",
            {"enc:arrayType": f"xsd:string[{len(command.plan.names)}]"},
        )
        for name in command.plan.names:
            ET.SubElement(names, "string").text = name
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _value_types(payload: bytes) -> list[str]:
    # The envelope has already passed size/depth/XXE checks. Resolve QName types
    # using each element's namespace scope, not the lexical prefix 'xsd'.
    scopes: list[dict[str, str]] = [{}]
    pending: dict[str, str] = {}
    result: list[str] = []
    for event, item in iterparse(
        BytesIO(payload),
        events=("start-ns", "start", "end"),
        forbid_dtd=True,
        forbid_entities=True,
        forbid_external=True,
    ):
        if event == "start-ns":
            prefix, uri = cast(tuple[str, str], item)
            pending[prefix] = uri
        elif event == "start":
            element = cast(ET.Element, item)
            scopes.append(scopes[-1] | pending)
            pending = {}
            if element.tag == "Value":
                kind = element.get(f"{{{XSI}}}type", "")
                prefix, separator, local = kind.partition(":")
                uri = scopes[-1].get(prefix if separator else "", "")
                result.append(f"{{{uri}}}{local if separator else prefix}")
        else:
            scopes.pop()
    return result


def parse_query_reply(payload: bytes) -> QueryReply:
    rpc_id, rpc = parse_envelope(payload)
    try:
        if rpc.tag == f"{{{SOAP}}}Fault":
            fields(rpc, ("faultcode", "faultstring", "detail"))
            scalar(child(rpc, "faultcode"), 256)
            scalar(child(rpc, "faultstring"), 4096, empty=True)
            detail = child(rpc, "detail")
            fields(detail, (f"{{{CWMP}}}Fault",))
            fault = child(detail, f"{{{CWMP}}}Fault")
            fields(fault, ("FaultCode", "FaultString"))
            code = unsigned(child(fault, "FaultCode"))
            if not 9000 <= code <= 9999:
                raise ValueError
            scalar(child(fault, "FaultString"), 4096, empty=True)
            # Retain the received numeric code only; fault strings are untrusted.
            return QueryReply(rpc_id, "fault", fault_code=code)
        if rpc.tag not in (
            f"{{{CWMP}}}GetParameterValuesResponse",
            f"{{{CWMP}}}GetParameterNamesResponse",
        ):
            raise RpcFault(rpc_id, FaultCode.METHOD_NOT_SUPPORTED)
        fields(rpc, ("ParameterList",))
        names: set[str] = set()
        if rpc.tag.endswith("GetParameterValuesResponse"):
            values = array(child(rpc, "ParameterList"), "ParameterValueStruct", 16)
            types = _value_types(payload)
            if len(types) != len(values):
                raise ValueError
            parameters: list[Parameter] = []
            for item, value_type in zip(values, types, strict=True):
                fields(item, ("Name", "Value"))
                name = scalar(child(item, "Name"), 256)
                if name in names or value_type != f"{{{XSD}}}string":
                    raise ValueError
                names.add(name)
                parameters.append(
                    Parameter(
                        name,
                        scalar(child(item, "Value"), 4096, empty=True),
                        "xsd:string",
                    )
                )
            return QueryReply(
                rpc_id, "get_parameter_values", parameters=tuple(parameters)
            )
        information: list[ParameterInfo] = []
        for item in array(child(rpc, "ParameterList"), "ParameterInfoStruct", 128):
            fields(item, ("Name", "Writable"))
            name = scalar(child(item, "Name"), 256)
            writable = scalar(child(item, "Writable"), 8).strip()
            if name in names or writable not in ("true", "false", "1", "0"):
                raise ValueError
            names.add(name)
            information.append(ParameterInfo(name, writable in ("true", "1")))
        return QueryReply(rpc_id, "get_parameter_names", names=tuple(information))
    except ValueError:
        # A malformed CPE response must not create a successful operation.
        raise BadEnvelope from None
