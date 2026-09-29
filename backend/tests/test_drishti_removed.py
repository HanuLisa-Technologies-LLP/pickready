"""Drishti's code is gone, replaced by Leadership Intelligence. This keeps it gone.

Owner spec 2026-09-29, section 3.6 and rule 37.2: there is no second
leadership system beside the reworked one. Deleted, all of it: the `/drishti`
routes and the page behind them, the capture conversation and its model call,
the per-function compiler, the `DrishtiProfile` model, Sutra's
`function_strategic_context` key and its rule, and the capability
`author_drishti_profile` with every one of its grants (migration 0135).

KEPT, DELIBERATELY: the `drishti_profiles` TABLE, as history (S4: it holds a
customer's own words; migration 0135 carried each row across as a Functional
Head version). Migrations are a dated record and are not swept.
"""
from __future__ import annotations

import importlib.util
import re

import sqlalchemy as sa

from app.main import app
from app.services import capabilities
from tests.removal_sweep import BACKEND, sweep

PATTERN = re.compile(
    "|".join(
        (
            r"author_drishti_profile",
            r"AUTHOR_DRISHTI_PROFILE",
            r"authorDrishtiProfile",
            r"drishti_conversation",
            r"DrishtiProfile",
            r"hiring/drishti\b",
            r"hiring\.drishti\b",
            r"import drishti\b",
            r"/org/drishti",
            r"\"/drishti",
            r"function_strategic_context",
            r"strategic_context\(",
        )
    )
)

#: This file has to name what it forbids.
EXEMPT = (BACKEND / "tests" / "test_drishti_removed.py",)


def test_no_live_source_names_drishti_code() -> None:
    hits = sweep(PATTERN, exempt=EXEMPT)
    assert not hits, hits


def test_the_modules_are_gone() -> None:
    for module in (
        "app.api.drishti",
        "app.models.drishti",
        "app.services.hiring.drishti",
        "app.services.hiring.drishti_conversation",
    ):
        assert importlib.util.find_spec(module) is None, module


def test_no_route_is_mounted_under_drishti() -> None:
    # The OpenAPI paths, which is every mounted route however it is nested.
    paths = list(app.openapi()["paths"])
    assert paths, "no routes were read, so the check would pass by vacuum"
    assert not [path for path in paths if "/drishti" in path]
    assert any(path.startswith("/api/v1/leadership") for path in paths)


def test_the_capability_left_the_code_matrix() -> None:
    assert "author_drishti_profile" not in capabilities.ALL_CAPABILITIES
    for grants in capabilities.DEFAULT_PERMISSION_MATRIX.values():
        assert "author_drishti_profile" not in grants


async def test_the_capability_left_the_database_and_the_table_stayed() -> None:
    """0135 deletes every grant row, global and per tenant, and every overlay
    key, and KEEPS the drishti table (S4)."""
    from app.core.config import get_settings
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(sa.text("SET LOCAL app.bypass_rls = 'on'"))
            grants = (
                await conn.execute(
                    sa.text(
                        "SELECT count(*) FROM role_permissions "
                        "WHERE capability = 'author_drishti_profile'"
                    )
                )
            ).scalar_one()
            overlays = (
                await conn.execute(
                    sa.text(
                        "SELECT count(*) FROM users WHERE permissions_json ? "
                        "'author_drishti_profile'"
                    )
                )
            ).scalar_one()
            table = (
                await conn.execute(sa.text("SELECT to_regclass('public.drishti_profiles')"))
            ).scalar_one()
    finally:
        await engine.dispose()
    assert grants == 0
    assert overlays == 0
    assert table is not None
