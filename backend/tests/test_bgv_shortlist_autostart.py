"""Shortlisting opens background verification, and never holds a stage change up.

THE WIRING THIS PINS
----------------------
`docs/spec/VIVEKIUM_SPRINT_FEATURES.md`, the eight wirings, number 2:
"Shortlisting fires two parallel triggers: assessment start AND the BGV email.
Neither waits for the other." The test drives the REAL chokepoint,
`hiring_pipeline.apply_transition`, rather than the service directly, because
the chokepoint is the claim: a trigger wired into one route is a trigger the
other five callers do not have.

FOUR PROPERTIES, AND EACH IS A DIFFERENT WAY THIS FEATURE GOES WRONG
----------------------------------------------------------------------
* a shortlist opens one verification per employer and binds one outbound
  record to each, which is what a later bounce resolves through;
* a fresher is untouched, so the offer gate's `not_required` path stays exactly
  as it was;
* a second shortlist opens nothing, because a re-shortlist, a retry and a
  redelivered task all arrive as separate calls;
* a BGV failure does not fail the move, and the stage change is COMMITTED and
  readable from a second connection afterwards. That last half is the one an
  assertion on the return value cannot make: the savepoint exists precisely
  because a failed statement poisons the surrounding transaction, and a
  transition that answered success and rolled back at COMMIT is the failure
  mode `audit_log` already taught this repository.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Iterator

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.db import superadmin_scope, tenant_scope
from app.services import bgv_autostart, hiring_pipeline
from app.workers import dispatch as dispatch_module


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
            await conn.execute(
                sa.text("SELECT bgv_verification_id FROM email_log LIMIT 0")
            )
        return True
    except Exception:  # noqa: BLE001 -- no database, or migration 0114 unapplied
        return False
    finally:
        await engine.dispose()


class World:
    def __init__(self) -> None:
        self.tenant = uuid.uuid4()
        self.recruiter = uuid.uuid4()
        self.job = uuid.uuid4()
        # The experienced candidate, history submitted, two employers.
        self.candidate = uuid.uuid4()
        self.link = uuid.uuid4()
        self.employments = [uuid.uuid4(), uuid.uuid4()]
        # The fresher, on the same job.
        self.fresher = uuid.uuid4()
        self.fresher_link = uuid.uuid4()
        # Experienced but still DRAFTING: verifying this one would ask an
        # employer to confirm details the candidate is still editing.
        self.drafting = uuid.uuid4()
        self.drafting_link = uuid.uuid4()
        self.drafting_employment = uuid.uuid4()


@pytest.fixture
def world() -> Iterator[World]:
    if not _run(_reachable()):
        pytest.skip("no database with migration 0114 -- skipping BGV autostart test")
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
                            "name": f"BGV-START-{w.tenant.hex[:6]}",
                            "domain": f"{w.tenant.hex[:10]}.bgvstart.test",
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
                            "id": str(w.recruiter),
                            "tid": str(w.tenant),
                            "email": f"{w.recruiter.hex[:10]}@bgvstart.test",
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
                    for candidate, background in (
                        (w.candidate, "experienced"),
                        (w.fresher, "fresher"),
                        (w.drafting, "experienced"),
                    ):
                        await session.execute(
                            sa.text(
                                "INSERT INTO candidates (id, full_name, email, "
                                " consent_databank, employment_background, "
                                " created_at) "
                                "VALUES (:id, 'Karthik Kumar', :email, false, "
                                " :bg, now())"
                            ),
                            {
                                "id": str(candidate),
                                "email": f"c-{candidate.hex[:10]}@bgvstart.test",
                                "bg": background,
                            },
                        )
                    for index, employment in enumerate(w.employments):
                        await session.execute(
                            sa.text(
                                "INSERT INTO candidate_employments (id, "
                                " candidate_id, employer_name, designation, "
                                " started_on, ended_on, hr_name, hr_email, "
                                " created_at) "
                                "VALUES (:id, :cid, :name, 'Engineer', "
                                " DATE '2019-06-03', DATE '2023-05-31', "
                                " 'R Menon', :hr, now())"
                            ),
                            {
                                "id": str(employment),
                                "cid": str(w.candidate),
                                "name": f"Northwind {index}",
                                "hr": f"hr{index}-{employment.hex[:6]}@nw.example",
                            },
                        )
                    await session.execute(
                        sa.text(
                            "INSERT INTO candidate_employments (id, candidate_id, "
                            " employer_name, designation, started_on, ended_on, "
                            " hr_name, hr_email, created_at) "
                            "VALUES (:id, :cid, 'Draft Co', 'Engineer', "
                            " DATE '2019-06-03', DATE '2023-05-31', 'R Menon', "
                            " :hr, now())"
                        ),
                        {
                            "id": str(w.drafting_employment),
                            "cid": str(w.drafting),
                            "hr": f"hr-{w.drafting_employment.hex[:8]}@draft.example",
                        },
                    )
                    # The submission stamp goes on LAST, matching the
                    # product's own order: the candidate types the list and
                    # then closes it. `w.drafting` is deliberately left
                    # unstamped, which is the state the autostart must refuse.
                    await session.execute(
                        sa.text(
                            "UPDATE candidates SET "
                            " employment_history_finalized_at = now() "
                            "WHERE id = ANY(:ids)"
                        ),
                        {"ids": [str(w.candidate), str(w.fresher)]},
                    )
                    for link, candidate in (
                        (w.link, w.candidate),
                        (w.fresher_link, w.fresher),
                        (w.drafting_link, w.drafting),
                    ):
                        await session.execute(
                            sa.text(
                                "INSERT INTO job_candidate_links "
                                "(id, tenant_id, job_id, candidate_id, source, "
                                " status) "
                                "VALUES (:id, :tid, :jid, :cid, 'fresh', 'applied')"
                            ),
                            {
                                "id": str(link),
                                "tid": str(w.tenant),
                                "jid": str(w.job),
                                "cid": str(candidate),
                            },
                        )

    async def _teardown() -> None:
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await session.execute(
                        sa.text("DELETE FROM candidates WHERE id = ANY(:ids)"),
                        {
                            "ids": [
                                str(w.candidate),
                                str(w.fresher),
                                str(w.drafting),
                            ]
                        },
                    )
                    await session.execute(
                        sa.text("DELETE FROM tenants WHERE id = :id"),
                        {"id": str(w.tenant)},
                    )

    _run(_seed())
    dispatch_module.clear_recorded()
    try:
        yield w
    finally:
        _run(_teardown())


async def _shortlist(world: World, link: uuid.UUID) -> None:
    sessions = _sessions()
    async with sessions() as session:
        async with session.begin():
            async with tenant_scope(session, world.tenant):
                await hiring_pipeline.apply_transition(
                    session,
                    link_id=link,
                    tenant_id=world.tenant,
                    target=hiring_pipeline.SHORTLISTED,
                    actor_user_id=world.recruiter,
                )


async def _verifications(world: World, candidate: uuid.UUID) -> list[dict]:
    """Read back from a SECOND CONNECTION, after the commit."""
    sessions = _sessions()
    async with sessions() as session:
        async with session.begin():
            async with superadmin_scope(session):
                rows = (
                    await session.execute(
                        sa.text(
                            "SELECT v.id, v.status, v.delivery_status, "
                            " v.first_sent_at, v.form_token, v.conversation_id, "
                            " e.employer_name "
                            "FROM bgv_verifications v "
                            "JOIN candidate_employments e "
                            "  ON e.id = v.candidate_employment_id "
                            "WHERE v.candidate_id = :cid ORDER BY e.employer_name"
                        ),
                        {"cid": str(candidate)},
                    )
                ).mappings().all()
    return [dict(row) for row in rows]


async def _bound_email_logs(world: World, candidate: uuid.UUID) -> list[dict]:
    sessions = _sessions()
    async with sessions() as session:
        async with session.begin():
            async with superadmin_scope(session):
                rows = (
                    await session.execute(
                        sa.text(
                            "SELECT id, email_type, recipient_email, "
                            " bgv_verification_id FROM email_log "
                            "WHERE candidate_id = :cid"
                        ),
                        {"cid": str(candidate)},
                    )
                ).mappings().all()
    return [dict(row) for row in rows]


def test_shortlisting_opens_and_sends_one_verification_per_employer(world: World):
    _run(_shortlist(world, world.link))

    rows = _run(_verifications(world, world.candidate))
    assert len(rows) == 2, rows
    for row in rows:
        assert row["status"] == "pending"
        assert row["delivery_status"] == "sent"
        assert row["first_sent_at"] is not None
        # The employer's single-use credential, minted at send.
        assert row["form_token"]
        # A thread, so the employer's reply has somewhere to land. Without it
        # the reply address carries no token and the answer arrives in the
        # sending mailbox instead.
        assert row["conversation_id"] is not None

    logs = _run(_bound_email_logs(world, world.candidate))
    assert len(logs) == 2, logs
    # THE BINDING, which is what a later bounce resolves through. Matching on
    # the recipient address instead would pick whichever request to that
    # mailbox went out last.
    assert {str(log["bgv_verification_id"]) for log in logs} == {
        str(row["id"]) for row in rows
    }
    assert {log["email_type"] for log in logs} == {"bgv_verification"}

    sends = [
        item
        for item in dispatch_module.recorded()
        if item.name == "pickready.send_email"
        and item.args[2] == "bgv_verification"
    ]
    assert len(sends) == 2, dispatch_module.recorded_names()


def test_a_fresher_is_never_asked_about_an_employer(world: World):
    """The path the offer gate depends on, asserted from the other end.

    `derive_status` answers `not_required` for a fresher and the gate never
    fires, so anything this trigger sent about them would be the product acting
    on a claim nobody made.
    """
    _run(_shortlist(world, world.fresher_link))
    assert _run(_verifications(world, world.fresher)) == []
    assert _run(_bound_email_logs(world, world.fresher)) == []


def test_a_draft_history_is_not_verified(world: World):
    """Verifying a draft emails an employer about details still being edited."""
    _run(_shortlist(world, world.drafting_link))
    assert _run(_verifications(world, world.drafting)) == []


def test_a_second_shortlist_opens_nothing(world: World):
    """A re-shortlist, a retry and a redelivery all arrive as separate calls."""
    _run(_shortlist(world, world.link))
    first = _run(_verifications(world, world.candidate))
    dispatch_module.clear_recorded()

    async def _again() -> int:
        sessions = _sessions()
        async with sessions() as session:
            async with session.begin():
                async with tenant_scope(session, world.tenant):
                    return await bgv_autostart.on_shortlist(
                        session,
                        tenant_id=world.tenant,
                        candidate_id=world.candidate,
                        link_id=world.link,
                        job_id=world.job,
                        actor_user_id=world.recruiter,
                    )

    assert _run(_again()) == 0
    assert _run(_verifications(world, world.candidate)) == first
    assert dispatch_module.recorded() == []


def test_a_bgv_failure_does_not_undo_the_stage_change(world: World, monkeypatch):
    """The savepoint is the claim, and the read-back is what tests it.

    A failed statement poisons the surrounding transaction, so catching an
    exception without a savepoint would not save the move at all: the
    shortlist write would already be doomed and would roll back at COMMIT,
    after the caller had been told it succeeded. The status is therefore read
    from a SECOND CONNECTION after the transaction closed.
    """

    async def _explode(*args, **kwargs):
        raise RuntimeError("the employer mail merge fell over")

    monkeypatch.setattr(bgv_autostart, "_open_and_send", _explode)
    _run(_shortlist(world, world.link))

    async def _status() -> str:
        sessions = _sessions()
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    return (
                        await session.execute(
                            sa.text(
                                "SELECT status FROM job_candidate_links WHERE id = :id"
                            ),
                            {"id": str(world.link)},
                        )
                    ).scalar()

    assert _run(_status()) == hiring_pipeline.SHORTLISTED
    # Nothing half-written: the savepoint took the verification row with it.
    assert _run(_verifications(world, world.candidate)) == []
