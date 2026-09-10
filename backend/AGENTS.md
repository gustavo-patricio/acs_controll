# Backend Instructions

## Architecture

- Keep `acs.domain` independent from FastAPI, Starlette, Celery, SQLAlchemy, and protocol adapters.
- Put use-case orchestration in `acs.application`.
- Keep REST transport code in `acs.api` and TR-069/CWMP transport code in `acs.cwmp`.
- Put database, messaging, object storage, and observability adapters in `acs.infrastructure`.
- Treat remote CPE actions as asynchronous, auditable operations.
- Keep Huawei model and firmware deviations isolated in `acs.cwmp.compatibility`.

## Engineering Rules

- Do not add production dependencies without documenting the reason and license.
- Do not log passwords, tokens, CPE credentials, or sensitive parameter values.
- Every new endpoint must have automated tests.
- Every database schema change must have a migration.
- Keep OpenAPI contracts and API errors explicit and versioned.
- Run lint, type checking, and tests before completing a change.
- Do not perform destructive operations against real CPEs outside an authorized environment.

## Commands

- Install or synchronize: `uv sync`
- Add a production dependency: `uv add <package>`
- Add a development dependency: `uv add --dev <package>`
- Lint: `uv run ruff check .`
- Format check: `uv run ruff format --check .`
- Type check: `uv run pyright`
- Test: `uv run pytest`

Use `uv` for dependency and virtual environment management. Do not install project dependencies directly with `pip`.
