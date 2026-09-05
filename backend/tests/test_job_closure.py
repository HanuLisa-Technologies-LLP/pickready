"""Gate 8: a client closes a posting once the hiring requirement is met.

The 30 days are the LONGEST a posting runs, never the shortest. What has to be
true after a closure, and what has to stay untouched, is the whole subject of
this module:

  * candidates stop arriving -- public link, board, job page, apply route;
  * the hiring team loses nothing -- the pipeline, the reports and a candidate
    part-way through an assessment all continue;
  * the closure is a DECISION, so no arithmetic over dates can reinstate it.

The visibility rules are pure functions, so every boundary is asserted directly
rather than inferred from an endpoint -- the same argument `test_job_posting.py`
makes, and for the same reason: a wrong boundary here silently grants or
removes a real person's access.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models.enums import JobStatus, Role
from app.services import job_posting

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
#: Published eighteen days ago: comfortably inside the 30-day active window, so
#: every assertion below is about the CLOSURE and never about the window.
START = NOW - timedelta(days=18)
CLOSED = NOW - timedelta(hours=1)


def _window(**overrides):
    base = {
        "posting_start": START,
        "posting_end_date": job_posting.posting_end(START),
        "grace_period_end_date": job_posting.grace_end(START),
        "now": NOW,
    }
    base.update(overrides)
    return base


# ── The state itself ─────────────────────────────────────────────────────────

def test_a_live_job_closed_early_reports_closed_not_active() -> None:
    assert job_posting.posting_status(**_window()) == job_posting.STATUS_ACTIVE
    assert (
        job_posting.posting_status(**_window(closed_at=CLOSED))
        == job_posting.STATUS_CLOSED
    )


def test_closure_outranks_every_date_derived_state() -> None:
    """`closed` is checked FIRST, and that ordering is the rule.

    Checked after the window instead, a job closed on day 18 would keep
    reading as `active` for twelve more days -- which is precisely the twelve
    days the client closed it to avoid.
    """
    for offset, expected_without_closure in (
        (timedelta(days=-1), job_posting.STATUS_SCHEDULED),
        (timedelta(days=18), job_posting.STATUS_ACTIVE),
        (timedelta(days=32), job_posting.STATUS_GRACE),
        (timedelta(days=90), job_posting.STATUS_EXPIRED),
    ):
        moment = START + offset
        assert (
            job_posting.posting_status(START, now=moment)
            == expected_without_closure
        )
        assert (
            job_posting.posting_status(
                START, now=moment, closed_at=moment - timedelta(minutes=1)
            )
            == job_posting.STATUS_CLOSED
        )


def test_a_closure_stamped_in_the_future_has_not_happened_yet() -> None:
    """`now >= closed_at`, not `closed_at is not None`.

    A stamp is not the same as the moment arriving. Reading any non-null value
    as closed would make a scheduled closure take effect the instant it was
    written, which is the opposite of scheduling one.
    """
    assert (
        job_posting.posting_status(**_window(closed_at=NOW + timedelta(days=2)))
        == job_posting.STATUS_ACTIVE
    )


# ── What closure stops ───────────────────────────────────────────────────────

def test_no_new_application_is_accepted() -> None:
    assert job_posting.can_apply(**_window()) is True
    assert job_posting.can_apply(**_window(closed_at=CLOSED)) is False


def test_the_public_link_stops_working_immediately() -> None:
    assert job_posting.public_link_active(START, now=NOW) is True
    assert job_posting.public_link_active(START, now=NOW, closed_at=CLOSED) is False


@pytest.mark.parametrize("has_applied", [True, False])
def test_the_job_leaves_every_candidates_board(has_applied) -> None:
    """Including an existing applicant's.

    The board is where a candidate goes to find something to apply to, and a
    filled requisition is not that. Their APPLICATION is unaffected: it is
    listed from `job_candidate_links`, which this predicate never touches.
    """
    assert (
        job_posting.can_view_job(**_window(), has_applied=has_applied) is True
    )
    assert (
        job_posting.can_view_job(
            **_window(closed_at=CLOSED), has_applied=has_applied
        )
        is False
    )


# ── What closure must NOT stop ───────────────────────────────────────────────

def test_a_candidate_already_in_the_pipeline_can_still_finish() -> None:
    """`can_edit_application` deliberately takes no `closed_at`.

    This is the predicate that lets someone finish an assessment they were
    already invited to. Cutting it off would destroy work the client has
    already been charged for, on the day they told us the requisition went
    well. The absence of the parameter is the enforcement, so the signature is
    asserted rather than only the behaviour -- a later "consistency" edit that
    added it would otherwise pass every other test in this file.
    """
    import inspect

    signature = inspect.signature(job_posting.can_edit_application)
    assert "closed_at" not in signature.parameters

    assert (
        job_posting.can_edit_application(
            applied_at=START + timedelta(days=2), **_window()
        )
        is True
    )


def test_the_summary_tells_the_team_what_happened() -> None:
    window = job_posting.describe(
        SimpleNamespace(
            posting_start_date=START,
            posting_end_date=job_posting.posting_end(START),
            grace_period_end_date=job_posting.grace_end(START),
            closed_at=CLOSED,
            closed_reason="Offer accepted, two roles filled.",
        ),
        now=NOW,
    )
    assert window.posting_status == job_posting.STATUS_CLOSED
    assert window.is_closed is True
    assert window.is_active is False
    assert window.closed_reason == "Offer accepted, two roles filled."
    summary = window.summary()
    assert "Closed" in summary
    # The team is told their pipeline survives, because the fear on clicking
    # Close is that it does not.
    assert "pipeline" in summary
    # No number reaches a client through this string.
    assert not any(character.isdigit() for character in summary)


def test_an_unclosed_job_describes_exactly_as_before() -> None:
    """The new fields are additive: nothing about a normal job moved."""
    window = job_posting.describe(
        SimpleNamespace(
            posting_start_date=START,
            posting_end_date=job_posting.posting_end(START),
            grace_period_end_date=job_posting.grace_end(START),
            closed_at=None,
            closed_reason=None,
        ),
        now=NOW,
    )
    assert window.posting_status == job_posting.STATUS_ACTIVE
    assert window.is_closed is False
    assert window.closed_at is None


# ── The route ────────────────────────────────────────────────────────────────

class _CloseSession:
    """Records the statements `close_job` runs, so the closure notice can be
    asserted without a database."""

    def __init__(self, job) -> None:
        self.job = job
        self.flushed = False
        self.statements: list = []

    async def get(self, model, ident):
        return self.job

    async def flush(self):
        self.flushed = True

    async def execute(self, statement, params=None, *args, **kwargs):
        self.statements.append((statement, params))
        return SimpleNamespace(
            scalar_one_or_none=lambda: "Acme Corp",
            scalar_one=lambda: "Acme Corp",
        )

    @property
    def closure_notice(self):
        """The bulk INSERT that writes the Updates feed, if it ran."""
        for statement, params in self.statements:
            if params and "kind" in params:
                return str(statement), params
        return None


def _closable_job():
    from app.models.job import Job

    return Job(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        title="Platform Engineer",
        jd_json={},
        status=JobStatus.ratified,
        ratified_at=NOW - timedelta(days=18),
        posting_start_date=START,
        created_by=uuid.uuid4(),
        created_at=START,
    )


def _user(tenant_id):
    return SimpleNamespace(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        role=Role.client,
        audience="org",
    )


async def _close(monkeypatch, job, reason=None):
    from app.api import jobs as jobs_api
    from app.schemas.jobs import JobCloseIn

    calls: dict = {}

    async def _fake_audit(session, **kwargs):
        calls["audit"] = kwargs

    async def _fake_visible(session, user, job_id):
        return job

    async def _fake_invalidate(job_id):
        calls["invalidated"] = job_id

    monkeypatch.setattr(jobs_api, "audit", _fake_audit)
    monkeypatch.setattr(jobs_api, "_get_visible_job", _fake_visible)
    monkeypatch.setattr(jobs_api, "_invalidate_public_job", _fake_invalidate)
    monkeypatch.setattr(
        jobs_api, "get_settings",
        lambda: SimpleNamespace(frontend_url="https://readypick.ai"),
    )
    session = _CloseSession(job)
    out = await jobs_api.close_job(
        job.id,
        JobCloseIn(reason=reason),
        user=_user(job.tenant_id),
        session=session,
    )
    calls["session"] = session
    return out, calls


@pytest.mark.asyncio
async def test_closing_stamps_the_moment_and_records_the_reason(monkeypatch) -> None:
    from app.services import hiring_pipeline

    job = _closable_job()
    out, calls = await _close(monkeypatch, job, reason="  Requirement met.  ")

    assert job.closed_at is not None
    assert job.closed_reason == "Requirement met."
    assert (
        job.lifecycle_state
        == hiring_pipeline.JobLifecycleState.CLOSED_ARCHIVED.value
    )
    # The read-time window on the response already reports the new state.
    assert out.posting_status == job_posting.STATUS_CLOSED
    assert out.closed_reason == "Requirement met."
    # "Why did this requisition stop" is the question the audit log exists to
    # answer, and the client typed the answer.
    assert calls["audit"]["action"] == "job_closed"
    assert calls["audit"]["metadata"]["reason"] == "Requirement met."
    # The cached public payload is dropped, or the 404 would take minutes.
    assert calls["invalidated"] == job.id


@pytest.mark.asyncio
async def test_closing_without_a_reason_is_normal(monkeypatch) -> None:
    """An empty note is stored as NULL, not as an empty string.

    The CHECK constraint reads "a reason implies a closure"; an empty string
    would satisfy it while telling a reader the client said something.
    """
    job = _closable_job()
    await _close(monkeypatch, job, reason="   ")
    assert job.closed_at is not None
    assert job.closed_reason is None


@pytest.mark.asyncio
async def test_closing_twice_is_refused(monkeypatch) -> None:
    """Restamping would move the moment applications actually stopped.

    Same argument publish makes about a second `posting_start_date` stamp.
    """
    job = _closable_job()
    await _close(monkeypatch, job)
    first = job.closed_at
    with pytest.raises(HTTPException) as exc:
        await _close(monkeypatch, job)
    assert exc.value.status_code == 409
    assert job.closed_at == first


@pytest.mark.asyncio
async def test_an_unpublished_draft_cannot_be_closed(monkeypatch) -> None:
    """There is no posting to stop, and Close would read as Delete."""
    job = _closable_job()
    job.ratified_at = None
    with pytest.raises(HTTPException) as exc:
        await _close(monkeypatch, job)
    assert exc.value.status_code == 409
    assert "Archive" in exc.value.detail


@pytest.mark.asyncio
async def test_closing_tells_everyone_still_waiting(monkeypatch) -> None:
    """They applied and are now waiting for an answer that is not coming.

    Leaving them to work it out from a link that stopped working is the exact
    silence the Updates feed exists to end.
    """
    from app.services import candidate_updates

    job = _closable_job()
    _out, calls = await _close(monkeypatch, job)
    notice = calls["session"].closure_notice
    assert notice is not None, "closing a job must write the candidates' feed"
    sql, params = notice
    assert params["kind"] == candidate_updates.JOB_CLOSED
    # ONE statement for the whole job: a popular posting has hundreds of
    # applicants and this runs inside the request that closed it.
    assert "INSERT INTO candidate_updates" in sql
    assert "SELECT" in sql
    # Three exclusions, all deliberate. A sourced candidate never applied; a
    # rejected one has already had their outcome and a closure notice after a
    # rejection reads as a second rejection; archived is not a live application.
    assert "'sourced', 'rejected', 'joined'" in sql
    assert "archived_at IS NULL" in sql
    # No number and no grade reaches the candidate through it.
    assert not any(character.isdigit() for character in params["body"])


def test_there_is_no_reopen_route() -> None:
    """RBAC 22 asks for a controlled revision mechanism, never a reopen.

    Reopening restarts a window candidates have already been told is over. The
    enforcement is the ABSENCE of the route, so absence is what is asserted.
    """
    from app.api import jobs as jobs_api

    paths = {route.path for route in jobs_api.router.routes}
    assert "/{job_id}/close" in paths
    assert not any("reopen" in path for path in paths)
