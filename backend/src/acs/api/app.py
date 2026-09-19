"""Administrative API application."""

from fastapi import FastAPI

from acs.api.routes.health import router as health_router

app = FastAPI(title="ACS Administrative API")
app.include_router(health_router)
