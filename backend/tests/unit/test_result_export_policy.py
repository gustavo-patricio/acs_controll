"""Export retains useful provenance while refusing unknown or identifying values."""

from datetime import UTC, datetime
from uuid import uuid4

from defusedxml.ElementTree import fromstring

from acs.cwmp.compatibility.result_export import export_document, export_xml
from acs.domain.operations.export import (
    ExportName,
    ExportOperation,
    ExportSnapshot,
    ExportValue,
)


def test_export_redacts_individual_identifiers_and_marks_omissions() -> None:
    at = datetime.now(UTC)
    op = uuid4()
    names = (
        "InternetGatewayDevice.DeviceInfo.HardwareVersion",
        "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.ExternalIPAddress",
        "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.DNSServers",
        "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.MACAddress",
        "InternetGatewayDevice.ManagementServer.ConnectionRequestURL",
        "InternetGatewayDevice.ManagementServer.Password",
        "InternetGatewayDevice.X_HW_Private.OpticalPassword",
        "InternetGatewayDevice.X_HW_Private.SomeValue",
    )
    raw = (
        " SN-PRIVATE https://192.168.18.1/path aa:bb:cc:dd:ee:ff ",
        "192.168.18.1",
        "192.168.18.1,8.8.8.8",
        "aa:bb:cc:dd:ee:ff",
        "https://admin:pass@192.168.18.1:7547/tr069",
        "PASSWORD-PRIVATE",
        "CREDENTIAL-PRIVATE",
        "UNKNOWN-PRIVATE",
    )
    snapshot = ExportSnapshot(
        "Huawei",
        "EG8145X6-10",
        "EG8145X6-10",
        "V5R022",
        "SN-PRIVATE",
        "",
        tuple(ExportName(n, False, False, at, op) for n in names),
        tuple(
            ExportValue(n, "xsd:string", v, at, op)
            for n, v in zip(names, raw, strict=True)
        ),
        (
            ExportOperation(
                op,
                "get_parameter_values",
                "device_info",
                None,
                names,
                names,
                "failed",
                "cpe_fault",
                9005,
                at,
            ),
        ),
        False,
    )
    document = export_document(snapshot)
    xml = export_xml(document)
    combined = str(document) + xml.decode()
    for secret in (
        "SN-PRIVATE",
        "192.168.18.1",
        "8.8.8.8",
        "aa:bb:cc:dd:ee:ff",
        "admin:pass",
        "PASSWORD-PRIVATE",
        "CREDENTIAL-PRIVATE",
        "UNKNOWN-PRIVATE",
    ):
        assert secret not in combined
    assert document["identity"]["serial"] == "ANONYMIZED0001"
    assert document["values"][0]["value"].startswith(" ANONYMIZED0001")
    assert (
        document["values"][1]["value"] == document["values"][2]["value"].split(",")[0]
    )
    assert document["values"][5]["reason"] == "sensitive"
    assert document["values"][7]["reason"] == "unknown_proprietary_sensitivity"
    assert document["values"][7]["xml_type"] == "xsd:string"
    assert document["xml_fidelity"] == "reconstructed"
    root = fromstring(xml)
    assert root.attrib["fidelity"] == "reconstructed"
    assert root.find("Manifest") is not None
    assert root.find("Response") is not None
    assert "PASSWORD-PRIVATE" not in xml.decode()


def test_unknown_types_and_old_untyped_results_are_omitted() -> None:
    at = datetime.now(UTC)
    op = uuid4()
    path = "InternetGatewayDevice.DeviceInfo.HardwareVersion"
    snapshot = ExportSnapshot(
        "Huawei",
        None,
        "MODEL",
        None,
        "SERIAL-1",
        "",
        (),
        (ExportValue(path, "xsd:unknown", "SECRET-RAW", at, op),),
        (
            ExportOperation(
                op,
                "get_parameter_values",
                "device_info",
                None,
                (path,),
                (path, "InternetGatewayDevice.DeviceInfo.SoftwareVersion"),
                "succeeded",
                None,
                None,
                at,
            ),
        ),
        False,
    )
    document = export_document(snapshot)
    assert all(item["value"] is None for item in document["values"])
    assert {item["reason"] for item in document["values"]} == {
        "type_not_allowed_for_path",
        "typed_observation_not_retained",
    }
    assert "SECRET-RAW" not in export_xml(document).decode()
