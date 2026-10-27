"""Offline reconstruction and integrity checks using the existing safe parser."""

import hashlib
import json
import re
from dataclasses import asdict
from datetime import UTC, datetime

from acs.cwmp.compatibility.evidence import anonymized_inform
from acs.cwmp.compatibility.generic import GenericInventoryNormalizer
from acs.cwmp.soap.inform import RpcFault, parse_inform
from acs.domain.operations.evidence import (
    EvidenceArtifact,
    EvidenceDevice,
    EvidenceManifest,
    EvidencePackage,
    InvalidEvidence,
)

MAX_MANIFEST = 16_384
CAPTURE_COMMENT = re.compile(
    rb"<!-- ACS evidence v1; source=(?:accepted-inform|offline-reconstruction); "
    rb"received_at=([^;]{1,64}); parameters_received=[0-9]+; "
    rb"parameters_retained=[0-9]+; "
    rb"serial, RPC ID, events, time, counters and types are synthetic\. -->"
)


def build_package(payload: bytes) -> EvidencePackage:
    """Input is untrusted. Publish only a new allowlisted reconstruction."""
    try:
        inform = parse_inform(payload)
        matches = CAPTURE_COMMENT.findall(payload)
        if len(matches) > 1:
            raise ValueError
        at = None
        if matches and matches[0] != b"unknown":
            at = datetime.fromisoformat(matches[0].decode("ascii"))
            if at.tzinfo is None:
                raise ValueError
            at = at.astimezone(UTC)
        xml = anonymized_inform(inform, at, source="offline-reconstruction")
        safe = parse_inform(xml)
        # Package counts describe the retained reconstruction, not an original
        # envelope that is no longer available. This also makes replay stable.
        xml = anonymized_inform(safe, at, source="offline-reconstruction")
        # The normalizer requires a timestamp; this internal sentinel never enters
        # the manifest. Unknown collection time stays unknown.
        inventory = GenericInventoryNormalizer().normalize(
            safe, at or datetime(2000, 1, 1, tzinfo=UTC)
        )
        manifest = EvidenceManifest(
            device=EvidenceDevice(
                safe.device.manufacturer,
                safe.device.oui,
                safe.device.product_class,
                inventory.model.value,
                inventory.firmware.value,
                inventory.hardware.value,
            ),
            artifact=EvidenceArtifact(
                "inform.xml", hashlib.sha256(xml).hexdigest(), len(xml)
            ),
            received_at=at.isoformat() if at else None,
            retained_parameters=len(safe.parameters),
            timestamp_source="input-declared" if at else "unknown",
        )
        return EvidencePackage(manifest, xml)
    except (ValueError, RpcFault):
        raise InvalidEvidence from None


def manifest_bytes(manifest: EvidenceManifest) -> bytes:
    return (
        json.dumps(asdict(manifest), sort_keys=True, indent=2, ensure_ascii=True) + "\n"
    ).encode()


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise InvalidEvidence
        result[key] = value
    return result


def verify_package(xml: bytes, manifest: bytes) -> EvidenceManifest:
    try:
        if len(manifest) > MAX_MANIFEST:
            raise InvalidEvidence
        document: object = json.loads(manifest, object_pairs_hook=_unique_object)
        expected = build_package(xml)
        # Rebuilding removes unknown elements, attributes and comments. Equality
        # prevents an edited XML from bypassing the allowlist with a new checksum.
        if expected.xml != xml or json.dumps(document, sort_keys=True) != json.dumps(
            asdict(expected.manifest), sort_keys=True
        ):
            raise InvalidEvidence
        return expected.manifest
    except (ValueError, UnicodeError, RecursionError):
        raise InvalidEvidence from None
