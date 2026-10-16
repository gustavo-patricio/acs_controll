# ACS Backend

## Local setup

Requirements:

- `uv`;
- Docker with Compose support.

Create the local configuration and synchronize the Python environment:

```bash
cp .env.example .env
uv sync
```

Start PostgreSQL from the repository root and verify the connection:

```bash
docker compose up -d postgres
cd backend
uv run acs-db-check
```

The development database is published only on `127.0.0.1:5432`. The `.env` file is local and must not be committed.

## Administrative API

Start the API from `backend/`:

```bash
uv run uvicorn acs.api.app:app --reload
```

`GET http://127.0.0.1:8000/health` returns `200` and `{"status":"healthy"}` when the API process is serving requests. This liveness endpoint does not check PostgreSQL; use `uv run acs-db-check` for database connectivity.

### Registered CPE inventory

`GET /api/v1/cpes` lists the CPEs persisted by Inform, using the same PostgreSQL
configuration as the CWMP receiver. Apply migrations before querying. The endpoint
is in the **administrative** Swagger UI at http://127.0.0.1:8000/docs, not port 7547.

```bash
curl 'http://127.0.0.1:8000/api/v1/cpes?limit=50&offset=0'
curl 'http://127.0.0.1:8000/api/v1/cpes?manufacturer=Huawei&serial_number=LAB000001'
```

The response contains `items`, `total` (after filtering), `limit` and `offset`.
Each item exposes `id`, `manufacturer`, `oui`, `product_class`, `serial_number`,
`model`, `firmware`, `hardware`, `first_seen` and `last_seen`. Unknown inventory
versions/model are `null`. Dates include a timezone. No credentials, raw parameters
or session cookies are returned. An empty inventory returns `200` with `items: []`.

- `limit`: default 50, range 1-100; `offset`: default 0, nonnegative.
- Optional `manufacturer` and `serial_number`: exact, case-sensitive matches,
  combined with AND, 1-64 characters each.
- Order: `last_seen` descending, then `id` ascending. Last seen is the last
  accepted Inform, not an online/offline determination.
- Count and items share a database snapshot per request. Pages may move between
  requests as new Inform messages update the inventory.
- Invalid query parameters return `422`; unavailable inventory returns `503`
  with `detail.code=inventory_unavailable` and a correlated `detail.request_id`
  / `X-Request-ID`, without database error details.

Administrative authentication is not implemented yet. Inventory is enabled only
in development/test with SQL echo disabled; otherwise this route returns `503`
while `/health` remains available. Keep the API bound to loopback or an isolated,
authorized lab network. Avoid access logs when querying identifying filters:

```bash
uv run uvicorn acs.api.app:app --host 127.0.0.1 --port 8000 --no-access-log
```

This read-only delivery adds no database columns or migration. Additional
search/sort options and authentication/RBAC remain future work.

### CPE details, sessions and events

Use the `id` returned by the inventory, not the serial number. All three routes
are available in the administrative Swagger UI at http://127.0.0.1:8000/docs.
They use the same development/test restriction and database as the inventory.

```bash
# Replace with an id from GET /api/v1/cpes.
CPE_ID='00000000-0000-0000-0000-000000000000'
curl "http://127.0.0.1:8000/api/v1/cpes/$CPE_ID"
curl "http://127.0.0.1:8000/api/v1/cpes/$CPE_ID/sessions?limit=50&offset=0"
curl "http://127.0.0.1:8000/api/v1/cpes/$CPE_ID/events?limit=50&offset=0"
```

- Detail returns the same public device fields as a list item, without a page
  wrapper. Missing model/version data remains `null`; no new CPE RPC is sent.
- Sessions return `items`, `total`, `limit`, `offset`. Each item contains `id`,
  `cpe_id`, `version`, `state`, `started_at`, `expires_at`, `completed_at`.
  Order is `started_at` descending, then session `id` ascending.
- Events use the same page wrapper. Each item contains `session_id`, `position`
  (zero-based position within the Inform), `code`, `received_at`. Order is session
  `started_at` descending, session `id` ascending, then `position` ascending.
  `received_at` is the ACS session start timestamp, not the event occurrence time
  on the CPE; the database does not store an independent event timestamp.
- Both histories accept `limit` 1-100 (default 50) and `offset` >= 0 (default 0).
  Count, parent existence and items are read from one snapshot per request.
- A known CPE with no history, or a page beyond the end, returns `200` and an empty
  `items` list (the total still counts all matching records).
- An unknown UUID returns `404` with `detail.code=cpe_not_found`. Database/service
  unavailability returns `503` with `detail.code=inventory_unavailable`. These
  errors carry a correlated `detail.request_id` and `X-Request-ID`. Malformed
  UUIDs and invalid pagination return the standard FastAPI `422` validation error.

Reads do not change sessions: `awaiting_empty` may already be expired, and
`completed` does not imply that the CPE is currently online. Tokens, token hashes,
RPC IDs, command keys, raw SOAP and parameter values are not exposed. No migration
is needed because these routes only read existing tables.

The new HTTP response contracts use Pydantic models; application records remain
plain dataclasses. This is the backend portion of RF-028/RF-035 and part of RF-036,
plus session/event visibility from the roadmap. It does not complete the UI,
authentication, online/offline classification, IP inventory or remote refresh.

Validate the HTTP contracts and PostgreSQL behavior from `backend/`:

```bash
uv run pytest tests/contract/test_cpes.py -v
ACS_RUN_INTEGRATION_TESTS=1 uv run pytest tests/integration/test_cwmp_persistence.py -v
```

PostgreSQL tests migrate and remove only disposable `test_cwmp_*` schemas, leaving
the real lab inventory untouched. No real CPE connection is needed for these tests.

## Development CWMP receiver

From `backend/`, with PostgreSQL running and `.env` configured:

```bash
uv sync --locked
uv run alembic upgrade head
uv run uvicorn acs.cwmp.http.app:app --host 127.0.0.1 --port 7547 --no-access-log
```

The separate CWMP application serves `POST /cwmp`. It currently accepts only the
CWMP 1.0 Inform profile. It refuses startup outside development/test or with SQL
echo enabled. See [CWMP decisions](docs/CWMP_FIRST_DELIVERY.md) for protocol scope,
session cookies, error behavior and remaining authentication work.

### Swagger UI

- Administrative API: http://127.0.0.1:8000/docs exposes `GET /health`, the CPE list,
  individual details, sessions and events.
- CWMP receiver: http://127.0.0.1:7547/docs exposes `POST /cwmp`.

Run both commands above in separate terminals to access both applications.
In the CWMP Swagger UI, expand `POST /cwmp`, select **Try it out**, and execute
the synthetic `inform` example with `text/xml`. Expect `200` with an XML
InformResponse. Then clear the request body completely (no spaces, quotes or
newlines) and execute again within 60 seconds: expect `204` with no body.
The browser manages the HttpOnly session cookie automatically on the same origin.
These requests write to the configured database; use only development/test data.
The minimal example exercises this receiver, not full CPE conformance.
Swagger UI assets require internet access to the default CDN.

### WSL2 LAN access and port forwarding recovery

For the NAT-based lab setup, keep PostgreSQL running and start the receiver
from `backend/` in WSL, listening beyond loopback:

```bash
uv run uvicorn acs.cwmp.http.app:app --host 0.0.0.0 --port 7547 --no-access-log
```

Confirm the current addresses with `ip -4 addr show eth0` in WSL and `ipconfig`
in Windows. The lab addresses below were Windows `192.168.18.7` and WSL
`172.23.109.98`; replace them if they change. The Windows address must be the
computer's LAN IPv4 address, not the default gateway.

If LAN access stops working, open **PowerShell as Administrator**, inspect the
rules, then remove the two overlapping ACS rules and recreate only the LAN rule:

```powershell
netsh interface portproxy show all

netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=7547
netsh interface portproxy delete v4tov4 listenaddress=192.168.18.7 listenport=7547

netsh interface portproxy add v4tov4 listenaddress=192.168.18.7 listenport=7547 connectaddress=172.23.109.98 connectport=7547
```

Only remove entries belonging to this lab. If an old rule uses another Windows
IP, use that old address when deleting it. A missing rule may report that it
could not be found; do not reset unrelated portproxy rules.

Verify both the direct WSL connection and the forwarded connection:

```powershell
Get-NetTCPConnection -LocalPort 7547 -State Listen
curl.exe --noproxy "*" --max-time 5 http://172.23.109.98:7547/openapi.json
curl.exe --noproxy "*" --max-time 5 http://192.168.18.7:7547/openapi.json
```

If WSL responds but the Windows LAN listener is still missing, restart IP Helper
and repeat the checks. This can temporarily interrupt other Windows forwarding:

```powershell
Restart-Service iphlpsvc
```

Keep the Windows firewall rule restricted to the authorized CPE/test device IPs.
Test `http://192.168.18.7:7547/openapi.json` from an authorized LAN device, then
configure the CPE ACS URL as `http://192.168.18.7:7547/cwmp`. A local Windows test
alone does not verify inbound firewall access from the LAN.

Port forwarding does not start Uvicorn or PostgreSQL. After a restart, ensure
both are running and update the forwarding destination if the WSL IP changed.
Do not expose this unauthenticated development receiver to the internet.

## Validation

### CPE simulator as pytest tests

The simulator lives only in `tests/cwmp_simulator.py`, uses synthetic identity and
current UTC time, and performs Inform/InformResponse followed by an empty POST.
It checks SOAP namespaces, echoed ID, MaxEnvelopes=1, cookies and HTTP 200/204.
It does not implement other RPCs or access PostgreSQL directly.

Run its self-tests without Docker or a running server:

```bash
uv run pytest tests/unit/test_cpe_simulator.py -v
```

To exercise actual HTTP, start PostgreSQL, apply migrations and run the CWMP
receiver using the commands above. Then, in another terminal under `backend/`:

```bash
ACS_RUN_LIVE_TESTS=1 uv run pytest tests/live/test_live_cpe_simulator.py -v
```

The live test is disabled by default and targets only `http://127.0.0.1:7547/cwmp`
(no redirects or environment proxies). It runs two sessions with the same random
synthetic identity. It leaves one synthetic CPE and two sessions in the configured
development database; it does not delete records. Use a lab database, not production.
A failure or unavailable receiver makes pytest fail instead of silently skipping.
Do not use `--showlocals`, HTTP debug logging or real credentials with these tests.

Verify persistence/idempotency separately, using a disposable migrated PostgreSQL
schema, without needing Uvicorn:

```bash
ACS_RUN_INTEGRATION_TESTS=1 uv run pytest tests/integration/test_cwmp_persistence.py -k simulated -v
```

This asserts one CPE, two completed sessions and two events. The live test alone
validates the wire exchange, not database rows. This minimal simulator is not a
full TR-069 conformance test and does not replace testing a real Huawei CPE.

### Quality checks

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
```

Run PostgreSQL integration tests while the Compose service is healthy:

```bash
ACS_RUN_INTEGRATION_TESTS=1 uv run pytest -m integration
```
