"""FastAPI application entrypoint.

Routers live one-module-per-PRD-section under app/api (claude.md §2):
auth, admin (super-admin console), companies, jobs, candidates, matching,
verification, dashboard, portal (candidate portal).
"""
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware

from app.api import (
    assessments,
    admin,
    auth,
    bd,
    billing,
    candidates,
    companies,
    dashboard,
    emails,
    jobs,
    matching,
    outreach,
    pipeline,
    portal,
    provider,
    telemetry,
    verification,
)
from app.core.config import get_settings

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("startup", environment=get_settings().environment)
    # Warm the engine + session factory once at boot rather than on the first
    # request, so the very first page load does not pay for pool construction.
    from app.core.db import get_session_factory

    get_session_factory()
    # Delivery-credential preflight: log a loud WARNING (not a hard crash, dev
    # without keys must still boot) if the Gmail SMTP or MSG91 config is
    # missing. Gmail SMTP is the only outbound mail path (claude.md rule 5);
    # legacy email-provider integrations have been removed from the codebase.
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

# ── Middleware ───────────────────────────────────────────────────────────────
# Starlette wraps in reverse order of registration: the LAST one added is the
# outermost. Registration order below is therefore CORS (innermost), then GZip,
# then the perf timer (outermost) so the timer measures the whole stack.

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        get_settings().frontend_url,
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # Browsers otherwise re-run the OPTIONS preflight before EVERY cross-origin
    # request. A dashboard firing eight calls paid eight extra round trips; with
    # this the browser caches the preflight for 10 minutes.
    max_age=600,
    # Diagnostics headers must be readable by the browser's network panel.
    expose_headers=["Server-Timing", "X-Query-Count"],
)

# JSON list payloads (the candidate table, the jobs board, the customers list)
# compress roughly 5:1. Below 1 KB compression costs more than it saves.
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Per-request timing + SQL query count. Diagnostics only, and only outside
# production (see app/core/instrumentation.py).
if not get_settings().is_production:
    from app.core.instrumentation import install_query_counter, timing_middleware

    install_query_counter()
    app.add_middleware(BaseHTTPMiddleware, dispatch=timing_middleware)

API_PREFIX = "/api/v1"
app.include_router(auth.router, prefix=f"{API_PREFIX}/auth", tags=["auth"])
app.include_router(admin.router, prefix=f"{API_PREFIX}/admin", tags=["admin"])
app.include_router(companies.router, prefix=f"{API_PREFIX}/companies", tags=["companies"])
app.include_router(jobs.router, prefix=f"{API_PREFIX}/jobs", tags=["jobs"])
app.include_router(candidates.router, prefix=f"{API_PREFIX}/candidates", tags=["candidates"])
app.include_router(matching.router, prefix=f"{API_PREFIX}/matching", tags=["matching"])
app.include_router(verification.router, prefix=f"{API_PREFIX}/verification", tags=["verification"])
app.include_router(outreach.router, prefix=f"{API_PREFIX}/outreach", tags=["outreach"])
app.include_router(dashboard.router, prefix=f"{API_PREFIX}/dashboard", tags=["dashboard"])
app.include_router(portal.router, prefix=f"{API_PREFIX}/portal", tags=["portal"])
app.include_router(telemetry.router, prefix=f"{API_PREFIX}/telemetry", tags=["telemetry"])
app.include_router(emails.router, prefix=f"{API_PREFIX}/emails", tags=["emails"])
app.include_router(pipeline.router, prefix=f"{API_PREFIX}/pipeline", tags=["pipeline"])
app.include_router(provider.router, prefix=f"{API_PREFIX}/provider", tags=["provider"])
# Business Development Portal, the fourth portal (/bd in the UI and the API).
app.include_router(bd.router, prefix=f"{API_PREFIX}/bd", tags=["bd"])
# Subscriptions + the credit ledger. Mounted at one path only (no /api/v2
# alias): it is new in this release, so there is no v1 client to keep working
# and a second prefix would just be a second URL for Razorpay's webhook to be
# configured against by mistake.
app.include_router(billing.router, prefix=f"{API_PREFIX}/billing", tags=["billing"])
app.include_router(assessments.router, prefix="/api/v2/assessments", tags=["assessments-v2"])

# ── /api/v2 aliases (2026-07-27 build spec) ──────────────────────────────────
# The spec names its new routes under /api/v2 (e.g. GET /api/v2/jobs/{job_id},
# PATCH /api/v2/companies/me/profile). The established contract is v1 and
# claude.md says to evolve additively rather than replace it, so the SAME
# router objects are mounted under both prefixes. One set of handlers, one set
# of permission checks — there is no second implementation to drift, and a v1
# client keeps working untouched.
app.include_router(jobs.router, prefix="/api/v2/jobs", tags=["jobs-v2"])
app.include_router(companies.router, prefix="/api/v2/companies", tags=["companies-v2"])
app.include_router(matching.router, prefix="/api/v2/matching", tags=["matching-v2"])
app.include_router(emails.router, prefix="/api/v2/emails", tags=["emails-v2"])
app.include_router(pipeline.router, prefix="/api/v2/pipeline", tags=["pipeline-v2"])
# The Provider Portal spec names its routes under /api/v2/provider. Same
# router object as the v1 mount above — one implementation, two prefixes.
app.include_router(provider.router, prefix="/api/v2/provider", tags=["provider-v2"])
app.include_router(bd.router, prefix="/api/v2/bd", tags=["bd-v2"])


@app.get("/health")
async def health() -> dict:
    # This endpoint is the staged-deploy gate, so a process-only response would
    # allow a revision with broken Cloud SQL credentials/networking to promote.
    from app.core.db import get_session_factory

    async with get_session_factory()() as session:
        await session.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok"}
