# Backend dependencies

Direct dependencies must have a defined purpose and a license compatible with the project policy.

| Dependency | Scope | Purpose | License |
| --- | --- | --- | --- |
| defusedxml | Runtime | Reject DTDs, entities and external references when parsing SOAP | PSF-2.0 |
| types-defusedxml | Development | Static typing for the XML adapter | Apache-2.0 |
| FastAPI | Runtime | Administrative HTTP API and OpenAPI contract | MIT |
| Uvicorn | Runtime | ASGI server for the administrative API | BSD-3-Clause |
| HTTPX | Development | FastAPI HTTP contract tests via TestClient | BSD-3-Clause |
| SQLAlchemy | Runtime | Async database engine, sessions, and persistence mapping | MIT |
| Psycopg, Psycopg Binary, Psycopg Pool | Runtime | PostgreSQL driver and connection-pool support | LGPL-3.0-only |
| Pydantic Settings | Runtime | Typed configuration loaded from environment variables | MIT |
| Alembic | Runtime | Versioned PostgreSQL schema migrations | MIT |
| pytest-asyncio | Development | Async database integration tests | Apache-2.0 |

Versions are resolved in `uv.lock`. Transitive dependencies remain subject to automated license review when that pipeline is introduced.
