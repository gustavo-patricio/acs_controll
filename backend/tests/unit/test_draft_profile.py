"""Only evidenced fields enter the unapproved X6 proposal."""

import json
from pathlib import Path

import pytest

from acs.cwmp.compatibility.profiles import ProfileInventoryNormalizer

FIXTURE = (
    Path(__file__).parents[1] / "fixtures/cwmp/huawei_x6_stored_results_anonymized.json"
)


def test_draft_uses_anonymized_export_without_approving_or_guessing() -> None:
    export = json.loads(FIXTURE.read_text())
    proposal = ProfileInventoryNormalizer.draft_from_export(export)
    assert proposal.approval == "rascunho"
    assert proposal.manufacturer == "Huawei Technologies Co., Ltd"
    assert proposal.product_class == "EG8145X6-10"
    assert proposal.firmware == "V5R022C00S364"
    assert {m.field for m in proposal.mappings} == {"model", "firmware", "hardware"}
    assert all(m.status == "comprovado" and m.evidence for m in proposal.mappings)
    assert all(
        m.xml_type is None and "tipo XML" in (m.pending or "")
        for m in proposal.mappings
    )
    assert all(
        "WAN" not in m.path_or_rule and "{i}" not in m.path_or_rule
        for m in proposal.mappings
    )
    assert any("WAN/Wi-Fi" in pending for pending in proposal.pending)
    assert "serial_number" not in FIXTURE.read_text().lower()
    assert all(item["value"] is None for item in export["values"])
    assert not any(
        p.profile_id == proposal.profile_id
        for p in ProfileInventoryNormalizer().profiles
    )


def test_draft_rejects_missing_identity_or_evidence() -> None:
    export = json.loads(FIXTURE.read_text())
    export["identity"]["firmware"] = "UNREVIEWED"
    with pytest.raises(ValueError, match="unsupported_identity"):
        ProfileInventoryNormalizer.draft_from_export(export)
    export = json.loads(FIXTURE.read_text())
    export["catalog"] = []
    with pytest.raises(ValueError, match="insufficient_evidence"):
        ProfileInventoryNormalizer.draft_from_export(export)
