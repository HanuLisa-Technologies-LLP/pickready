"""The Job SWOT Analysis routes, over a real database and the real router.

WHY THIS IS A ROUTE TEST AND NOT A SERVICE TEST
------------------------------------------------
`test_swot_analysis.py` proves the RULES on a fake session: the version check,
the human-edit latch, the refusal ordering. What broke in production was none
of those. The routes wrote a correct audit row and then mutated it AFTER
`audit()` had flushed it, which emits `UPDATE audit_log` - a statement the
tenant session's role (`SET LOCAL ROLE pickready_app`, see `tenant_scope`) has
had REVOKED since migration 0001, binding since the 2026-09-11 credential
split. On the generate route the doomed UPDATE flushed at COMMIT, after the
200 response had already left, so the whole transaction rolled back: the
client held `version` one ahead of the database, and the user's FIRST save
answered 409 "Someone else saved this SWOT while you were editing" with
nobody else in the building.

Only a test that runs the real router over a real session, with the RLS role
drop applied, and then reads the table back from a SECOND connection can see
that failure. "The response said 200" is exactly the evidence that lied.
"""
from __future__ import annotations

import asyncio
import uuid
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
from app.services import swot_analysis

BASE = "/api/v2/assessments/jobs"

DRAFT = {
    "strengths": "A senior brief with a well understood skill profile.",
    "weaknesses": "The must-have list is narrow for the band offered.",
    "opportunities": "Adjacent platform engineers convert into this role.",
    "threats": "Two competitors are hiring the same profile this quarter.",
}

#: The exact refusal sentence the service raises. The screen renders the
#: server's sentence verbatim, so the sentence is part of the contract.
CONFLICT_SENTENCE = (
    "Someone else saved this SWOT while you were editing. Reload to "
    "see their version before saving yours."
)


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
            await conn.execute(sa.text("SELECT 1 FROM job_swot_analyses LIMIT 0"))
        return True
    except Exception:  # noqa: BLE001 - no database here
        return False
    finally:
        await engine.dispose()


class World:
    """One customer, one client super admin, one job."""

    def __init__(self) -> None:
        self.tenant = uuid.uuid4()
        self.user = uuid.uuid4()
        self.job = uuid.uuid4()


@pytest.fixture
def world() -> Iterator[World]:
    if not _run(_reachable()):
        pytest.skip("no database reachable - skipping SWOT analysis API test")

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
                            "name": f"Swot-API-{w.tenant.hex[:6]}",
                            "domain": f"{w.tenant.hex[:10]}.swot.test",
                        },
                    )
                    await session.execute(
                        sa.text(
                            "INSERT INTO users (id, tenant_id, email, full_name, "
                            " role, status) "
                            "VALUES (:id, :tid, :email, 'Priya Raman', 'client', "
                            " 'active')"
                        ),
                        {
                            "id": str(w.user),
                            "tid": str(w.tenant),
                            "email": f"{w.user.hex[:10]}@swot.test",
                        },
                    )
                    await session.execute(
                        sa.text(
                            "INSERT INTO jobs (id, tenant_id, title, jd_json, status) "
                            "VALUES (:id, :tid, 'Backend Engineer', "
                            " CAST('{}' AS jsonb), 'draft')"
                        ),
                        {"id": str(w.job), "tid": str(w.tenant)},
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


def _committed_row(world: World) -> dict | None:
    """The document as a SECOND connection sees it, after the request is over.

    This is the assertion that catches a write that answered 200 and then
    rolled back at commit, which no assertion on the response body can see.
    """

    async def _read():
        sessions = _sessions()
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    row = (
                        await session.execute(
                            sa.text(
                                "SELECT version, status, strengths "
                                "FROM job_swot_analyses WHERE job_id = :jid"
                            ),
                            {"jid": str(world.job)},
                        )
                    ).mappings().first()
                    return dict(row) if row is not None else None

    return _run(_read())


# ── The false 409 (2026-09-20): a first-time save must succeed ───────────────


def test_the_first_time_save_succeeds_and_is_really_committed(
    client: TestClient, world: World
) -> None:
    got = client.get(f"{BASE}/{world.job}/swot-analysis")
    assert got.status_code == 200, got.text
    assert got.json()["status"] == "not_generated"
    loaded_version = got.json()["version"]

    saved = client.put(
        f"{BASE}/{world.job}/swot-analysis",
        json={**DRAFT, "expected_version": loaded_version},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == loaded_version + 1
    assert saved.json()["human_edited"] is True

    # The response is not the evidence; the table is. Before the fix the
    # save's transaction could not commit (the post-flush audit mutation was
    # an UPDATE the app role cannot run), so the row silently kept its old
    # version and the next save 409'd with nobody else editing.
    committed = _committed_row(world)
    assert committed is not None
    assert committed["version"] == loaded_version + 1
    assert committed["strengths"] == DRAFT["strengths"]


def test_a_save_then_a_save_with_the_refreshed_version_succeeds(
    client: TestClient, world: World
) -> None:
    got = client.get(f"{BASE}/{world.job}/swot-analysis")
    first = client.put(
        f"{BASE}/{world.job}/swot-analysis",
        json={**DRAFT, "expected_version": got.json()["version"]},
    )
    assert first.status_code == 200, first.text

    second = client.put(
        f"{BASE}/{world.job}/swot-analysis",
        json={
            **DRAFT,
            "strengths": "Revised after a second read.",
            "expected_version": first.json()["version"],
        },
    )
    assert second.status_code == 200, second.text
    assert second.json()["strengths"] == "Revised after a second read."
    assert _committed_row(world)["version"] == second.json()["version"]


#: A JD long enough for `generation_sufficiency.swot_input_state`.
JD_DOCUMENT = (
    "## Description\n"
    + " ".join(
        "The platform team runs the ingestion services that feed every finance "
        "report, and this engineer owns their reliability end to end."
        .split()
        * 6
    )
)


def _give_the_job_a_jd(world: World) -> None:
    async def _write() -> None:
        async with _sessions()() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await session.execute(
                        sa.text("UPDATE jobs SET jd_markdown = :jd WHERE id = :id"),
                        {"jd": JD_DOCUMENT, "id": str(world.job)},
                    )

    _run(_write())


def test_a_generation_survives_its_own_response(
    client: TestClient, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The production mechanism, pinned, on the DISPATCHED flow.

    The generate route used to call the model in the request, answer 200 with
    `version` N+1, and then fail its commit on a post-flush audit UPDATE, so the
    client edited a draft the database never kept. Now the route answers 202
    with the document `generating` and hands the work off after its commit; the
    worker writes the draft and its audit row in one INSERT. Both halves are
    read back from a SECOND connection, and the save the user then makes
    against the worker's version works first time.
    """
    from app.workers import dispatch
    from app.workers.tasks import generate_job_swot

    _give_the_job_a_jd(world)

    async def _draft(session, job):
        return dict(DRAFT)

    monkeypatch.setattr(swot_analysis, "draft", _draft)

    requested = client.post(
        f"{BASE}/{world.job}/swot-analysis/generate",
        json={"confirm_overwrite": False},
    )
    assert requested.status_code == 202, requested.text
    assert requested.json()["status"] == "generating"
    assert _committed_row(world)["status"] == "generating"
    sent = [d for d in dispatch.recorded() if d.name == "pickready.generate_job_swot"]
    assert len(sent) == 1, "the generation is dispatched once, after the commit"

    generate_job_swot(*sent[0].args, **sent[0].kwargs)

    committed = _committed_row(world)
    assert committed is not None
    assert committed["status"] == "generated"
    assert committed["strengths"] == DRAFT["strengths"]

    async def _audit():
        async with _sessions()() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    return (
                        await session.execute(
                            sa.text(
                                "SELECT agent_name, actor_user_id FROM audit_log "
                                "WHERE action = 'job_swot_analysis_generated' "
                                "AND job_id = :jid"
                            ),
                            {"jid": str(world.job)},
                        )
                    ).mappings().all()

    rows = _run(_audit())
    assert len(rows) == 1, "the worker's audit row committed, once"
    assert rows[0]["agent_name"] == "bodha"
    assert str(rows[0]["actor_user_id"]) == str(world.user)

    got = client.get(f"{BASE}/{world.job}/swot-analysis")
    assert got.json()["version"] == committed["version"]
    saved = client.put(
        f"{BASE}/{world.job}/swot-analysis",
        json={**DRAFT, "expected_version": committed["version"]},
    )
    assert saved.status_code == 200, saved.text


def test_generation_is_refused_before_anything_when_the_jd_is_too_thin(
    client: TestClient, world: World
) -> None:
    """The sufficiency gate: fixed copy, row unchanged, nothing dispatched."""
    from app.services import generation_sufficiency
    from app.workers import dispatch

    refused = client.post(
        f"{BASE}/{world.job}/swot-analysis/generate",
        json={"confirm_overwrite": False},
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"] == generation_sufficiency.EMPTY_STATE_COPY[
        "swot.jd_too_thin"
    ]
    assert not [d for d in dispatch.recorded() if d.name == "pickready.generate_job_swot"]
    committed = _committed_row(world)
    assert committed is None or committed["status"] == "not_generated"


def test_a_genuinely_stale_version_is_still_refused_with_the_server_sentence(
    client: TestClient, world: World
) -> None:
    got = client.get(f"{BASE}/{world.job}/swot-analysis")
    stale_version = got.json()["version"]

    first = client.put(
        f"{BASE}/{world.job}/swot-analysis",
        json={**DRAFT, "expected_version": stale_version},
    )
    assert first.status_code == 200, first.text

    # A second editor who loaded before the first save must be refused, and
    # the refusal sentence must be the server's, verbatim.
    conflicted = client.put(
        f"{BASE}/{world.job}/swot-analysis",
        json={**DRAFT, "expected_version": stale_version},
    )
    assert conflicted.status_code == 409
    assert conflicted.json()["detail"] == CONFLICT_SENTENCE

    # The refused save changed nothing.
    assert _committed_row(world)["version"] == first.json()["version"]
