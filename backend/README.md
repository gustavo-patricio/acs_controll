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

## Quality checks

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
