"""FastAPI application entrypoint.

Routers live one-module-per-PRD-section under app/api (claude.md §2):
auth, admin (super-admin console), companies, jobs, candidates, matching,
verification, dashboard, portal (candidate portal).
"""
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    admin,
    auth,
    candidates,
    companies,
    dashboard,
    jobs,
    matching,
    portal,
    telemetry,
    verification,
)
from app.core.config import get_settings

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("startup", environment=get_settings().environment)
    # Delivery-credential preflight: log a loud WARNING (not a hard crash — dev
    # without keys must still boot) if Resend/MSG91 config is missing.
    from app.core.config import preflight_delivery_config

    missing = preflight_delivery_config()
    if missing:
        log.warning("delivery.preflight_missing_keys", missing=missing)
    yield
    from app.core.db import get_engine
    await get_engine().dispose()


app = FastAPI(
    title="PickReady API",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_PREFIX = "/api/v1"
app.include_router(auth.router, prefix=f"{API_PREFIX}/auth", tags=["auth"])
app.include_router(admin.router, prefix=f"{API_PREFIX}/admin", tags=["admin"])
app.include_router(companies.router, prefix=f"{API_PREFIX}/companies", tags=["companies"])
app.include_router(jobs.router, prefix=f"{API_PREFIX}/jobs", tags=["jobs"])
app.include_router(candidates.router, prefix=f"{API_PREFIX}/candidates", tags=["candidates"])
app.include_router(matching.router, prefix=f"{API_PREFIX}/matching", tags=["matching"])
app.include_router(verification.router, prefix=f"{API_PREFIX}/verification", tags=["verification"])
app.include_router(dashboard.router, prefix=f"{API_PREFIX}/dashboard", tags=["dashboard"])
app.include_router(portal.router, prefix=f"{API_PREFIX}/portal", tags=["portal"])
app.include_router(telemetry.router, prefix=f"{API_PREFIX}/telemetry", tags=["telemetry"])


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
