"""Explicit synthetic authentication for protocol and inventory contract tests."""

from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from fastapi import FastAPI
from httpx import AsyncBaseTransport, AsyncClient

from acs.api.app import create_app as real_admin_app
from acs.application.services.cpe_inventory import CpeInventoryService
from acs.application.services.inform import InformService
from acs.cwmp.http.app import create_app as real_cwmp_app
from acs.domain.cpe.inform import Inform
from acs.domain.security import AuthenticationFailed, Principal

PASSWORD = "synthetic-test-password"
ADMIN = Principal(
    uuid5(NAMESPACE_URL, "test-admin"), "admin", frozenset({"inventory:read"})
)


def device_principal(oui: str, product_class: str, serial: str) -> Principal:
    return Principal(
        uuid5(NAMESPACE_URL, f"test-cpe:{oui}:{product_class}:{serial}"),
        "cpe",
        frozenset(),
        oui,
        product_class,
        serial,
    )


def principal_for(inform: Inform) -> Principal:
    d = inform.device
    return device_principal(d.oui, d.product_class, d.serial_number)


class StubAuthenticator:
    async def authenticate(
        self, username: str, password: str, kind: Literal["admin", "cpe"]
    ) -> Principal:
        if password != PASSWORD:
            raise AuthenticationFailed
        if kind == "admin" and username == "test":
            return ADMIN
        devices = {
            "test": ("LAB-ONT", "LAB000001"),
            "swagger": ("LAB", "SWAGGER0001"),
            "simulator": ("SIMULATOR", "SIM-INTEGRATION"),
        }
        if kind == "cpe" and username in devices:
            product_class, serial = devices[username]
            return device_principal("A1B2C3", product_class, serial)
        raise AuthenticationFailed


def admin_app(inventory: CpeInventoryService | None = None) -> FastAPI:
    return real_admin_app(inventory, StubAuthenticator())


def cwmp_app(service: InformService | None = None) -> FastAPI:
    return real_cwmp_app(service, StubAuthenticator())


def authorized_client(
    *, transport: AsyncBaseTransport, base_url: str = "https://test"
) -> AsyncClient:
    return AsyncClient(
        transport=transport,
        base_url=base_url.replace("http://", "https://"),
        auth=("test", PASSWORD),
    )
