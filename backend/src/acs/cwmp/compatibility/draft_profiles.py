"""Evidence-backed proposal; never registered for runtime normalization."""

from dataclasses import dataclass
from typing import Literal, cast


@dataclass(frozen=True)
class DraftMapping:
    field: Literal["model", "firmware", "hardware"]
    path_or_rule: str
    xml_type: str | None
    conversion: str
    evidence: tuple[str, ...]
    status: Literal["comprovado", "pendente"]
    pending: str | None = None


@dataclass(frozen=True)
class DraftProfile:
    profile_id: str
    manufacturer: str
    product_class: str
    firmware: str
    approval: Literal["rascunho"]
    mappings: tuple[DraftMapping, ...]
    pending: tuple[str, ...]


IDENTITY = {
    "manufacturer": "Huawei Technologies Co., Ltd",
    "product_class": "EG8145X6-10",
    "firmware": "V5R022C00S364",
}
ROOT = "InternetGatewayDevice.DeviceInfo."
INFORM = "huawei_eg8145x6_10_v5r022c00s364.xml"


def draft_from_export(document: dict[str, object]) -> DraftProfile:
    if (
        document.get("format_version") != "stored-results-1"
        or document.get("partial") is not True
    ):
        raise ValueError("unsupported_export")
    identity = document.get("identity")
    if not isinstance(identity, dict):
        raise TypeError("unsupported_identity")
    identity = cast(dict[str, object], identity)
    if any(identity.get(k) != v for k, v in IDENTITY.items()):
        raise ValueError("unsupported_identity")
    items = document.get("catalog")
    if not isinstance(items, list):
        raise TypeError("missing_catalog")
    paths: set[str] = set()
    for item in cast(list[object], items):
        if isinstance(item, dict):
            entry = cast(dict[str, object], item)
            path = entry.get("path")
            if entry.get("is_object") is False and isinstance(path, str):
                paths.add(path)
    if not {ROOT + "SoftwareVersion", ROOT + "HardwareVersion"}.issubset(paths):
        raise ValueError("insufficient_evidence")
    return DraftProfile(
        "huawei-eg8145x6-10-v5r022c00s364-draft",
        IDENTITY["manufacturer"],
        IDENTITY["product_class"],
        IDENTITY["firmware"],
        "rascunho",
        (
            DraftMapping(
                "model",
                "DeviceId.ProductClass when ModelName is absent",
                None,
                "texto validado; regra já existente no perfil X6",
                ("export:identity.product_class", INFORM + ":DeviceId.ProductClass"),
                "comprovado",
                "ModelName tem apenas nome observado; tipo XML não aplicável a DeviceId",
            ),
            DraftMapping(
                "firmware",
                ROOT + "SoftwareVersion",
                None,
                "texto validado pelo normalizador genérico; sem conversão de unidade",
                ("export:catalog:SoftwareVersion", INFORM + ":SoftwareVersion"),
                "comprovado",
                "tipo XML original não preservado",
            ),
            DraftMapping(
                "hardware",
                ROOT + "HardwareVersion",
                None,
                "texto validado pelo normalizador genérico; sem conversão de unidade",
                ("export:catalog:HardwareVersion", INFORM + ":HardwareVersion"),
                "comprovado",
                "tipo XML original não preservado",
            ),
        ),
        (
            "Tipo XML original de DeviceInfo",
            "Resolução de instâncias WAN/Wi-Fi",
            "Folhas e semântica de campos WAN, ópticos e proprietários",
            "Unidades e escalas ópticas",
        ),
    )
