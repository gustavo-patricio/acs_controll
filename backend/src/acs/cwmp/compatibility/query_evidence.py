"""Partial, anonymized SOAP read evidence; no original XML or session secrets."""

import hashlib
import ipaddress
import json
import re
from dataclasses import asdict, replace
from datetime import datetime
from typing import Literal
from uuid import UUID
from xml.etree import ElementTree as ET

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from acs.cwmp.compatibility import huawei_telemetry
from acs.cwmp.compatibility.evidence import safe_text
from acs.cwmp.soap.inform import ENC, XSI, RpcFault, envelope
from acs.cwmp.soap.queries import XSD, parse_query_reply, serialize_query
from acs.domain.cpe.inform import Parameter
from acs.domain.operations.evidence import InvalidEvidence
from acs.domain.operations.query import (
    QueryCommand,
    QueryConflict,
    QueryKind,
    QueryPlan,
    QueryReply,
    QueryScope,
    QueryTarget,
)

MAX_QUERY_EVIDENCE = 1_048_576
RPC_ID = "anonymized-query-001"


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class TargetEvidence(ClosedModel):
    manufacturer: str = Field(max_length=64)
    oui: str = Field(max_length=6)
    product_class: str = Field(max_length=64)
    firmware: str | None = Field(max_length=256)
    model: str | None = Field(max_length=256)


class XmlArtifact(ClosedModel):
    xml: str = Field(max_length=MAX_QUERY_EVIDENCE, repr=False)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0, le=MAX_QUERY_EVIDENCE)


class QueryEvidenceManifest(ClosedModel):
    target: TargetEvidence
    profile_id: str = Field(max_length=128)
    profile_version: str = Field(max_length=32)
    kind: QueryKind
    scope: QueryScope
    parameter_path: str = Field(max_length=256)
    requested_names: tuple[str, ...] = Field(max_length=16)
    received_at: AwareDatetime
    received_count: int = Field(ge=0, le=128)
    omitted_names: tuple[str, ...] = Field(max_length=16)
    substituted_names: tuple[str, ...] = Field(max_length=16)
    partial: Literal[True] = True
    cwmp_version: Literal["1.0"] = "1.0"
    xml_fidelity: Literal["reconstructed-with-resolved-types"] = (
        "reconstructed-with-resolved-types"
    )
    anonymization_policy: Literal["scoped-read-v1"] = "scoped-read-v1"
    optical_unit: Literal["unverified"] = "unverified"


class QueryEvidenceBundle(ClosedModel):
    format_version: Literal["query-evidence-1"] = "query-evidence-1"
    manifest: QueryEvidenceManifest
    request: XmlArtifact
    response: XmlArtifact


def _artifact(payload: bytes) -> XmlArtifact:
    return XmlArtifact(
        xml=payload.decode("utf-8"),
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
    )


def _response(reply: QueryReply) -> bytes:
    root, body = envelope(RPC_ID)
    if reply.kind == "fault":
        fault = ET.SubElement(body, "soap:Fault")
        ET.SubElement(fault, "faultcode").text = "soap:Client"
        ET.SubElement(fault, "faultstring").text = "REDACTED"
        detail = ET.SubElement(ET.SubElement(fault, "detail"), "cwmp:Fault")
        ET.SubElement(detail, "FaultCode").text = str(reply.fault_code)
        ET.SubElement(detail, "FaultString").text = "REDACTED"
    else:
        root.set("xmlns:enc", ENC)
        values = reply.kind == "get_parameter_values"
        if values:
            root.set("xmlns:xsi", XSI)
            root.set("xmlns:xsd", XSD)
        rpc = ET.SubElement(
            body,
            "cwmp:GetParameterValuesResponse"
            if values
            else "cwmp:GetParameterNamesResponse",
        )
        entry = "ParameterValueStruct" if values else "ParameterInfoStruct"
        count = len(reply.parameters) if values else len(reply.names)
        items = ET.SubElement(
            rpc, "ParameterList", {"enc:arrayType": f"cwmp:{entry}[{count}]"}
        )
        if values:
            for parameter in sorted(reply.parameters, key=lambda p: p.name):
                item = ET.SubElement(items, entry)
                ET.SubElement(item, "Name").text = parameter.name
                ET.SubElement(
                    item, "Value", {"xsi:type": parameter.value_type}
                ).text = parameter.value
        else:
            for parameter in sorted(reply.names, key=lambda p: p.name):
                item = ET.SubElement(items, entry)
                ET.SubElement(item, "Name").text = parameter.name
                ET.SubElement(item, "Writable").text = (
                    "true" if parameter.writable else "false"
                )
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def build_query_evidence(
    reply: QueryReply,
    plan: QueryPlan,
    kind: QueryKind,
    at: datetime,
    received_count: int,
) -> str:
    """Caller has correlated the reply and projected approved discovery names."""
    retained: list[Parameter] = []
    substituted: list[str] = []
    ips: dict[str, str] = {}
    counts = {4: 0, 6: 0}

    def address(raw: str) -> str:
        parsed = ipaddress.ip_address(raw.strip())
        key = str(parsed)
        if key not in ips:
            counts[parsed.version] += 1
            index = counts[parsed.version]
            ips[key] = (
                f"192.0.2.{index}" if parsed.version == 4 else f"2001:db8::{index:x}"
            )
        return ips[key]

    for parameter in sorted(reply.parameters, key=lambda p: p.name):
        if parameter.name not in plan.names:
            raise InvalidEvidence
        if plan.scope == "device_info":
            if parameter.value_type == "xsd:string" and safe_text(parameter.value, 256):
                retained.append(parameter)
            continue
        name = huawei_telemetry.field_name(
            plan.parameter_path, parameter.name, plan.scope
        )
        if name is None:
            continue
        raw = parameter.value.strip()
        if plan.scope == "optical":
            if parameter.value_type in (
                "xsd:string",
                "xsd:int",
                "xsd:unsignedInt",
            ) and re.fullmatch(r"[+-]?[0-9]{1,10}(?:\.[0-9]{1,4})?", raw):
                retained.append(parameter)
            continue
        observation = huawei_telemetry.observe(name, parameter)
        if observation.state not in ("valid", "unavailable"):
            continue
        if observation.state == "valid" and name in ("ipv4", "ipv6", "dns", "mac"):
            substituted.append(parameter.name)
            value = (
                "02:00:00:00:00:01"
                if name == "mac"
                else ",".join(address(part) for part in raw.split(","))
            )
            parameter = replace(parameter, value=value)
        retained.append(parameter)
    projected = replace(reply, rpc_id=RPC_ID, parameters=tuple(retained))
    omitted = (
        tuple(sorted(set(plan.names) - {p.name for p in retained}))
        if reply.kind == "get_parameter_values"
        else ()
    )
    manifest = QueryEvidenceManifest(
        target=TargetEvidence(**asdict(plan.target)),
        profile_id=plan.profile_id,
        profile_version=plan.profile_version,
        kind=kind,
        scope=plan.scope,
        parameter_path=plan.parameter_path,
        requested_names=plan.names,
        received_at=at,
        received_count=received_count,
        omitted_names=omitted,
        substituted_names=tuple(substituted),
    )
    request = serialize_query(QueryCommand(UUID(int=0), RPC_ID, kind, plan))
    result = QueryEvidenceBundle(
        manifest=manifest,
        request=_artifact(request),
        response=_artifact(_response(projected)),
    ).model_dump_json()
    if len(result.encode()) > MAX_QUERY_EVIDENCE:
        raise InvalidEvidence
    return result


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise InvalidEvidence
        result[key] = value
    return result


def verify_query_evidence(payload: bytes) -> QueryEvidenceBundle:
    # Local import avoids a module cycle: the policy also uses the builder.
    from acs.cwmp.compatibility.queries import HuaweiQueryPolicy

    try:
        if len(payload) > MAX_QUERY_EVIDENCE:
            raise InvalidEvidence
        json.loads(payload, object_pairs_hook=_unique)
        bundle = QueryEvidenceBundle.model_validate_json(payload)
        manifest = bundle.manifest
        if (
            manifest.scope == "discovery" and manifest.kind != "get_parameter_names"
        ) or (
            manifest.scope in ("wan", "optical")
            and manifest.kind != "get_parameter_values"
        ):
            raise InvalidEvidence
        policy = HuaweiQueryPolicy()
        target = QueryTarget(**manifest.target.model_dump())
        plan = policy.plan(
            target,
            manifest.scope,
            None if manifest.scope == "device_info" else manifest.parameter_path,
            manifest.requested_names,
        )
        if (plan.profile_id, plan.profile_version, plan.parameter_path, plan.names) != (
            manifest.profile_id,
            manifest.profile_version,
            manifest.parameter_path,
            manifest.requested_names,
        ):
            raise InvalidEvidence
        reply = parse_query_reply(bundle.response.xml.encode())
        if reply.kind not in (manifest.kind, "fault"):
            raise InvalidEvidence
        count = (
            len(reply.names)
            if reply.kind == "get_parameter_names"
            else len(reply.parameters)
        )
        if count > manifest.received_count or (
            reply.kind == "get_parameter_values"
            and manifest.received_count != len(plan.names)
        ):
            raise InvalidEvidence
        if reply.kind == "get_parameter_names":
            if any(
                not item.name.startswith(plan.parameter_path)
                or "." in item.name[len(plan.parameter_path) :].rstrip(".")
                for item in reply.names
            ):
                raise InvalidEvidence
            reply = replace(reply, names=policy.discovery_names(reply, plan))
        rebuilt = QueryEvidenceBundle.model_validate_json(
            build_query_evidence(
                reply,
                plan,
                manifest.kind,
                manifest.received_at,
                manifest.received_count,
            )
        )
        if rebuilt != bundle:
            raise InvalidEvidence
        return bundle
    except (ValueError, QueryConflict, RpcFault, RecursionError):
        raise InvalidEvidence from None
