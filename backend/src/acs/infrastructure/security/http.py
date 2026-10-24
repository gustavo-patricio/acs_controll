"""HTTP Basic over TLS, shared transport logic for two separate security realms."""

import base64
import binascii
from typing import Literal

from fastapi import Request

from acs.application.services.authentication import Authenticator
from acs.domain.security import (
    AuthenticationFailed,
    AuthenticationUnavailable,
    Principal,
)


class InsecureTransport(Exception):
    """Credentials must not be submitted over plaintext HTTP."""


async def authenticate_request(
    request: Request,
    authenticator: Authenticator | None,
    kind: Literal["admin", "cpe"],
) -> Principal:
    if request.url.scheme != "https":
        raise InsecureTransport
    headers = request.headers.getlist("authorization")
    if len(headers) != 1 or len(headers[0]) > 2048:
        raise AuthenticationFailed
    scheme, separator, value = headers[0].partition(" ")
    if not separator or scheme.lower() != "basic":
        raise AuthenticationFailed
    try:
        decoded = base64.b64decode(value, validate=True).decode("utf-8")
        username, separator, password = decoded.partition(":")
        if not separator or not username or not password:
            raise AuthenticationFailed
        if any(ord(char) < 32 or ord(char) == 127 for char in decoded):
            raise AuthenticationFailed
    except (ValueError, UnicodeError, binascii.Error):
        raise AuthenticationFailed from None
    if authenticator is None:
        raise AuthenticationUnavailable
    return await authenticator.authenticate(username, password, kind)
