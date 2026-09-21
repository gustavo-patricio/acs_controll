# First CWMP vertical delivery

## Scope and normative references

This is a development implementation of Inform/InformResponse and empty HTTP
continuation. The accepted profile is SOAP 1.1 with CWMP namespace
`urn:dslforum-org:cwmp-1-0` (CWMP 1.0). Other CWMP namespaces are rejected rather
than silently mapped. This is not a claim of full TR-069 conformance or Huawei
firmware homologation.

References used:

- [BBF CWMP 1.0 schema](https://github.com/BroadbandForum/cwmp-data-models/blob/master/cwmp-1-0.xsd).
- [TR-069](https://www.broadband-forum.org/pdfs/tr-069-1-0-0.pdf), A.3.3.1 (Inform), A.5.2 (ACS faults).
- [TR-069 Amendment 6 Corrigendum 1](https://www.broadband-forum.org/pdfs/tr-069-1-6-1.pdf), 3.4.2 (session cookies), 3.4.6 (empty HTTP responses), 3.5 (SOAP), 3.7 (sessions).
- [defusedxml](https://github.com/tiran/defusedxml) for explicit DTD/entity/external-resource rejection.

InformResponse echoes the received ID and returns MaxEnvelopes=1. A CPE returns
the session cookie on its subsequent empty POST; the ACS responds with HTTP 204
and no body when it has nothing to send. These are HTTP exchanges within one CWMP
session, not server-initiated requests to the CPE.

## Architecture and files

- `domain/cpe/inform.py`: immutable DeviceId, events, parameters and Inform observations; standard library only.
- `domain/events/session.py`: session state and continuation validation.
- `application/services/inform.py`: repository port and use cases; hashes session tokens.
- `cwmp/soap/inform.py`: XML parsing and serialization, isolated from persistence and HTTP.
- `cwmp/http/app.py`: separate FastAPI adapter, body limits and HTTP/cookie handling.
- `infrastructure/database/cwmp_models.py` and `inform_repository.py`: SQLAlchemy mappings and atomic PostgreSQL transactions.
- `migrations/versions/0001_cwmp_inform.py`: explicit migration; no automatic table creation on application startup.

## Persistence and session semantics

`cpes` has a unique key `(oui, product_class, serial_number)`. Manufacturer is a
display field, so changing it does not create another device. OUI is normalized
to uppercase. PostgreSQL ON CONFLICT updates the existing row safely under
concurrent Informs. Server timestamps track first/last reception independently
of the device clock.

Each first Inform creates a session and its ordered events in the same transaction
as the CPE upsert. Acknowledgement follows commit. The session records ID, version,
device CurrentTime, RetryCount, MaxEnvelopes, parameter count and server timestamps.
Events retain EventCode and CommandKey; neither value is written to logs.

Every session gets a random 256-bit token in an HttpOnly cookie scoped to `/cwmp`.
Only its SHA-256 hash is stored. The log correlation ID is a separate UUID, not
the cookie. Cookie identity never relies on source IP or cwmp:ID alone. Cookies
are session cookies (no persistent Max-Age) and Secure on HTTPS. They are not
device authentication.

`awaiting_empty` becomes `completed` on the first valid empty POST. The fixed
session lifetime is 60 seconds; expired, unknown, missing or completed tokens
cannot continue a session. Completion takes a row lock. Repeated Inform with the
same active cookie, device identity, RPC ID and version returns the existing
receipt without duplicating events (first accepted contents win). Without a
cookie it creates a new session but upserts the same CPE, even with a reused RPC ID.

There is no operation queue in this delivery: no operations can be registered,
so every valid empty continuation completes the session. Abandoned sessions remain
`awaiting_empty` with an expired timestamp until a future retention job handles
them. Multiple sessions for one CPE are currently allowed.

## Parser and data policy

The XML adapter extracts DeviceId, Event, MaxEnvelopes, CurrentTime, RetryCount,
ParameterList, CWMP namespace/version and ID. Parameters retain lexical value and
lexical xsi:type; it does not perform full XSD coercion or external schema loading.
Only SoftwareVersion, HardwareVersion and ModelName under Device.DeviceInfo or
InternetGatewayDevice.DeviceInfo are eligible for inventory storage. Arbitrary
parameter values and raw SOAP payloads are discarded after processing.

DTD, entity declarations and external references are prohibited by defusedxml.
Input is limited to 1 MiB, depth 32, 64 events, 4096 parameters and bounded fields;
the HTTP body has a 10-second read deadline. Limits are local resource policies.
The adapter validates namespaces, required fields, duplicate/missing structures,
array counts when declared, unsigned integers, OUI and timezone-bearing dates.
Only a single inline SOAP body element is accepted. A bounded nonempty ID header
is required. SOAP multi-reference encoding, nested values, compressed HTTP bodies,
version negotiation and schema validation are not supported yet.

## Errors and logs

| Condition | HTTP response | SOAP detail |
| --- | --- | --- |
| Valid Inform, committed | 200 text/xml | InformResponse |
| Valid empty continuation | 204, empty | None |
| Supported envelope but invalid Inform arguments | 500 text/xml | 8003 Invalid arguments, SOAP Client |
| Unsupported method in accepted namespace | 500 text/xml | 8000 Method not supported, SOAP Server |
| Unsafe/malformed XML, unsupported namespace, missing ID | 400, empty | None |
| Missing, expired or invalid session cookie | 400, empty | None |
| Body too large | 413, empty | None |
| Unsupported content type or encoding | 415, empty | None |
| Body read timeout | 408, empty | None |
| Persistence unavailable | 503, empty | No InformResponse |

8000 and 8003 are ACS codes from A.5.2, not invented application codes. Faults use
the same ID, SOAP faultstring `CWMP fault` and CWMP Fault detail. Other errors are
local HTTP transport rejections without invented CWMP codes. No other RPC handler
is implemented.

The `acs.cwmp` logger emits JSON with allowlisted event, server request UUID, HTTP
status, session UUID, CPE UUID and version. Raw ID, identity strings, SOAP, parameter
values, headers, cookie tokens and exception messages are excluded. Run Uvicorn
with `--no-access-log` to avoid query-string/access logging. SQL echo is rejected
for this receiver, and SQLAlchemy exception parameters are hidden.

## Fixtures and tests

Fixtures in `tests/fixtures/cwmp` are generated laboratory data, not captures from
real Huawei devices. Serial/OUI/product and credentials are fictional. The secret
sentinel deliberately exercises log exclusion. XXE and malformed fixtures must be
rejected before any repository access.

Run from `backend/`:

```bash
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest -m 'not integration'
ACS_RUN_INTEGRATION_TESTS=1 uv run pytest
uv run alembic upgrade head
uv run alembic check
```

Integration tests create random `test_cwmp_*` schemas on the configured PostgreSQL,
apply Alembic and drop only those schemas on exit. The database user needs schema
creation privileges. They test atomic upsert, concurrent/repeated Informs, events,
session completion/expiry, rollback, actual HTTP flow and migration/model parity.
Upgrade/downgrade testing occurs only in the temporary schemas.

## Next delivery

The current receiver is development/test only and refuses production/staging
startup. Authentication policy, TLS/reverse-proxy deployment, binding authenticated
device identity to sessions, rate limits, global concurrency limits and real CPE
homologation are prerequisites for deployment. Do not expose it to the Internet.

Next work also includes REST inventory queries, Huawei fixtures and version
negotiation, retention/expired-session cleanup, metrics, richer data-model mapping
and an operation scheduler. None of GetParameterValues, SetParameterValues, Reboot,
Connection Request, Download or firmware management is implemented.

Traceability: RF-012/013/015/018/019/063, RN-001/008/010. This task covers their
Inform ingestion foundation, not every inventory field or the full original
Entrega 1 acceptance criteria (which also require authentication and REST queries).
