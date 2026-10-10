"""Validate the simulator itself, including failures rather than only happy paths."""

import pytest
from httpx import AsyncClient, MockTransport, ReadTimeout, Request, Response

from acs.cwmp.soap.inform import parse_inform, serialize_inform_response
from cwmp_simulator import build_inform, simulate_session, validate_response


def test_generated_inform_is_accepted_by_server_parser() -> None:
    inform = parse_inform(build_inform("SIM<&1", "id<&1"))
    assert inform.device.serial_number == "SIM<&1"
    assert inform.rpc_id == "id<&1"
    assert inform.current_time.utcoffset() is not None
    assert inform.events[0].code == "1 BOOT"


@pytest.mark.parametrize(
    "payload",
    [
        b"<broken",
        b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>',
        serialize_inform_response("wrong-id"),
        serialize_inform_response("expected").replace(b">1<", b">2<"),
    ],
)
def test_invalid_responses_are_rejected(payload: bytes) -> None:
    with pytest.raises(AssertionError):
        validate_response(payload, "expected")


@pytest.mark.asyncio
async def test_client_preserves_cookie_and_sends_zero_bytes() -> None:
    calls = 0

    def handle(request: Request) -> Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            inform = parse_inform(request.content)
            return Response(
                200,
                content=serialize_inform_response(inform.rpc_id),
                headers={
                    "Content-Type": "text/xml",
                    "Set-Cookie": "acs_cwmp_session=synthetic; Path=/cwmp; HttpOnly",
                },
            )
        assert request.content == b""
        assert request.headers["cookie"] == "acs_cwmp_session=synthetic"
        return Response(
            204, headers={"Set-Cookie": "acs_cwmp_session=; Max-Age=0; Path=/cwmp"}
        )

    async with AsyncClient(transport=MockTransport(handle)) as client:
        await simulate_session(client, "http://test/cwmp", "SIM1")
    assert calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [False, True])
async def test_network_and_http_failures_are_not_success(timeout: bool) -> None:
    def handle(request: Request) -> Response:
        if timeout:
            raise ReadTimeout("Synthetic timeout", request=request)
        return Response(503)

    async with AsyncClient(transport=MockTransport(handle)) as client:
        with pytest.raises(ReadTimeout if timeout else AssertionError):
            await simulate_session(client, "http://test/cwmp", "SIM1")
