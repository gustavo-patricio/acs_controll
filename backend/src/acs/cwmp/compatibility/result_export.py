"""Conservative export policy and reconstructed response archive (never raw XML)."""

import ipaddress
import json
import re
from typing import Any
from xml.etree import ElementTree as ET

from acs.cwmp.compatibility.evidence import INVENTORY_PATHS, safe_text
from acs.cwmp.soap.inform import ENC, XSI, envelope
from acs.cwmp.soap.queries import XSD
from acs.domain.operations.export import ExportSnapshot

SENSITIVE = re.compile(
    r"password|passwd|secret|token|credential|username|passphrase|psk", re.IGNORECASE
)
MAC = re.compile(r"(?i)(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}")
URL = re.compile(r"(?i)\b(?:https?|ftp)://[^\s]+")
IP_CANDIDATE = re.compile(
    r"(?<![\w])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![\w])|(?<![\w])(?:[0-9a-fA-F]{0,4}:){2,}[0-9a-fA-F:.]+"
)
TYPES = {"xsd:string", "xsd:int", "xsd:unsignedInt", "xsd:boolean"}


class Redactor:
    def __init__(self, serial: str) -> None:
        self.serial = serial
        self.addresses: dict[str, str] = {}
        self.macs: dict[str, str] = {}

    def address(self, raw: str) -> str:
        parsed = ipaddress.ip_address(raw)
        key = str(parsed)
        if key not in self.addresses:
            index = len(self.addresses) + 1
            self.addresses[key] = (
                str(ipaddress.ip_address("2001:db8::") + index)
                if parsed.version == 6
                else str(ipaddress.ip_address("198.18.0.0") + index)
            )
        return self.addresses[key]

    def mac(self, match: re.Match[str]) -> str:
        key = match[0].lower().replace("-", ":")
        if key not in self.macs:
            index = len(self.macs) + 1
            self.macs[key] = "02:00:" + ":".join(
                f"{b:02x}" for b in index.to_bytes(4, "big")
            )
        return self.macs[key]

    def text(self, value: str | None) -> str | None:
        if value is None:
            return None
        if not safe_text(value, 4096):
            return "REDACTED"
        value = URL.sub("https://example.invalid/anonymized", value)
        value = MAC.sub(self.mac, value)

        def ip(match: re.Match[str]) -> str:
            try:
                return self.address(match[0])
            except ValueError:
                return match[0]

        value = IP_CANDIDATE.sub(ip, value)
        if self.serial:
            value = value.replace(self.serial, "ANONYMIZED0001")
        return value

    def path(self, value: str | None) -> str | None:
        # Numeric TR instances are not IP addresses: preserve their spelling.
        if value is None:
            return None
        if not safe_text(value, 256):
            return "REDACTED"
        return value.replace(self.serial, "ANONYMIZED0001") if self.serial else value

    def metadata(self, value: str | None) -> str | None:
        return "REDACTED" if value and SENSITIVE.search(value) else self.text(value)

    def value(self, path: str, kind: str, raw: str) -> tuple[str | None, str]:
        if SENSITIVE.search(path):
            return None, "sensitive"
        if any(part.startswith("X_") for part in path.split(".")):
            return None, "unknown_proprietary_sensitivity"
        if kind != "xsd:string":
            return None, "type_not_allowed_for_path"
        leaf = path.rsplit(".", 1)[-1]
        if leaf == "SerialNumber":
            return "ANONYMIZED0001", "anonymized"
        if leaf in ("URL", "ConnectionRequestURL"):
            return "https://example.invalid/anonymized", "anonymized"
        if leaf in ("IPAddress", "ExternalIPAddress", "DNSServers"):
            try:
                return ",".join(
                    self.address(part.strip()) for part in raw.split(",")
                ) if raw else "", "anonymized"
            except ValueError:
                return None, "invalid_address"
        if leaf == "MACAddress":
            if not MAC.fullmatch(raw):
                return None, "invalid_mac"
            return MAC.sub(self.mac, raw), "anonymized"
        if path in INVENTORY_PATHS and safe_text(raw, 256):
            clean = self.metadata(raw)
            return clean, "retained" if clean == raw else "anonymized"
        return None, "not_in_export_allowlist"


def export_document(source: ExportSnapshot) -> dict[str, Any]:
    redact = Redactor(source.serial)
    refs = {
        row.id: f"operation-{index:04d}"
        for index, row in enumerate(source.operations, 1)
    }
    operations: list[dict[str, Any]] = []
    values: list[dict[str, Any]] = []
    seen = {(v.operation_id, v.path) for v in source.values}
    for row in source.operations:
        operations.append(
            {
                "ref": refs[row.id],
                "kind": row.kind,
                "scope": row.scope,
                "parameter_path": redact.path(row.path),
                "requested_names": [redact.path(n) for n in row.names],
                "state": row.state,
                "failure_reason": redact.metadata(row.failure_reason),
                "fault_code": row.fault_code,
                "finished_at": row.finished_at.isoformat() if row.finished_at else None,
            }
        )
        if row.kind == "get_parameter_values":
            for path in row.result_names:
                if path.startswith(source.prefix) and (row.id, path) not in seen:
                    values.append(
                        {
                            "path": redact.path(path),
                            "xml_type": None,
                            "value": None,
                            "status": "omitted",
                            "reason": "typed_observation_not_retained",
                            "operation_ref": refs[row.id],
                            "observed_at": None,
                        }
                    )
    for row in source.values:
        value, reason = redact.value(row.path, row.xml_type, row.value)
        values.append(
            {
                "path": redact.path(row.path),
                "xml_type": redact.text(row.xml_type),
                "value": value,
                "status": "omitted" if value is None else reason,
                "reason": reason if value is None else None,
                "operation_ref": refs[row.operation_id],
                "observed_at": row.observed_at.isoformat(),
            }
        )
    return {
        "format_version": "stored-results-1",
        "xml_fidelity": "reconstructed",
        "partial": True,
        "identity": {
            "manufacturer": redact.metadata(source.manufacturer),
            "model": redact.metadata(source.model),
            "product_class": redact.metadata(source.product_class),
            "firmware": redact.metadata(source.firmware),
            "serial": "ANONYMIZED0001",
        },
        "scope": {
            "prefix": redact.path(source.prefix),
            "observations": "latest_per_path",
            "recent_operations_limit": 256,
            "history_truncated": source.history_truncated,
        },
        "catalog": [
            {
                "path": redact.path(r.path),
                "is_object": r.is_object,
                "writable": r.writable,
                "observed_at": r.observed_at.isoformat(),
                "operation_ref": refs[r.operation_id],
            }
            for r in source.catalog
        ],
        "values": values,
        "operations": operations,
    }


def export_xml(document: dict[str, Any]) -> bytes:
    archive = ET.Element(
        "CollectedResponses",
        {
            "formatVersion": "stored-results-1",
            "fidelity": "reconstructed",
            "partial": "true",
        },
    )
    # Metadata is the exact same sanitized projection as JSON, including omissions.
    ET.SubElement(archive, "Manifest", {"encoding": "json"}).text = json.dumps(
        document, ensure_ascii=False
    )
    for operation in document["operations"]:
        ref = operation["ref"]
        names = [item for item in document["catalog"] if item["operation_ref"] == ref]
        values = [
            item
            for item in document["values"]
            if item["operation_ref"] == ref
            and item["status"] != "omitted"
            and item["xml_type"] in TYPES
        ]
        fault = operation["fault_code"]
        if not names and not values and fault is None:
            continue
        root, body = envelope(ref)
        if fault is not None:
            node = ET.SubElement(body, "soap:Fault")
            ET.SubElement(node, "faultcode").text = "soap:Client"
            ET.SubElement(node, "faultstring").text = "REDACTED"
            detail = ET.SubElement(ET.SubElement(node, "detail"), "cwmp:Fault")
            ET.SubElement(detail, "FaultCode").text = str(fault)
            ET.SubElement(detail, "FaultString").text = "REDACTED"
        else:
            is_names = operation["kind"] == "get_parameter_names"
            root.set("xmlns:enc", ENC)
            root.set("xmlns:xsi", XSI)
            root.set("xmlns:xsd", XSD)
            rpc = ET.SubElement(
                body,
                "cwmp:GetParameterNamesResponse"
                if is_names
                else "cwmp:GetParameterValuesResponse",
            )
            items = names if is_names else values
            struct = "ParameterInfoStruct" if is_names else "ParameterValueStruct"
            listing = ET.SubElement(
                rpc, "ParameterList", {"enc:arrayType": f"cwmp:{struct}[{len(items)}]"}
            )
            for item in items:
                node = ET.SubElement(listing, struct)
                ET.SubElement(node, "Name").text = item["path"]
                if is_names:
                    ET.SubElement(node, "Writable").text = str(item["writable"]).lower()
                else:
                    ET.SubElement(
                        node, "Value", {"xsi:type": item["xml_type"]}
                    ).text = item["value"]
        wrapper = ET.SubElement(
            archive, "Response", {"operationRef": ref, "partial": "true"}
        )
        wrapper.append(root)
    return ET.tostring(archive, encoding="utf-8", xml_declaration=True)
