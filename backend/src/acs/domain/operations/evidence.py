"""Portable partial evidence, without protocol or storage dependencies."""

from dataclasses import dataclass, field
from typing import Literal


class InvalidEvidence(ValueError):
    """Safe error boundary: never include input contents or filesystem paths."""


@dataclass(frozen=True)
class EvidenceDevice:
    manufacturer: str
    oui: str
    product_class: str
    model: str | None
    firmware: str | None
    hardware: str | None


@dataclass(frozen=True)
class EvidenceArtifact:
    name: Literal["inform.xml"]
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class EvidenceManifest:
    device: EvidenceDevice
    artifact: EvidenceArtifact
    received_at: str | None
    retained_parameters: int
    format_version: Literal["1"] = "1"
    evidence_kind: Literal["partial-inform-reconstruction"] = (
        "partial-inform-reconstruction"
    )
    partial: Literal[True] = True
    timestamp_source: Literal["input-declared", "unknown"] = "unknown"
    anonymization_policy: Literal["device-info-allowlist-v1"] = (
        "device-info-allowlist-v1"
    )
    xml_types: Literal["reconstructed-as-xsd-string"] = "reconstructed-as-xsd-string"
    authenticity_verified: Literal[False] = False


@dataclass(frozen=True)
class EvidencePackage:
    manifest: EvidenceManifest
    xml: bytes = field(repr=False)
