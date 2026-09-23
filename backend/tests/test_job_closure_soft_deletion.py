"""Thirty days between closing a job and losing its assessment data.

Change request 22, owner ruling 2026-09-22, which REVERSES the 2026-09-18
vivekium C5 ruling that closure deleted inline. Three things have to be true
for that reversal to be worth anything, and each of them is a section below:

  * the data is UNREACHABLE from the instant of closure, or "withheld" is a
    word rather than a behaviour;
  * it is still THERE, so the dispute path has something to hand back;
  * it is GONE at the end of the window, and the deletion order is
    objects-then-rows, or the media survives with nothing left to name it.

The last one is the reason this file exists rather than an extra case in
`test_job_closure_erasure.py`. `video_recordings.conversation_id` is ON DELETE
CASCADE, so deleting `assessment_conversations` takes away the only rows that
carry the S3 keys. A purge that deleted rows first would report success and
leave a candidate's assessment video in the bucket for ever, and nothing in
the database would ever mention it again. That is asserted here by watching
what the object deleter can still see at the moment it is called, not by
watching that it was called.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models.enums import Role
from app.services import job_assessment_retention as retention

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


def _job(**overrides):
    """A closable job as the pure half of the module sees it.

    A SimpleNamespace rather than the ORM class, because `describe` takes the
    `_ClosableJob` Protocol precisely so this half is testable without a
    database, a migration or an engine.
    """
    base = {
        "id": uuid.uuid4(),
        "closed_at": None,
        "assessment_purge_due_at": None,
        "assessment_purged_at": None,
        "assessment_dispute_opened_at": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


# ── The three states, derived ────────────────────────────────────────────────


def test_an_open_job_is_live_and_carries_no_countdown() -> None:
    """`days_remaining` is None rather than zero for a job with no deadline.

    Zero would render as "it goes today" on the one screen that reads this.
    """
    state = retention.describe(_job(), now=NOW)
    assert state.state == retention.STATE_LIVE
    assert state.withheld is False
    assert state.days_remaining is None
    assert state.message() is None


def test_a_job_closed_a_minute_ago_is_withheld_and_still_retrievable() -> None:
    """THE WHOLE POINT OF THE REVERSAL, in one assertion pair.

    `withheld` and `retrievable` are both true at once, and they have to be:
    the employer loses access at the instant of closure AND the bytes survive,
    or the dispute path has nothing to be a path to.
    """
    closed = NOW - timedelta(minutes=1)
    state = retention.describe(
        _job(closed_at=closed, assessment_purge_due_at=retention.purge_due_at(closed)),
        now=NOW,
    )
    assert state.state == retention.STATE_PENDING_DELETION
    assert state.withheld is True
    assert state.retrievable is True
    assert state.message() == retention.WITHHELD_MESSAGE


def test_the_window_is_thirty_days_and_the_countdown_rounds_up() -> None:
    """A job with nineteen hours left reads as 1 day, never as 0.

    The floor is the load-bearing half. Somebody deciding whether to raise a
    dispute today must not be told the window has already shut while the gate
    is still letting them through.
    """
    closed = NOW
    due = retention.purge_due_at(closed)
    assert due - closed == timedelta(days=30)

    def _remaining(elapsed: timedelta) -> int | None:
        return retention.describe(
            _job(closed_at=closed, assessment_purge_due_at=due),
            now=closed + elapsed,
        ).days_remaining

    assert _remaining(timedelta(0)) == 30
    assert _remaining(timedelta(hours=1)) == 30
    assert _remaining(timedelta(days=29)) == 1
    assert _remaining(timedelta(days=29, hours=5)) == 1
    assert _remaining(timedelta(days=29, hours=23, minutes=59)) == 1


def test_an_expired_window_reads_as_purged_before_the_sweep_has_run() -> None:
    """The access answer does not wait for a worker.

    Answering `pending_deletion` until a sweep happens to run would let a
    scheduler outage silently extend a window the candidate was promised the
    end of, and would let the dispute path hand back data past that date.
    """
    closed = NOW - timedelta(days=31)
    state = retention.describe(
        _job(closed_at=closed, assessment_purge_due_at=retention.purge_due_at(closed)),
        now=NOW,
    )
    assert state.state == retention.STATE_PURGED
    assert state.retrievable is False
    assert state.message() == retention.PURGED_MESSAGE


def test_a_job_closed_before_this_release_reads_as_purged() -> None:
    """It has no due date because its data was destroyed by the old ruling.

    `purged` is the only truthful answer: there is nothing retained and
    nothing to retrieve. Migration 0112 stamps those rows, and this is the
    branch that keeps a row the migration could not reach honest anyway.
    """
    state = retention.describe(_job(closed_at=NOW - timedelta(days=2)), now=NOW)
    assert state.state == retention.STATE_PURGED
    assert state.message() == retention.PURGED_MESSAGE


# ── The access gate ──────────────────────────────────────────────────────────


def _principal():
    return SimpleNamespace(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), role=Role.client
    )


def _grant(monkeypatch, allowed: bool) -> list[str]:
    """Answer the grant engine with a fixed verdict, recording what was asked.

    The capability NAME is captured and asserted on, because the failure this
    guards against is the gate asking about `view_review_screen` (which every
    customer role holds) and therefore withholding the data from nobody.
    """
    from app.services import rbac

    asked: list[str] = []

    async def _has_capability(session, tenant_id, role, capability, user_id):
        asked.append(capability)
        return allowed

    monkeypatch.setattr(rbac, "has_capability", _has_capability)
    return asked


def _closed_job(**overrides):
    closed = NOW - timedelta(days=2)
    base = {
        "closed_at": closed,
        "assessment_purge_due_at": retention.purge_due_at(closed),
    }
    base.update(overrides)
    return _job(**base)


@pytest.mark.asyncio
async def test_an_open_job_is_readable_and_asks_the_grant_engine_nothing(
    monkeypatch,
) -> None:
    """The gate costs a live job nothing, which is most reads in the product."""
    asked = _grant(monkeypatch, True)
    state = await retention.require_readable(
        None, _principal(), _job(), now=NOW
    )
    assert state.state == retention.STATE_LIVE
    assert asked == []


@pytest.mark.asyncio
async def test_a_closed_job_is_withheld_from_the_recruiter(monkeypatch) -> None:
    """410 GONE, with the server's own sentence.

    Not 403, which would say "ask somebody for permission" when nobody on the
    employer's side can grant it. Not 404, which would say the application
    does not exist and make a recruiter doubt their own pipeline.
    """
    _grant(monkeypatch, False)
    with pytest.raises(HTTPException) as caught:
        await retention.require_readable(
            None, _principal(), _closed_job(), now=NOW
        )
    assert caught.value.status_code == 410
    assert caught.value.detail == retention.WITHHELD_MESSAGE


@pytest.mark.asyncio
async def test_the_capability_alone_does_not_open_a_closed_job(
    monkeypatch,
) -> None:
    """BOTH HALVES ARE REQUIRED, and this is the half that is easy to drop.

    Holding `retrieve_disputed_assessment` with no dispute open would be a
    standing ability to read every closed job in the tenant, which is a
    different feature from retrieving the records of the one job somebody is
    actually disputing. The grant engine is not even consulted, because there
    is nothing for a yes to authorise.
    """
    asked = _grant(monkeypatch, True)
    with pytest.raises(HTTPException) as caught:
        await retention.require_readable(
            None, _principal(), _closed_job(), now=NOW
        )
    assert caught.value.status_code == 410
    assert asked == []


@pytest.mark.asyncio
async def test_an_open_dispute_alone_does_not_open_it_either(monkeypatch) -> None:
    """The complementary half: the dispute says WHICH job, not WHO.

    Without this, opening a dispute would hand the records back to every
    recruiter in the tenant, which is the withholding undone by the act that
    was supposed to narrow it.
    """
    asked = _grant(monkeypatch, False)
    with pytest.raises(HTTPException) as caught:
        await retention.require_readable(
            None,
            _principal(),
            _closed_job(assessment_dispute_opened_at=NOW),
            now=NOW,
        )
    assert caught.value.status_code == 410
    assert asked == [retention.RETRIEVE_DISPUTED_ASSESSMENT]


@pytest.mark.asyncio
async def test_the_dispute_path_reads_the_withheld_records(monkeypatch) -> None:
    """Capability plus open dispute plus inside the window: allowed."""
    asked = _grant(monkeypatch, True)
    state = await retention.require_readable(
        None,
        _principal(),
        _closed_job(assessment_dispute_opened_at=NOW),
        now=NOW,
    )
    assert state.state == retention.STATE_PENDING_DELETION
    assert asked == [retention.RETRIEVE_DISPUTED_ASSESSMENT]


@pytest.mark.asyncio
async def test_the_dispute_path_closes_when_the_window_does(monkeypatch) -> None:
    """A dispute left open past day thirty retrieves nothing.

    The dispute deliberately does not extend the window: the thirty days were
    promised to the candidate as well as to the employer, and an unresolved
    argument must not be able to keep somebody's assessment alive for ever.
    """
    closed = NOW - timedelta(days=31)
    _grant(monkeypatch, True)
    with pytest.raises(HTTPException) as caught:
        await retention.require_readable(
            None,
            _principal(),
            _job(
                closed_at=closed,
                assessment_purge_due_at=retention.purge_due_at(closed),
                assessment_dispute_opened_at=NOW - timedelta(days=1),
            ),
            now=NOW,
        )
    assert caught.value.detail == retention.PURGED_MESSAGE


# ── The purge, against a real schema ─────────────────────────────────────────


def _run(coro_factory):
    async def _wrapped():
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.core.config import get_settings

        engine = create_async_engine(get_settings().database_url)
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            return await coro_factory(factory)
        finally:
            await engine.dispose()

    return asyncio.run(_wrapped())


def _skip_without_database() -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.config import get_settings

    async def _probe():
        engine = create_async_engine(get_settings().database_url)
        try:
            async with engine.connect():
                return True
        except Exception:  # noqa: BLE001 -- no database is a skip, not a failure
            return False
        finally:
            await engine.dispose()

    if not asyncio.run(_probe()):
        pytest.skip("no database reachable, skipping job purge test")


class _World:
    def __init__(self) -> None:
        self.tenant = uuid.uuid4()
        self.due_job = uuid.uuid4()
        self.waiting_job = uuid.uuid4()
        self.candidate = uuid.uuid4()
        self.profile = uuid.uuid4()
        self.due_link = uuid.uuid4()
        self.waiting_link = uuid.uuid4()
        self.due_conversation = uuid.uuid4()
        self.waiting_conversation = uuid.uuid4()


async def _seed(session, w: _World, *, now: datetime) -> None:
    """Two closed jobs: one whose window expired, one with days left.

    The SECOND is the control and it is the assertion that matters most. A
    sweep that purged every closed job would satisfy every "the due job is
    gone" check on its own, and would destroy the retention window this whole
    release exists to create.
    """
    from sqlalchemy import text

    await session.execute(
        text(
            "INSERT INTO tenants (id, name, domain, spf_dkim_status) "
            "VALUES (:t, :n, :d, 'pending')"
        ),
        {"t": str(w.tenant), "n": f"purge-{w.tenant}",
         "d": f"{w.tenant}.purge.test"},
    )
    for job, due in (
        (w.due_job, now - timedelta(minutes=5)),
        (w.waiting_job, now + timedelta(days=10)),
    ):
        await session.execute(
            text(
                "INSERT INTO jobs (id, tenant_id, title, jd_json, status, "
                "closed_at, assessment_purge_due_at) "
                "VALUES (:j, :t, 'Backend Engineer', '{}'::jsonb, 'draft', "
                ":closed, :due)"
            ),
            {"j": str(job), "t": str(w.tenant),
             "closed": due - timedelta(days=30), "due": due},
        )
    await session.execute(
        text(
            "INSERT INTO candidates (id, tenant_id, full_name, email, "
            "consent_databank) VALUES (:c, :t, 'Purge Subject', :e, false)"
        ),
        {"c": str(w.candidate), "t": str(w.tenant),
         "e": f"{w.candidate}@purge.test"},
    )
    await session.execute(
        text(
            "INSERT INTO profiles (id, candidate_id, source_tenant_id, "
            "resume_text) VALUES (:p, :c, :t, 'Kafka and Postgres')"
        ),
        {"p": str(w.profile), "c": str(w.candidate), "t": str(w.tenant)},
    )
    for link, job in ((w.due_link, w.due_job), (w.waiting_link, w.waiting_job)):
        await session.execute(
            text(
                "INSERT INTO job_candidate_links "
                "(id, tenant_id, job_id, candidate_id, profile_id, source) "
                "VALUES (:l, :t, :j, :c, :p, 'manual')"
            ),
            {"l": str(link), "t": str(w.tenant), "j": str(job),
             "c": str(w.candidate), "p": str(w.profile)},
        )
    for conversation, link, job in (
        (w.due_conversation, w.due_link, w.due_job),
        (w.waiting_conversation, w.waiting_link, w.waiting_job),
    ):
        await session.execute(
            text(
                "INSERT INTO assessment_conversations "
                "(id, tenant_id, job_id, job_candidate_link_id, grade, status, "
                "next_question_index, reminders_sent, follow_ups_used, "
                "reasks_used, mode) VALUES (:i, :t, :j, :l, 'non_managerial', "
                "'active', 0, 0, 0, 0, 'conversational')"
            ),
            {"i": str(conversation), "t": str(w.tenant), "j": str(job),
             "l": str(link)},
        )
        await session.execute(
            text(
                "INSERT INTO functional_skills_reports "
                "(id, tenant_id, job_id, job_candidate_link_id, grade, "
                "overall_summary, synthesized_at) "
                "VALUES (:i, :t, :j, :l, 'non_managerial', "
                "'A summary of the closed assessment.', now())"
            ),
            {"i": str(uuid.uuid4()), "t": str(w.tenant), "j": str(job),
             "l": str(link)},
        )


async def _cleanup(session, w: _World) -> None:
    from sqlalchemy import text

    await session.execute(
        text("DELETE FROM candidates WHERE id = :c"), {"c": str(w.candidate)}
    )
    for job in (w.due_job, w.waiting_job):
        await session.execute(
            text("DELETE FROM jobs WHERE id = :j"), {"j": str(job)}
        )
    await session.execute(
        text("DELETE FROM tenants WHERE id = :t"), {"t": str(w.tenant)}
    )


async def _report_counts(session, w: _World) -> tuple[int, int]:
    from sqlalchemy import text

    async def _count(job) -> int:
        return int(
            (
                await session.execute(
                    text(
                        "SELECT COUNT(*) FROM functional_skills_reports "
                        "WHERE job_id = :j"
                    ),
                    {"j": str(job)},
                )
            ).scalar_one()
        )

    return await _count(w.due_job), await _count(w.waiting_job)


def _stub_media(monkeypatch, *, finished: bool, watcher: list | None = None):
    """Replace the object store with a recorder, keeping the database real.

    The S3 half is stubbed because the question here is ORDER and the answer
    lives in Postgres: when the deleter is asked to remove the objects, are
    the rows that name them still there? A live bucket would answer the same
    thing more slowly and would make the test skip wherever MinIO is not up.
    """
    from app.services import assessment_media_retention as media_retention

    entries = [{"key": "assessments/one.mp4", "kind": "assessment_video_compressed"}]

    async def _keys(session, job_id):
        return list(entries)

    def _delete(found):
        if watcher is not None:
            watcher.append(list(found))
        if finished:
            return media_retention.MediaDeletion(deleted=len(found), remaining=())
        return media_retention.MediaDeletion(
            deleted=0, remaining=tuple(found), failure="ObjectStorageError"
        )

    monkeypatch.setattr(media_retention, "object_keys_for_job", _keys)
    monkeypatch.setattr(media_retention, "delete_objects", _delete)


def test_the_sweep_purges_the_due_job_and_leaves_the_waiting_one(
    monkeypatch,
) -> None:
    """The window is real, and it is real in the direction that costs data.

    `jobs_due_for_purge` asks the TABLE, so a job with ten days left is not
    enumerated at all, and the purge stamps the due job so a second pass finds
    nothing left to do.
    """
    _skip_without_database()
    _stub_media(monkeypatch, finished=True)
    w = _World()

    async def _flow(factory):
        from sqlalchemy import text

        from app.core.db import superadmin_scope

        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await _seed(session, w, now=datetime.now(timezone.utc))
        try:
            async with factory() as session:
                async with session.begin():
                    async with superadmin_scope(session):
                        due = await retention.jobs_due_for_purge(session)
                        due_ids = {job.id for job in due}
                        outcomes = [
                            await retention.purge_job(session, job)
                            for job in due
                            if job.id == w.due_job
                        ]
            # Read back from a SECOND connection: an assertion on the objects
            # the purge returned cannot see a write that rolled back.
            async with factory() as session:
                async with superadmin_scope(session):
                    counts = await _report_counts(session, w)
                    stamps = (
                        await session.execute(
                            text(
                                "SELECT assessment_purged_at FROM jobs "
                                "WHERE id = :j"
                            ),
                            {"j": str(w.due_job)},
                        )
                    ).scalar_one()
                    waiting_stamp = (
                        await session.execute(
                            text(
                                "SELECT assessment_purged_at FROM jobs "
                                "WHERE id = :j"
                            ),
                            {"j": str(w.waiting_job)},
                        )
                    ).scalar_one()
                    remaining = await retention.jobs_due_for_purge(session)
            return due_ids, outcomes, counts, stamps, waiting_stamp, remaining
        finally:
            async with factory() as session:
                async with session.begin():
                    async with superadmin_scope(session):
                        await _cleanup(session, w)

    due_ids, outcomes, counts, stamp, waiting_stamp, remaining = _run(_flow)

    assert w.due_job in due_ids
    assert w.waiting_job not in due_ids
    assert outcomes[0].completed is True
    assert outcomes[0].media_deleted == 1
    # The due job's report is gone; the waiting job's is untouched.
    assert counts == (0, 1)
    assert stamp is not None
    assert waiting_stamp is None
    # Converged: the finished job is never enumerated again.
    assert w.due_job not in {job.id for job in remaining}


def test_a_failed_object_deletion_keeps_the_rows_that_name_the_objects(
    monkeypatch,
) -> None:
    """OBJECTS FIRST, ROWS SECOND, and this is the assertion behind that rule.

    `video_recordings.conversation_id` is ON DELETE CASCADE, so deleting
    `assessment_conversations` removes the only rows carrying the S3 keys. If
    the purge deleted rows first, an object store that refused would leave a
    candidate's recording in the bucket with nothing anywhere able to name it
    again. So a pass that cannot confirm every object gone deletes NOTHING,
    records the failure, and comes back next hour.

    The watcher is what makes this an assertion about order rather than about
    a call: it captures the report count AT THE MOMENT the deleter runs.
    """
    _skip_without_database()
    w = _World()
    seen: list = []
    _stub_media(monkeypatch, finished=False, watcher=seen)

    async def _flow(factory):
        from sqlalchemy import text

        from app.core.db import superadmin_scope

        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await _seed(session, w, now=datetime.now(timezone.utc))
        try:
            async with factory() as session:
                async with session.begin():
                    async with superadmin_scope(session):
                        due = await retention.jobs_due_for_purge(session)
                        subject = next(
                            row for row in due if row.id == w.due_job
                        )
                        outcome = await retention.purge_job(session, subject)
            async with factory() as session:
                async with superadmin_scope(session):
                    counts = await _report_counts(session, w)
                    attempts, failure, stamp = (
                        await session.execute(
                            text(
                                "SELECT assessment_purge_attempts, "
                                "assessment_purge_last_failure, "
                                "assessment_purged_at FROM jobs WHERE id = :j"
                            ),
                            {"j": str(w.due_job)},
                        )
                    ).one()
                    still_due = await retention.jobs_due_for_purge(session)
            return outcome, counts, attempts, failure, stamp, still_due
        finally:
            async with factory() as session:
                async with session.begin():
                    async with superadmin_scope(session):
                        await _cleanup(session, w)

    outcome, counts, attempts, failure, stamp, still_due = _run(_flow)

    # The object deleter ran while the data was still there, which is the
    # ordering the whole design turns on.
    assert seen and seen[0]
    assert outcome.completed is False
    assert outcome.media_remaining == 1
    # NOTHING was deleted. The report survives a failed pass.
    assert counts == (1, 1)
    assert stamp is None
    assert attempts == 1
    # A class name, never a message: the record outlives the rows.
    assert failure == "ObjectStorageError"
    # And it comes back on the next sweep rather than being written off.
    assert w.due_job in {job.id for job in still_due}


def test_purging_an_already_purged_job_is_a_no_op(monkeypatch) -> None:
    """A redelivered dispatch and a sweep arriving together must be harmless.

    Asserted by calling it twice: the second pass touches no object store and
    does not move the attempt counter, so an idempotent retry cannot make a
    healthy job look like a struggling one.
    """
    _skip_without_database()
    w = _World()
    seen: list = []
    _stub_media(monkeypatch, finished=True, watcher=seen)

    async def _flow(factory):
        from sqlalchemy import text

        from app.core.db import superadmin_scope

        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await _seed(session, w, now=datetime.now(timezone.utc))
        try:
            async with factory() as session:
                async with session.begin():
                    async with superadmin_scope(session):
                        subject = next(
                            row
                            for row in await retention.jobs_due_for_purge(session)
                            if row.id == w.due_job
                        )
                        first = await retention.purge_job(session, subject)
                async with session.begin():
                    async with superadmin_scope(session):
                        second = await retention.purge_job(session, subject)
            async with factory() as session:
                async with superadmin_scope(session):
                    attempts = (
                        await session.execute(
                            text(
                                "SELECT assessment_purge_attempts FROM jobs "
                                "WHERE id = :j"
                            ),
                            {"j": str(w.due_job)},
                        )
                    ).scalar_one()
            return first, second, attempts
        finally:
            async with factory() as session:
                async with session.begin():
                    async with superadmin_scope(session):
                        await _cleanup(session, w)

    first, second, attempts = _run(_flow)

    assert first.completed is True
    assert second.completed is True
    assert second.media_deleted == 0
    assert second.rows is None
    assert len(seen) == 1
    assert attempts == 1


# ── The dispute route's own refusals ─────────────────────────────────────────


def test_a_dispute_reason_of_spaces_is_refused() -> None:
    """`min_length` on the raw string would accept three spaces.

    An unlock of withheld candidate data with no stated reason is an unlock
    nobody can review afterwards, so the validator strips first and the stored
    value is the stripped one.
    """
    import pydantic

    from app.schemas.jobs import AssessmentDisputeIn

    with pytest.raises(pydantic.ValidationError):
        AssessmentDisputeIn(reason="   ")
    assert AssessmentDisputeIn(reason="  Candidate queried the grade.  ").reason == (
        "Candidate queried the grade."
    )


class _JobSession:
    """Enough session for the dispute routes: they touch one job row."""

    def __init__(self, job) -> None:
        self.job = job

    async def flush(self) -> None:
        return None


async def _open_dispute(monkeypatch, job):
    from app.api import jobs as jobs_api
    from app.schemas.jobs import AssessmentDisputeIn

    recorded: list[dict] = []

    async def _visible(session, user, job_id):
        return job

    async def _record(session, **kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(jobs_api, "_get_visible_job", _visible)
    monkeypatch.setattr(jobs_api, "record_action", _record)
    out = await jobs_api.open_assessment_dispute(
        job.id,
        AssessmentDisputeIn(reason="The candidate queried their grade."),
        user=SimpleNamespace(
            user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), role=Role.client
        ),
        session=_JobSession(job),
    )
    return out, recorded


def _orm_job(**overrides):
    """A real `Job` instance, so the route writes to the mapped columns."""
    from app.models.job import Job

    job = Job(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        title="Platform Engineer",
        jd_json={},
    )
    for key, value in overrides.items():
        setattr(job, key, value)
    return job


@pytest.mark.asyncio
async def test_opening_a_dispute_records_who_when_and_why(monkeypatch) -> None:
    """The three columns AND the audit row, from one act.

    The columns exist rather than the audit row alone because a state that
    lives only in a log is a state nothing can index, refuse against or show
    on a screen. The audit row exists because the columns are overwritten by
    the next dispute and the log is not.
    """
    closed = datetime.now(timezone.utc) - timedelta(days=1)
    job = _orm_job(
        closed_at=closed, assessment_purge_due_at=retention.purge_due_at(closed)
    )
    out, recorded = await _open_dispute(monkeypatch, job)

    assert job.assessment_dispute_opened_at is not None
    assert job.assessment_dispute_opened_by is not None
    assert job.assessment_dispute_reason == "The candidate queried their grade."
    assert out.dispute_open is True
    assert out.state == retention.STATE_PENDING_DELETION
    # It does NOT extend the window: the due date the closure stamped stands.
    assert job.assessment_purge_due_at == retention.purge_due_at(closed)
    assert [entry["action"] for entry in recorded] == [
        retention.ACTION_DISPUTE_OPENED
    ]
    assert recorded[0]["metadata"]["reason"] == "The candidate queried their grade."


@pytest.mark.asyncio
async def test_a_dispute_cannot_be_opened_on_a_live_job(monkeypatch) -> None:
    """409, not a silent success.

    Recording a dispute on an open job would suggest the records had been
    withheld and released, when nothing was ever withheld.
    """
    with pytest.raises(HTTPException) as caught:
        await _open_dispute(monkeypatch, _orm_job())
    assert caught.value.status_code == 409


@pytest.mark.asyncio
async def test_a_dispute_cannot_be_opened_once_the_data_is_gone(
    monkeypatch,
) -> None:
    """410 with the server's own sentence.

    Offering to open a dispute over records that no longer exist would be a
    control that can only ever disappoint, and it would write a row claiming
    somebody has access to something nothing can produce.
    """
    closed = datetime.now(timezone.utc) - timedelta(days=40)
    job = _orm_job(
        closed_at=closed,
        assessment_purge_due_at=retention.purge_due_at(closed),
        assessment_purged_at=datetime.now(timezone.utc),
    )
    with pytest.raises(HTTPException) as caught:
        await _open_dispute(monkeypatch, job)
    assert caught.value.status_code == 410
    assert caught.value.detail == retention.PURGED_MESSAGE
    assert job.assessment_dispute_opened_at is None
