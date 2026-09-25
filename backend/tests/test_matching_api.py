"""The AI Matching routes as plain coroutines: the gates and the status payload.

The real-database halves (the audit row read back from a second connection,
the cross-tenant 404, the dispatch after commit) are in
`test_matching_task_status_tenancy.py`. These pin the refusals and the payload
shaping, which need no database.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
import uuid

import pytest
from fastapi import HTTPException

from app.api import matching as matching_api
from app.api.deps import CurrentUser
from app.models.enums import JobStatus, Role
from app.schemas.matching import MatchingTaskStatusOut, RunMatchingOut


class _CountResult:
    def scalar_one(self):
        return 30


class _Session:
    async def execute(self, _query):
        return _CountResult()


def _job(**overrides):
    base = dict(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        ratified_at=datetime.now(timezone.utc),
        status=JobStatus.ratified,
        archived_at=None,
        closed_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _user(job):
    return CurrentUser(uuid.uuid4(), job.tenant_id, Role.recruiter, "org")


@pytest.fixture
def wired(monkeypatch):
    """The route with its collaborators replaced; returns what they saw."""
    seen: dict = {"dispatched": [], "audited": [], "saved": True}

    async def _audit(*_args, **kwargs):
        seen["audited"].append(kwargs)

    async def _skills_saved(_db, _job_id):
        return seen["saved"]

    def _dispatch_after_commit(session, name, *, args=None, kwargs=None):
        seen["dispatched"].append((name, args))
        return SimpleNamespace(id="task-123")

    monkeypatch.setattr(matching_api, "audit", _audit)
    monkeypatch.setattr(matching_api.assessment_contract, "skills_saved", _skills_saved)
    monkeypatch.setattr(matching_api, "dispatch_after_commit", _dispatch_after_commit)
    return seen


def _install_job(monkeypatch, job):
    async def _get(*_args):
        return job

    monkeypatch.setattr(matching_api, "_get_job", _get)


@pytest.mark.asyncio
async def test_run_matching_dispatches_after_commit_and_audits_the_task_id(
    monkeypatch, wired
):
    job = _job()
    _install_job(monkeypatch, job)
    out = await matching_api.run_matching(job.id, user=_user(job), session=_Session())
    assert out.task_id == "task-123"
    assert out.candidate_count == 30
    assert wired["dispatched"] == [("pickready.run_matching", [str(job.id)])]
    # The status route proves a task id through this row, so the id must be
    # on it; without it every poll of a real run would 404.
    assert wired["audited"][0]["metadata"] == {"task_id": "task-123"}
    assert wired["audited"][0]["action"] == matching_api.ACTION_MATCHING_TRIGGERED


@pytest.mark.parametrize(
    "overrides, saved, detail",
    [
        ({"ratified_at": None}, True, matching_api.DETAIL_NOT_PUBLISHED),
        ({"archived_at": datetime.now(timezone.utc)}, True, matching_api.DETAIL_ARCHIVED),
        ({"closed_at": datetime.now(timezone.utc)}, True, matching_api.DETAIL_CLOSED),
        ({}, False, matching_api.DETAIL_SKILLS_NOT_SAVED),
    ],
)
@pytest.mark.asyncio
async def test_run_matching_refuses_with_the_server_sentence_and_starts_nothing(
    monkeypatch, wired, overrides, saved, detail
):
    job = _job(**overrides)
    _install_job(monkeypatch, job)
    wired["saved"] = saved
    with pytest.raises(HTTPException) as refused:
        await matching_api.run_matching(job.id, user=_user(job), session=_Session())
    assert refused.value.status_code == 409
    assert refused.value.detail == detail
    assert wired["dispatched"] == []
    assert wired["audited"] == []


def test_the_skills_sentence_is_the_one_the_plan_names():
    assert matching_api.DETAIL_SKILLS_NOT_SAVED == (
        "Save the skills on this job before running AI Matching."
    )


async def _status(monkeypatch, run_status, *, started=True):
    job = _job()
    _install_job(monkeypatch, job)

    async def _started(*_args):
        return started

    async def _read(_run_id):
        return run_status

    monkeypatch.setattr(matching_api, "_task_was_started_for", _started)
    monkeypatch.setattr(matching_api.task_status, "read", _read)
    return await matching_api.matching_task_status(
        job.id, "task-123", user=_user(job), session=_Session()
    )


@pytest.mark.asyncio
async def test_matching_task_status_reports_completion(monkeypatch):
    from app.workers import status as task_status

    out = await _status(
        monkeypatch,
        task_status.RunStatus(run_id="task-123", state=task_status.STATE_SUCCESS, payload={}),
    )
    assert out.done is True
    assert out.state == "SUCCESS"
    assert out.degraded is False
    assert out.degraded_reasons == []


@pytest.mark.asyncio
async def test_matching_task_status_never_renders_a_failure_as_stages(monkeypatch):
    """A failed run draws the empty plan, not whatever the failure left behind:
    on failure the record holds the exception CLASS NAME, and the stage list is
    rendered on a recruiter's screen."""
    from app.workers import status as task_status

    out = await _status(
        monkeypatch,
        task_status.RunStatus(
            run_id="task-123", state=task_status.STATE_FAILURE, payload={},
            error="ValueError",
        ),
    )
    assert out.done is True
    assert out.state == "FAILURE"
    assert [stage.status for stage in out.stages] == ["pending"] * len(out.stages)


@pytest.mark.asyncio
async def test_a_degraded_run_says_so_in_the_servers_words(monkeypatch):
    from app.services import matching_progress
    from app.workers import status as task_status

    payload = matching_progress.empty_payload(operation_id="task-123")
    payload["degraded"] = True
    payload["degraded_reasons"] = [
        "The embedding service was unavailable, so this run found candidates "
        "by keywords only."
    ]
    out = await _status(
        monkeypatch,
        task_status.RunStatus(
            run_id="task-123", state=matching_progress.STATE_PROGRESS, payload=payload
        ),
    )
    assert out.degraded is True
    assert out.degraded_reasons == payload["degraded_reasons"]


@pytest.mark.asyncio
async def test_a_task_this_job_did_not_start_is_not_found(monkeypatch):
    from app.workers import status as task_status

    with pytest.raises(HTTPException) as refused:
        await _status(
            monkeypatch,
            task_status.RunStatus(run_id="task-123", state=task_status.STATE_SUCCESS, payload={}),
            started=False,
        )
    assert refused.value.status_code == 404


def test_client_matching_schemas_do_not_expose_numeric_scores():
    # By whole name part: `scored_count` is a count of rows the run finished,
    # which the stage list has always shown; `match_score` would be a score.
    for model in (RunMatchingOut, MatchingTaskStatusOut):
        assert not any(
            part in {"score", "percent", "percentage", "rank", "tier", "weight"}
            for name in model.model_fields
            for part in name.split("_")
        ), model.__name__


def test_the_results_route_and_the_unscoped_status_route_are_gone():
    paths = {getattr(route, "path", "") for route in matching_api.router.routes}
    assert "/jobs/{job_id}/results" not in paths
    assert "/tasks/{task_id}" not in paths
    assert "/jobs/{job_id}/tasks/{task_id}" in paths
