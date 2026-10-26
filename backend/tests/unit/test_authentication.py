"""Authentication policy, hashing and safe transport parsing."""

import base64
from dataclasses import replace

import pytest
from httpx import ASGITransport, AsyncClient

from acs.api.app import create_app as admin_app
from acs.application.services.cpe_inventory import CpeInventoryService
from acs.application.services.inform import InformService
from acs.cwmp.compatibility.generic import GenericInventoryNormalizer
from acs.cwmp.http.app import create_app as cwmp_app
from acs.cwmp.soap.inform import parse_inform
from acs.domain.security import AccessDenied
from acs.infrastructure.security import accounts
from acs.infrastructure.security.passwords import hash_password, verify_password
from auth_support import ADMIN, PASSWORD, StubAuthenticator, principal_for
from contract.test_cpes import InventoryStub
from contract.test_cwmp import MemoryRepository


def test_shared_flag_cannot_authorize_an_admin_as_device() -> None:
    with pytest.raises(AccessDenied):
        replace(ADMIN, shared_cpe=True).require_device("A1B2C3", "LAB", "ONE")


@pytest.mark.parametrize(
    "arguments",
    [
        ["create", "admin", "test", "--shared"],
        ["rotate", "cpe", "test", "--shared"],
        ["create", "cpe", "test", "--shared", "--oui", "A1B2C3"],
    ],
)
def test_shared_cli_rejects_invalid_combinations(
    arguments: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.argv", ["acs-account", *arguments])
    with pytest.raises(SystemExit) as error:
        accounts.main()
    assert error.value.code == 2


def test_scrypt_is_salted_and_verifies_without_plaintext() -> None:
    first, second = hash_password(PASSWORD), hash_password(PASSWORD)
    assert first != second and PASSWORD not in first
    assert verify_password(PASSWORD, first)
    assert not verify_password("incorrect", first)
    assert not verify_password(PASSWORD, "invalid")


@pytest.mark.asyncio
async def test_services_cannot_authorize_anonymous_or_wrong_identity(
    inform_xml: bytes,
) -> None:
    inventory = CpeInventoryService(InventoryStub())
    with pytest.raises(AccessDenied):
        await inventory.list_cpes()
    with pytest.raises(AccessDenied):
        await inventory.for_principal(
            replace(ADMIN, permissions=frozenset())
        ).list_cpes()
    inform = parse_inform(inform_xml)
    service = InformService(MemoryRepository(), GenericInventoryNormalizer())
    with pytest.raises(AccessDenied):
        await service.receive(inform, None, principal=ADMIN)
    with pytest.raises(AccessDenied):
        await service.receive(
            inform,
            None,
            principal=replace(principal_for(inform), serial_number="OTHER"),
        )
    with pytest.raises(AccessDenied):
        await service.finish("cookie", principal=ADMIN)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "header",
    [
        None,
        "Bearer invalid",
        "Basic !!!",
        "Basic " + base64.b64encode(b"missing-colon").decode(),
        "Basic " + "x" * 2048,
    ],
)
async def test_missing_and_malformed_credentials_are_challenged(
    header: str | None,
) -> None:
    apps = [
        (
            admin_app(CpeInventoryService(InventoryStub()), StubAuthenticator()),
            "/api/v1/cpes",
            "GET",
            "acs-admin",
        ),
        (
            cwmp_app(
                InformService(MemoryRepository(), GenericInventoryNormalizer()),
                StubAuthenticator(),
            ),
            "/cwmp",
            "POST",
            "acs-cpe",
        ),
    ]
    for app, path, method, realm in apps:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="https://test"
        ) as client:
            response = await client.request(
                method, path, headers={"Authorization": header} if header else {}
            )
            assert response.status_code == 401
            assert realm in response.headers["www-authenticate"]
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["x-request-id"]


@pytest.mark.asyncio
async def test_plain_http_and_forwarded_header_cannot_bypass_tls(
    inform_xml: bytes,
) -> None:
    for app, path, method in (
        (
            admin_app(CpeInventoryService(InventoryStub()), StubAuthenticator()),
            "/api/v1/cpes",
            "GET",
        ),
        (
            cwmp_app(
                InformService(MemoryRepository(), GenericInventoryNormalizer()),
                StubAuthenticator(),
            ),
            "/cwmp",
            "POST",
        ),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.request(
                method,
                path,
                auth=("test", PASSWORD),
                headers={"X-Forwarded-Proto": "https"},
            )
            assert response.status_code == 403
            assert "www-authenticate" not in response.headers


@pytest.mark.asyncio
async def test_openapi_security_and_public_liveness() -> None:
    for app, path, scheme in (
        (
            admin_app(CpeInventoryService(InventoryStub()), StubAuthenticator()),
            "/api/v1/cpes",
            "AdminBasic",
        ),
        (
            cwmp_app(
                InformService(MemoryRepository(), GenericInventoryNormalizer()),
                StubAuthenticator(),
            ),
            "/cwmp",
            "CpeBasic",
        ),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="https://test"
        ) as client:
            schema = (await client.get("/openapi.json")).json()
            assert schema["components"]["securitySchemes"][scheme]["scheme"] == "basic"
            operation = schema["paths"][path][
                "get" if scheme == "AdminBasic" else "post"
            ]
            assert operation["security"] == [{scheme: []}]
            if scheme == "AdminBasic":
                assert (await client.get("/health")).status_code == 200
                assert "security" not in schema["paths"]["/health"]["get"]
