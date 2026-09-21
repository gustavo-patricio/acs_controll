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

- Administrative API: http://127.0.0.1:8000/docs exposes `GET /health`.
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

## Validation

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
