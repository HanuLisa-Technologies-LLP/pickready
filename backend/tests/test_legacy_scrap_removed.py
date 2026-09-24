"""The legacy scrap (Phase 7 Wave B, WP-B2) and the guard that made it safe.

WHAT WENT, 2026-09-24
---------------------
Migration 0128_legacy_scrap dropped four tables the pilot probe (CONTRACT v3)
found empty: the per-job preset technical bank, the per-candidate technical
track, the multi-vendor LLM key roster and the login one-time-code challenges.
It dropped two columns nothing read (the retired technical-approval stamp on
`jobs` and the stored deficit flag on `tenants`), deleted the role_permissions
rows of the learning-revocation capability, and moved the resume provider's
server default to the store actually used. The ORM mappings, the enums, the
credit helpers and the capability constant went in the same change.

WHY A DROP IS SAFE HERE WHEN S4 SAID NO DROP
--------------------------------------------
Every drop is preceded by a count that RAISES and aborts the whole upgrade if
anything is there, so the migration can only ever delete an empty object. The
guard is the whole argument, which is why it is tested here against real SQL
rather than trusted: a guard that reports "empty" over rows it cannot see is
worse than no guard, and FORCE row level security makes exactly that happen
for a migration role outside the bypass scope.

THE SWEEP AND ITS PENDING LIST
------------------------------
`PENDING` names files other packages of this release own and are already
rewriting or deleting. It is a RATCHET in both directions, the
`test_login_otp_removed` shape: a mention anywhere else fails, and an entry
whose file no longer mentions the names fails too, so the list only shrinks.
The multi-vendor names are swept by `test_multivendor_ai_removed.py`.
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import types

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from tests.removal_sweep import BACKEND, sweep

MIGRATION_PATH = BACKEND / "alembic" / "versions" / "0128_legacy_scrap.py"
THIS_FILE = pathlib.Path(__file__).resolve()
#: The sibling removal sweep of the same change asserts its own names are
#: absent by naming them, the same reason this file is exempt from itself.
SIBLING = THIS_FILE.parent / "test_multivendor_ai_removed.py"

GONE = re.compile(
    r"\bTechnicalQuestion\b|\bCandidateTechnicalQuestion\b"
    r"|candidate_technical_questions|has_credit_headroom|_sync_deficit"
    r"|\bcredit_deficit\b|REVOKE_AGENT_LEARNINGS|revoke_agent_learnings"
    r"|questions_approved_at"
)

#: file -> the package that removes the mention. Each is a comment or a write
#: to an attribute that is no longer mapped, never a read of a dropped object.
PENDING: dict[str, str] = {
    # Phase 1 rewrote the setup gate comments; gone on the Phase 1 line.
    "backend/app/api/assessments.py": "Phase 1",
    # Phase 1 rewrote this script; its merge must drop the one stamp line.
    "backend/app/scripts/backfill_functional_reports.py": "Phase 1",
    # Both deleted by Phase 1 with the approval chain.
    "backend/app/scripts/validate_ppi.py": "Phase 1",
    "backend/tests/test_assessment_setup_gate.py": "Phase 1",
}

#: The tables 0128 drops. Exactly these: see the rulings test below.
DROPPED = frozenset(
    {
        "technical_questions",
        "candidate_technical_questions",
        "llm_provider_keys",
        "otp_challenges",
    }
)


def _files(hits: list[str]) -> set[str]:
    return {hit.split(":", 1)[0].replace("\\", "/") for hit in hits}


def _migration(captured: list[str]) -> types.ModuleType:
    """Load 0128 with `op` replaced by a recorder, so the guard SQL it BUILDS
    is what the database tests below execute."""
    spec = importlib.util.spec_from_file_location("migration_0128", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.op = types.SimpleNamespace(execute=captured.append)
    return module


# ── The tree ─────────────────────────────────────────────────────────────────


def test_nothing_names_what_was_removed() -> None:
    hits = sweep(GONE, exempt=(THIS_FILE, SIBLING))
    spread = sorted(_files(hits) - set(PENDING))
    assert not spread, (
        "A removed legacy name appeared outside the pending list. Remove it "
        f"rather than listing the file: {spread}"
    )


def test_the_pending_list_only_shrinks() -> None:
    stale = sorted(set(PENDING) - _files(sweep(GONE, exempt=(THIS_FILE, SIBLING))))
    assert not stale, stale


def test_the_models_and_helpers_are_gone() -> None:
    import app.models as models
    from app.models import assessment, enums, tenant, user
    from app.services import capabilities, credits

    for module, name in (
        (assessment, "TechnicalQuestion"),
        (assessment, "CandidateTechnicalQuestion"),
        (tenant, "LLMProviderKey"),
        (user, "OTPChallenge"),
        (enums, "OTPChannel"),
        (enums, "LLMProvider"),
        (enums, "LLMRoleHint"),
        (credits, "has_credit_headroom"),
        (credits, "_sync_deficit"),
        (capabilities, "REVOKE_AGENT_LEARNINGS"),
    ):
        assert not hasattr(module, name), f"{module.__name__}.{name}"
    for name in (
        "TechnicalQuestion", "LLMProviderKey", "OTPChallenge", "AgentLearning",
    ):
        assert name not in models.__all__
    # The agent-memory mapping went with the unreachable package that used it
    # (WP-B5 deletes the package); the table stays as history.
    from app.models import agent

    assert not hasattr(agent, "AgentLearning")
    assert "revoke_agent_learnings" not in capabilities.ALL_CAPABILITIES
    for grants in capabilities.DEFAULT_PERMISSION_MATRIX.values():
        assert "revoke_agent_learnings" not in grants


def test_the_resume_provider_default_names_the_store_actually_used() -> None:
    from app.models.candidate import Profile
    from app.services import resume_storage

    column = Profile.__table__.c.resume_storage_provider
    assert column.default.arg == resume_storage.STORAGE_PROVIDER
    assert column.server_default.arg == resume_storage.STORAGE_PROVIDER


def test_the_migration_drops_exactly_the_empty_four() -> None:
    """The owner rulings, pinned: the SWOT transcript and `jobs.level` hold
    pilot rows and stay; `verification_requests` is Phase 6's drop, and one
    drop per table is the rule."""
    module = _migration([])
    assert frozenset(module.DROPPED_TABLES) == DROPPED
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    assert "DROP TABLE job_swot_intakes" not in source
    assert "DROP TABLE verification_requests" not in source
    assert "DROP COLUMN level" not in source


# ── The database ─────────────────────────────────────────────────────────────


async def _engine_or_skip():
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except (OSError, DBAPIError):
        await engine.dispose()
        pytest.skip("no database reachable, skipping the 0128 schema checks")
    return engine


class _DriverTransaction:
    """The asyncpg connection inside ITS OWN transaction, always rolled back.

    The driver connection is used so a DO block reaches the server verbatim,
    and the transaction must be the driver's too: SQLAlchemy's `begin()` is
    lazy and never reaches a connection used underneath it, and outside a
    transaction `SET LOCAL` and a transaction-local `set_config` are discarded
    at once, which would run the RLS test as the superuser it exists to avoid.
    """

    def __init__(self, conn) -> None:
        self._conn = conn

    async def __aenter__(self):
        raw = await self._conn.get_raw_connection()
        self.driver = raw.driver_connection
        self._tx = self.driver.transaction()
        await self._tx.start()
        return self.driver

    async def __aexit__(self, *exc) -> None:
        await self._tx.rollback()


async def test_the_migrated_schema_carries_none_of_it_and_keeps_the_history() -> None:
    engine = await _engine_or_skip()
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT set_config('app.bypass_rls','on',false)"))
            tables = set(
                (
                    await conn.execute(
                        text(
                            "SELECT table_name FROM information_schema.tables "
                            "WHERE table_schema = 'public'"
                        )
                    )
                ).scalars()
            )
            assert not (DROPPED & tables), sorted(DROPPED & tables)
            assert "job_swot_intakes" in tables

            columns = set(
                (
                    await conn.execute(
                        text(
                            "SELECT table_name || '.' || column_name "
                            "FROM information_schema.columns "
                            "WHERE table_schema = 'public' "
                            "AND table_name IN ('jobs', 'tenants')"
                        )
                    )
                ).scalars()
            )
            assert "jobs.questions_approved_at" not in columns
            assert "tenants.credit_deficit" not in columns
            assert "jobs.level" in columns

            default = (
                await conn.execute(
                    text(
                        "SELECT column_default FROM information_schema.columns "
                        "WHERE table_name = 'profiles' "
                        "AND column_name = 'resume_storage_provider'"
                    )
                )
            ).scalar_one()
            assert default.startswith("'s3'"), default

            grants = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM role_permissions "
                        "WHERE capability = 'revoke_agent_learnings'"
                    )
                )
            ).scalar_one()
            assert grants == 0
    finally:
        await engine.dispose()


async def test_the_emptiness_guard_refuses_a_table_holding_a_row() -> None:
    captured: list[str] = []
    module = _migration(captured)
    module._guard_empty("scrap_probe")
    guard = captured[-1]

    engine = await _engine_or_skip()
    try:
        async with engine.connect() as conn:
            async with _DriverTransaction(conn) as driver:
                await driver.execute("CREATE TEMP TABLE scrap_probe (x int)")
                await driver.execute(guard)  # empty: passes
                await driver.execute("INSERT INTO scrap_probe VALUES (1)")
                with pytest.raises(
                    asyncpg.exceptions.RaiseError, match=r"scrap_probe holds 1 row\(s\)"
                ):
                    await driver.execute(guard)
    finally:
        await engine.dispose()


async def test_the_null_guard_refuses_a_stamped_column() -> None:
    captured: list[str] = []
    module = _migration(captured)
    module._guard_all_null("scrap_stamps", "stamped_at")
    guard = captured[-1]

    engine = await _engine_or_skip()
    try:
        async with engine.connect() as conn:
            async with _DriverTransaction(conn) as driver:
                await driver.execute(
                    "CREATE TEMP TABLE scrap_stamps (stamped_at timestamptz)"
                )
                await driver.execute("INSERT INTO scrap_stamps VALUES (NULL)")
                await driver.execute(guard)  # every value NULL: passes
                await driver.execute("INSERT INTO scrap_stamps VALUES (now())")
                with pytest.raises(
                    asyncpg.exceptions.RaiseError, match=r"1 row\(s\) carry stamped_at"
                ):
                    await driver.execute(guard)
    finally:
        await engine.dispose()


async def test_the_guard_refuses_to_count_under_row_level_security() -> None:
    """Outside the bypass scope, as a role that is neither superuser nor
    BYPASSRLS, a count over a FORCE RLS table reads zero whatever it holds.
    The migration must refuse to run there rather than drop on that zero."""
    captured: list[str] = []
    module = _migration(captured)
    module._require_unfiltered_reads()
    guard = captured[-1]

    engine = await _engine_or_skip()
    try:
        async with engine.connect() as conn:
            async with _DriverTransaction(conn) as driver:
                await driver.execute('SET LOCAL ROLE "pickready_app"')
                await driver.execute("SELECT set_config('app.bypass_rls', 'on', true)")
                await driver.execute(guard)  # in the bypass scope: passes
                await driver.execute("SELECT set_config('app.bypass_rls', 'off', true)")
                with pytest.raises(
                    asyncpg.exceptions.RaiseError, match="0128 refuses to run"
                ):
                    await driver.execute(guard)
    finally:
        await engine.dispose()
