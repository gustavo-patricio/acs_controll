# Database migrations

Run `uv run alembic upgrade head` from `backend/` to apply migrations using
`ACS_DATABASE_URL` from the environment or local `.env`.

`uv run alembic check` verifies that models and the migrated database agree.
Migration `0001_cwmp_inform` creates `cpes`, `cwmp_sessions`, and `cwmp_events`.
Downgrade removes these tables and their data; only use it in disposable databases.
Integration tests exercise upgrade and downgrade in their own temporary schemas.
