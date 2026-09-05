"""Operational Hygiene: the status-hygiene pre-check for new Job Setup.

Provenance: add-features-specdoc (2026-09-05), "Operational Hygiene" section.
An employer should update all applicant statuses from earlier jobs before
posting a new one. The locked consideration is the whole subject of the last
test in this file: this is a STRONG REMINDER and never a hard block, because
an urgent hire must not stall on unrelated old-job cleanup.

What is pinned here:

  * which pipeline stages count as resolved (the FSM's terminal set plus
    `sourced`, which never applied), and that `hold` does NOT;
  * which jobs count as "earlier" (closed, grace, expired), and that a live
    in-window job is never nagged about;
  * a tenant whose old jobs are all decided gets an EMPTY summary;
  * unresolved applications on a closed job are counted per job;
  * the endpoint sits behind the existing job-creation capability; and
  * POST /jobs still succeeds while hygiene items exist, structurally and
    functionally, because the reminder is advisory by locked decision.
"""
from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services import hiring_pipeline, job_posting, status_hygiene

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
#: Published forty days ago: past the 30-day window AND the 5-day grace tail.
START_EXPIRED = NOW - timedelta(days=40)
#: Published five days ago: comfortably inside the active window.
START_ACTIVE = NOW - timedelta(days=5)
#: Published eighteen days ago and closed yesterday: closed dominates active.
START_CLOSED = NOW - timedelta(days=18)
CLOSED_AT = NOW - timedelta(days=1)


def _job_row(
    *,
    title: str,
    posting_start: datetime,
    closed_at: datetime | None = None,
    unresolved: int = 1,
) -> dict:
    return {
        "id": uuid.uuid4(),
        "title": title,
        "posting_start_date": posting_start,
        "posting_end_date": job_posting.posting_end(posting_start),
        "grace_period_end_date": job_posting.grace_end(posting_start),
        "closed_at": closed_at,
        "unresolved": unresolved,
    }


class _HygieneSession:
    """Returns canned grouped rows and records the query that asked for them."""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.queries: list[tuple[str, dict | None]] = []

    async def execute(self, statement, params=None):
        self.queries.append((str(statement), params))
        rows = self.rows

        class _Result:
            def mappings(self):
                return self

            def all(self):
                return rows

        return _Result()


# ── Which stages are resolved ────────────────────────────────────────────────

def test_resolved_is_the_terminal_set_plus_sourced() -> None:
    """Read from the FSM, never restated: a stage added to `hiring_pipeline`
    cannot silently drift this module's idea of "decided"."""
    assert (
        status_hygiene.RESOLVED_STATUSES
        == hiring_pipeline.TERMINAL | {hiring_pipeline.SOURCED}
    )
    # Every member is a real pipeline status, so a typo in the set would be
    # a string the mirror column never contains and the filter a no-op.
    assert status_hygiene.RESOLVED_STATUSES <= hiring_pipeline.ALL_STATUSES


def test_sourced_is_not_owed_a_status_update() -> None:
    """Gate 5: a sourced row is a resume in a filing cabinet, not an
    application. Nobody applied, so nobody is waiting on a decision."""
    assert hiring_pipeline.SOURCED in status_hygiene.RESOLVED_STATUSES


def test_hold_is_deliberately_unresolved() -> None:
    """A pause is a decision to decide later, and on a job that has ended,
    later has arrived. The reminder is where a forgotten hold resurfaces."""
    assert hiring_pipeline.HOLD not in status_hygiene.RESOLVED_STATUSES
    # And the live middle of the funnel is unresolved too, obviously.
    for stage in (
        hiring_pipeline.APPLIED,
        hiring_pipeline.ASSESSMENT_INVITED,
        hiring_pipeline.SHORTLISTED,
        hiring_pipeline.OFFER_EXTENDED,
        hiring_pipeline.OFFERED,  # the legacy synonym is non-terminal as well
    ):
        assert stage not in status_hygiene.RESOLVED_STATUSES


def test_earlier_means_the_posting_is_over() -> None:
    """Closed, grace and expired qualify; active and scheduled never do.

    The grace tail accepts no new application, so for hygiene purposes the
    posting is over. A live job's applicants are being worked, and nagging
    about them would train recruiters to dismiss the reminder."""
    assert status_hygiene.EARLIER_POSTING_STATUSES == frozenset(
        {
            job_posting.STATUS_CLOSED,
            job_posting.STATUS_GRACE,
            job_posting.STATUS_EXPIRED,
        }
    )


# ── The summary itself ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_all_terminal_old_jobs_return_an_empty_summary() -> None:
    """The grouped query joins on unresolved links, so a tenant whose old jobs
    are all decided produces zero rows and an empty, zero-total summary."""
    session = _HygieneSession(rows=[])
    summary = await status_hygiene.unresolved_summary(
        session, uuid.uuid4(), now=NOW
    )
    assert summary.jobs == []
    assert summary.total_unresolved == 0
    # The SQL excluded exactly the resolved set, from the constant rather than
    # a second hand-typed list that could drift from it.
    sql, params = session.queries[0]
    assert "NOT IN" in sql
    assert set(params["resolved"]) == set(status_hygiene.RESOLVED_STATUSES)
    assert "archived_at IS NULL" in sql


@pytest.mark.asyncio
async def test_unresolved_applications_on_a_closed_job_are_counted() -> None:
    row = _job_row(
        title="Platform Engineer",
        posting_start=START_CLOSED,
        closed_at=CLOSED_AT,
        unresolved=3,
    )
    summary = await status_hygiene.unresolved_summary(
        _HygieneSession([row]), uuid.uuid4(), now=NOW
    )
    assert len(summary.jobs) == 1
    item = summary.jobs[0]
    assert item.job_id == row["id"]
    assert item.title == "Platform Engineer"
    assert item.posting_status == job_posting.STATUS_CLOSED
    assert item.unresolved_count == 3
    assert summary.total_unresolved == 3


@pytest.mark.asyncio
async def test_a_live_in_window_job_is_not_earlier() -> None:
    """A job still taking applications is never nagged about, however many
    undecided applicants it carries. Its expired sibling still is."""
    live = _job_row(title="Live Role", posting_start=START_ACTIVE, unresolved=9)
    over = _job_row(title="Old Role", posting_start=START_EXPIRED, unresolved=2)
    summary = await status_hygiene.unresolved_summary(
        _HygieneSession([live, over]), uuid.uuid4(), now=NOW
    )
    assert [item.title for item in summary.jobs] == ["Old Role"]
    assert summary.jobs[0].posting_status == job_posting.STATUS_EXPIRED
    assert summary.total_unresolved == 2


@pytest.mark.asyncio
async def test_a_job_in_its_grace_tail_counts_as_earlier() -> None:
    """Day 32: past the 30-day window, inside the 5-day edit tail. No new
    application can arrive, so the posting is over for hygiene purposes."""
    row = _job_row(
        title="Grace Role",
        posting_start=NOW - timedelta(days=32),
        unresolved=1,
    )
    summary = await status_hygiene.unresolved_summary(
        _HygieneSession([row]), uuid.uuid4(), now=NOW
    )
    assert [item.posting_status for item in summary.jobs] == [
        job_posting.STATUS_GRACE
    ]


# ── The endpoint ─────────────────────────────────────────────────────────────

def _precheck_route():
    from app.api import jobs as jobs_api

    return next(
        route
        for route in jobs_api.router.routes
        if route.path == "/setup/status-hygiene"
    )


def test_the_precheck_is_behind_the_job_creation_capability() -> None:
    """The existing CREATE_JOB capability, deliberately not a new constant: a
    new capability is half a change without its seeding migration, and this
    read exists solely to precede the action CREATE_JOB already governs."""
    from app.services import capabilities as caps

    route = _precheck_route()
    assert route.methods == {"GET"}
    parameter = inspect.signature(route.endpoint).parameters["user"]
    gate = parameter.default.dependency
    assert gate.__qualname__.startswith("require_capability")
    assert inspect.getclosurevars(gate).nonlocals["capability"] == caps.CREATE_JOB


def test_the_precheck_registers_before_the_job_id_route() -> None:
    """`/setup/status-hygiene` must not be swallowed by `/{job_id}` reading
    "setup" as a UUID. Starlette matches in registration order, so the order
    is asserted rather than assumed."""
    from app.api import jobs as jobs_api

    paths = [route.path for route in jobs_api.router.routes]
    assert paths.index("/setup/status-hygiene") < paths.index("/{job_id}")


# ── The locked decision: a reminder, never a block ───────────────────────────

def test_job_creation_never_consults_the_hygiene_check() -> None:
    """Structural half. The enforcement of "advisory, never a gate" is the
    ABSENCE of the call: `create_job` does not import, reference or await
    anything from the hygiene service, so no future summary shape can turn
    the reminder into a refusal without failing this test."""
    from app.api import jobs as jobs_api

    source = inspect.getsource(jobs_api.create_job)
    assert "status_hygiene" not in source
    assert "unresolved" not in source


@pytest.mark.asyncio
async def test_a_job_is_created_while_hygiene_items_exist(monkeypatch) -> None:
    """Functional half: POST /jobs succeeds with a non-empty hygiene summary
    standing. The stubs mirror `test_jobs._stub_create_deps` because every
    OTHER gate on create is out of scope here and tested in its own module."""
    from app.api import jobs as jobs_api
    from app.models.enums import JobStatus, Role
    from app.schemas.jobs import JDIn, JobCreateIn
    from app.services import credits
    from app.services.hiring import company_requirements

    # The hygiene summary is loudly non-empty. If create_job consulted it at
    # all, this is the shape that would trip a gate.
    async def _dirty_summary(session, tenant_id, now=None):
        return status_hygiene.StatusHygieneSummary(
            jobs=[
                status_hygiene.JobHygieneItem(
                    job_id=uuid.uuid4(),
                    title="Old Role",
                    posting_status=job_posting.STATUS_CLOSED,
                    unresolved_count=7,
                )
            ],
            total_unresolved=7,
        )

    monkeypatch.setattr(status_hygiene, "unresolved_summary", _dirty_summary)

    async def _funded(*a, **k):
        return True

    monkeypatch.setattr(credits, "has_positive_balance", _funded)

    async def _dna_complete(session, tenant_id):
        return True

    monkeypatch.setattr(company_requirements, "is_complete", _dna_complete)

    async def _fake_audit(session, **kwargs):
        return None

    async def _fake_publish(session, job):
        job.status = JobStatus.ratified
        job.ratified_at = datetime.now(timezone.utc)

    monkeypatch.setattr(jobs_api, "audit", _fake_audit)
    monkeypatch.setattr(jobs_api.fsm, "apply_direct_publish", _fake_publish)
    monkeypatch.setattr(jobs_api, "dispatch", lambda *a, **k: None)
    monkeypatch.setattr(
        jobs_api,
        "get_settings",
        lambda: SimpleNamespace(frontend_url="https://readypick.ai"),
    )

    class _FakeSession:
        def __init__(self) -> None:
            self.added: list = []

        def add(self, obj) -> None:
            self.added.append(obj)
            self._stamp(obj)

        async def flush(self) -> None:
            for obj in self.added:
                self._stamp(obj)

        async def execute(self, *a, **k):
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(first=lambda: None, all=lambda: []),
                scalar=lambda: None,
                scalar_one=lambda: None,
                scalar_one_or_none=lambda: None,
                first=lambda: None,
                all=lambda: [],
            )

        async def get(self, model, ident):
            return None

        async def refresh(self, obj, attribute_names=None) -> None:
            self._stamp(obj)
            start = getattr(obj, "posting_start_date", None)
            if start is not None:
                if getattr(obj, "posting_end_date", None) is None:
                    obj.posting_end_date = start + timedelta(days=30)
                if getattr(obj, "grace_period_end_date", None) is None:
                    obj.grace_period_end_date = start + timedelta(days=35)

        @staticmethod
        def _stamp(obj) -> None:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()
            if hasattr(obj, "created_at") and getattr(obj, "created_at", None) is None:
                obj.created_at = datetime.now(timezone.utc)

    user = SimpleNamespace(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        role=Role.recruiter,
        audience="org",
    )
    out = await jobs_api.create_job(
        JobCreateIn(
            title="Backend Engineer",
            grade="non_managerial",
            jd=JDIn(role="Own APIs", skills=["Python"]),
        ),
        user=user,
        session=_FakeSession(),
    )
    assert out.status == JobStatus.ratified
    assert out.public_url is not None
