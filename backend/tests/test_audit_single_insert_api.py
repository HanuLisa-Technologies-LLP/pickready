"""Audit rows are written in ONE INSERT, proven from a second connection.

THE CLASS OF BUG THIS PINS
--------------------------
`audit()` flushes its INSERT and returns the row. Anything assigned to that
row afterwards emits `UPDATE audit_log`, a statement the application role
(`SET LOCAL ROLE pickready_app`, see `tenant_scope`) has had REVOKED since
migration 0001, binding since the 2026-09-11 credential split. Depending on
when the doomed UPDATE flushes, the request either 500s in-handler or, worse,
answers 200 and then rolls the WHOLE transaction back at commit, after the
response has left. That second shape caused the SWOT false-409 fixed on
2026-09-20 (`test_swot_analysis_api.py` is the model for these tests).

Three more sites carried the same pattern and were converted on the same day:
the framework finalize route, the send-to-hiring-manager route, and the
pipeline-halt audit record (whose own try/except turned the failure into a
log line, so no halt was ever actually recorded). Each test here runs the
real code over a real session with the real role drop, then reads the table
back from a SECOND connection, because "the response said 200" is exactly
the evidence that lied.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Iterator

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.deps import CurrentUser, get_current_user, get_tenant_db
from app.core.config import get_settings
from app.core.db import superadmin_scope, tenant_scope
from app.core.security import AUDIENCE_ORG
from app.main import app
from app.models.enums import Role
from app.services import ppi
from app.services.hiring import pipeline_halt, scorecard

JD = "## The role\nOwns the payments platform and its two services end to end."


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _sessions():
    return async_sessionmaker(
        create_async_engine(get_settings().database_url, poolclass=NullPool),
        expire_on_commit=False,
    )


async def _reachable() -> bool:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.text("SELECT 1 FROM audit_log LIMIT 0"))
        return True
    except Exception:  # noqa: BLE001 - no database here
        return False
    finally:
        await engine.dispose()


class World:
    """One customer, one client super admin, one job (state set per test)."""

    def __init__(self) -> None:
        self.tenant = uuid.uuid4()
        self.user = uuid.uuid4()
        self.job = uuid.uuid4()


@pytest.fixture
def world() -> Iterator[World]:
    if not _run(_reachable()):
        pytest.skip("no database reachable - skipping audit single-insert test")

    w = World()
    sessions = _sessions()

    async def _seed() -> None:
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await session.execute(
                        sa.text(
                            "INSERT INTO tenants (id, name, domain, spf_dkim_status) "
                            "VALUES (:id, :name, :domain, 'pending')"
                        ),
                        {
                            "id": str(w.tenant),
                            "name": f"Audit-API-{w.tenant.hex[:6]}",
                            "domain": f"{w.tenant.hex[:10]}.audit.test",
                        },
                    )
                    await session.execute(
                        sa.text(
                            "INSERT INTO users (id, tenant_id, email, full_name, "
                            " role, status) "
                            "VALUES (:id, :tid, :email, 'Meera Pillai', 'client', "
                            " 'active')"
                        ),
                        {
                            "id": str(w.user),
                            "tid": str(w.tenant),
                            "email": f"{w.user.hex[:10]}@audit.test",
                        },
                    )

    async def _teardown() -> None:
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await session.execute(
                        sa.text("DELETE FROM tenants WHERE id = :id"),
                        {"id": str(w.tenant)},
                    )

    _run(_seed())
    try:
        yield w
    finally:
        _run(_teardown())


def _seed_job(world: World, *, lifecycle_state: str, jd_markdown: str | None) -> None:
    async def _insert() -> None:
        sessions = _sessions()
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await session.execute(
                        sa.text(
                            "INSERT INTO jobs (id, tenant_id, title, jd_json, "
                            " jd_markdown, status, lifecycle_state) "
                            "VALUES (:id, :tid, 'Platform Engineer', "
                            " CAST('{}' AS jsonb), :jd, 'draft', :state)"
                        ),
                        {
                            "id": str(world.job),
                            "tid": str(world.tenant),
                            "jd": jd_markdown,
                            "state": lifecycle_state,
                        },
                    )

    _run(_insert())


@pytest.fixture
def client(world: World) -> Iterator[TestClient]:
    sessions = _sessions()

    async def _current_user() -> CurrentUser:
        return CurrentUser(
            user_id=world.user,
            tenant_id=world.tenant,
            role=Role.client,
            audience=AUDIENCE_ORG,
        )

    async def _tenant_db():
        # Entered exactly as the production dependency enters it: the session
        # drops to the RLS app role, so the audit table's revoked UPDATE grant
        # is part of what this test exercises.
        async with sessions() as session:
            async with session.begin():
                async with tenant_scope(session, world.tenant):
                    yield session

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_current_user] = _current_user
    app.dependency_overrides[get_tenant_db] = _tenant_db
    try:
        with TestClient(app) as http:
            yield http
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def _committed(query: str, params: dict) -> dict | None:
    """One row as a SECOND connection sees it, after the request is over."""

    async def _read():
        sessions = _sessions()
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    row = (
                        (await session.execute(sa.text(query), params))
                        .mappings()
                        .first()
                    )
                    return dict(row) if row is not None else None

    return _run(_read())


def _audit_row(action: str, job_id: uuid.UUID) -> dict | None:
    return _committed(
        "SELECT actor_role, agent_name, correlation_id, job_id, "
        " previous_state, new_state "
        "FROM audit_log WHERE action = :action AND job_id = :jid",
        {"action": action, "jid": str(job_id)},
    )


# ── finalize_framework: role_definition_finalized in one INSERT ─────────────


def test_finalizing_the_framework_survives_its_own_response(
    client: TestClient, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The finalize route's audit row and lifecycle write really commit.

    The scorecard freeze itself is not under test (test_scorecard covers it),
    so it is stubbed to its contract; what runs for real is the route, the
    tenant session with the role drop, and the audit INSERT.
    """
    _seed_job(world, lifecycle_state="IN_REVIEW", jd_markdown=JD)

    async def _freeze(session, job, *, actor_user_id, correlation_id):
        return SimpleNamespace(
            version=3,
            situation_key="turnaround",
            approved_at=datetime.now(timezone.utc),
        )

    async def _load_framework(session, job_id):
        return []

    monkeypatch.setattr(scorecard, "freeze", _freeze)
    monkeypatch.setattr(ppi, "load_framework", _load_framework)

    response = client.post(
        f"/api/v2/assessments/jobs/{world.job}/framework/finalize"
    )
    assert response.status_code == 200, response.text

    job_row = _committed(
        "SELECT lifecycle_state, finalized_by, criteria_version "
        "FROM jobs WHERE id = :id",
        {"id": str(world.job)},
    )
    assert job_row is not None
    assert job_row["lifecycle_state"] == "FINALIZED"
    assert str(job_row["finalized_by"]) == str(world.user)
    assert job_row["criteria_version"] == 3

    audit_row = _audit_row("role_definition_finalized", world.job)
    assert audit_row is not None, "the audit row never committed"
    assert audit_row["actor_role"] == "client"
    assert audit_row["new_state"] == {"lifecycle_state": "FINALIZED"}


# ── send-to-hiring-manager: jd_sent_to_hiring_manager in one INSERT ─────────


def test_sending_to_the_hiring_manager_survives_its_own_response(
    client: TestClient, world: World
) -> None:
    _seed_job(world, lifecycle_state="DRAFT", jd_markdown=JD)

    response = client.post(f"/api/v1/jobs/{world.job}/send-to-hiring-manager")
    assert response.status_code == 200, response.text

    job_row = _committed(
        "SELECT lifecycle_state FROM jobs WHERE id = :id",
        {"id": str(world.job)},
    )
    assert job_row is not None
    assert job_row["lifecycle_state"] == "SENT_TO_HIRING_MANAGER"

    audit_row = _audit_row("jd_sent_to_hiring_manager", world.job)
    assert audit_row is not None, "the audit row never committed"
    assert audit_row["actor_role"] == "client"
    assert audit_row["previous_state"] == {"lifecycle_state": "DRAFT"}
    assert audit_row["new_state"] == {"lifecycle_state": "SENT_TO_HIRING_MANAGER"}


# ── pipeline_halt: the halt record actually exists afterwards ───────────────


def test_a_pipeline_halt_is_really_recorded(world: World) -> None:
    """`_record` writes its row even for an agent hit with no human principal.

    Before the 2026-09-20 fix the post-flush mutation aborted `_record`'s own
    transaction and its except clause reduced that to a log line, so the one
    row distinguishing "the platform refused, deliberately" from "the platform
    broke" never existed. The read below is from a second connection.
    """
    _seed_job(world, lifecycle_state="DRAFT", jd_markdown=JD)
    halt = pipeline_halt.PipelineHalted(
        pipeline_halt.STAGE_SUTRA_MATRIX, pipeline_halt.STAGE_SUTRA_MATRIX
    )
    correlation = f"halt-{uuid.uuid4().hex[:12]}"

    async def _exercise() -> None:
        await pipeline_halt._record(  # noqa: SLF001 - the code under test
            halt,
            tenant_id=world.tenant,
            actor_user_id=None,
            job_id=world.job,
            correlation_id=correlation,
            agent="sutra",
        )
        # Leave no pooled connection bound to this throwaway loop.
        from app.core.db import get_engine  # noqa: PLC0415

        await get_engine().dispose()

    _run(_exercise())

    row = _committed(
        "SELECT actor_user_id, agent_name, correlation_id, job_id, "
        " metadata_json "
        "FROM audit_log WHERE action = 'pipeline_halted' "
        "AND correlation_id = :corr",
        {"corr": correlation},
    )
    assert row is not None, "the halt was never recorded"
    assert row["actor_user_id"] is None
    # `ck_audit_log_agent_has_principal` forbids the agent COLUMN without a
    # human principal, so the agent travels in the metadata facts instead and
    # the record still says which agent was stopped.
    assert row["agent_name"] is None
    assert row["metadata_json"]["agent"] == "sutra"
    assert str(row["job_id"]) == str(world.job)
    assert row["metadata_json"]["stage"] == pipeline_halt.STAGE_SUTRA_MATRIX


def test_a_halt_with_a_principal_keeps_the_agent_column(world: World) -> None:
    """Beside a human principal the agent stays in the RBAC 34 column."""
    _seed_job(world, lifecycle_state="DRAFT", jd_markdown=JD)
    halt = pipeline_halt.PipelineHalted(
        pipeline_halt.STAGE_BODHA_SWOT, pipeline_halt.STAGE_BODHA_SWOT
    )
    correlation = f"halt-{uuid.uuid4().hex[:12]}"

    async def _exercise() -> None:
        await pipeline_halt._record(  # noqa: SLF001 - the code under test
            halt,
            tenant_id=world.tenant,
            actor_user_id=world.user,
            job_id=world.job,
            correlation_id=correlation,
            agent="bodha",
        )
        from app.core.db import get_engine  # noqa: PLC0415

        await get_engine().dispose()

    _run(_exercise())

    row = _committed(
        "SELECT actor_user_id, agent_name, metadata_json "
        "FROM audit_log WHERE action = 'pipeline_halted' "
        "AND correlation_id = :corr",
        {"corr": correlation},
    )
    assert row is not None, "the halt was never recorded"
    assert str(row["actor_user_id"]) == str(world.user)
    assert row["agent_name"] == "bodha"
    assert row["metadata_json"]["agent"] == "bodha"
