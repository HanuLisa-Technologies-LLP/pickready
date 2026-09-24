"""The tenant-owned employer-verification system is RETIRED (vivekium C8).

Owner-ruled 2026-09-18: the brief describes the candidate-owned system
(candidate_employments, bgv_verifications, the seven-item checkbox form),
so the older tenant-owned one goes the way the 2026-09-09 and
2026-09-10 removals went,
with a sweep rather than a memory.

AMENDED Phase 6 (CONTRACT v3): the TABLE is dropped too, by migration 0122,
and only when it is EMPTY. The upgrade RAISES naming the count when a row
exists, because deleting what an employer actually said about a verification
that really ran is an owner decision, never a migration's. Pilot counted zero
rows on 2026-09-24. The 40-aspect outreach route that shared this router,
`POST /verification/outreach`, is retired in the same phase.

The sweep covers live source only. Migrations, docs and history keep the
name, because they record what was true when they were written.
"""
from __future__ import annotations

import asyncio
import pathlib

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

BACKEND = pathlib.Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
FRONTEND = BACKEND.parent / "frontend"

#: The retired system's names. `verification_requests` itself is allowed in
#: exactly one live place, the models package's comment trail, and is
#: otherwise the table's name in MIGRATIONS, which are provenance.
RETIRED_NAMES = (
    "VerificationRequest",
    "send_verification_requests",
    "parse_verification_reply",
    "verification_parsing",
    "EMPLOYER_FORM_FIELDS",
    "employer_seq",
)

#: This test, and the API contract doc trail, may speak the names.
_ALLOWED = {"test_verification_requests_retired.py"}


def _live_python():
    for path in APP.rglob("*.py"):
        if path.name in _ALLOWED:
            continue
        yield path


def test_the_retired_system_is_gone_from_live_backend_source() -> None:
    offenders: list[str] = []
    for path in _live_python():
        text = path.read_text(encoding="utf-8", errors="replace")
        for name in RETIRED_NAMES:
            if name in text:
                offenders.append(f"{path.relative_to(BACKEND)}: {name}")
    assert not offenders, (
        "the retired employer-verification system is still named in live "
        f"source: {offenders}. It was retired under C8; the surviving system "
        "is candidate_employments + bgv_verifications + /bgv/form/{token}."
    )


def test_the_retired_routes_are_not_registered() -> None:
    from app.main import app

    # THE PUBLISHED PATHS, NOT `app.router.routes`.
    #
    # Reading `.path` off every entry in `app.router.routes` was reading a
    # private shape, and FastAPI changed it: from 0.141 `include_router`
    # leaves a single `_IncludedRouter` in that list, which carries `path =
    # None` and does not expose `.routes`, so the comprehension raised
    # `AttributeError` and the nested routes were unreachable from it anyway.
    # `app.openapi()["paths"]` is the documented surface, returns fully
    # prefixed paths, and gives byte-identical answers on 0.136 and 0.141.
    #
    # It reports only routes in the schema, so a retired path hidden with
    # `include_in_schema=False` would read as absent. The survivor assertion
    # below is what stops that being a vacuous pass, and it is why that
    # assertion is load bearing rather than decorative.
    paths = set(app.openapi()["paths"])
    for gone in (
        "/api/v1/verification/form/{token}",
        "/api/v1/verification/profile/{profile_id}",
        "/api/v1/verification/requests/{request_id}/override",
        "/api/v1/verification/outreach",
    ):
        assert gone not in paths, f"{gone} is still registered"
    # The survivors, so this test cannot pass by the router being empty.
    assert "/api/v1/verification/inbound-email" in paths
    assert "/api/v1/bgv/form/{token}" in paths


def test_the_retired_frontend_intake_is_gone() -> None:
    offenders: list[str] = []
    for path in FRONTEND.rglob("*.tsx"):
        if "node_modules" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "employer_emails" in text or "VerificationFormInfo" in text:
            offenders.append(str(path.relative_to(FRONTEND)))
    assert not offenders, f"retired intake still in the frontend: {offenders}"


def test_the_retired_table_is_dropped_from_the_migrated_schema() -> None:
    """Migration 0122 dropped it, read from the database rather than from the
    migration file: a guard that raised, or a drop that was skipped, would
    leave the file saying one thing and the schema another."""
    from app.core.config import get_settings

    async def _exists() -> bool | None:
        engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
        try:
            async with engine.connect() as conn:
                return bool(
                    (
                        await conn.execute(
                            sa.text("SELECT to_regclass('public.verification_requests')")
                        )
                    ).scalar()
                )
        except (OSError, sa.exc.DBAPIError):
            return None
        finally:
            await engine.dispose()

    loop = asyncio.new_event_loop()
    try:
        exists = loop.run_until_complete(_exists())
    finally:
        loop.close()
    if exists is None:
        pytest.skip("no database reachable -- the schema cannot be read")
    assert exists is False, "verification_requests still exists after migration 0122"
