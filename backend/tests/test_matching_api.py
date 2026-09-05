from datetime import datetime, timezone
from types import SimpleNamespace
import uuid

import pytest

from app.api import matching as matching_api
from app.api.deps import CurrentUser
from app.models.enums import JobStatus, Role
from app.schemas.candidates import LinkOut
from app.schemas.matching import MatchResultOut


class _CountResult:
    def scalar_one(self):
        return 30


class _Session:
    async def execute(self, _query):
        return _CountResult()


@pytest.mark.asyncio
async def test_run_matching_returns_task_id_and_candidate_count(monkeypatch):
    job_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    user = CurrentUser(uuid.uuid4(), tenant_id, Role.recruiter, "org")
    job = SimpleNamespace(
        id=job_id,
        tenant_id=tenant_id,
        ratified_at=datetime.now(timezone.utc),
        status=JobStatus.ratified,
        archived_at=None,
        assessment_status="ready_for_candidates",
    )

    async def _job(*_args):
        return job

    async def _audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(matching_api, "_get_job", _job)
    monkeypatch.setattr(matching_api, "audit", _audit)
    monkeypatch.setattr(
        matching_api,
        "dispatch",
        lambda *_args, **_kwargs: SimpleNamespace(id="task-123"),
    )

    out = await matching_api.run_matching(
        job_id, user=user, session=_Session()
    )
    assert out.task_id == "task-123"
    assert out.candidate_count == 30


@pytest.mark.asyncio
async def test_matching_task_status_reports_completion(monkeypatch):
    from app.workers import status as task_status

    async def _read(run_id):
        return task_status.RunStatus(
            run_id=run_id, state=task_status.STATE_SUCCESS, payload={}
        )

    monkeypatch.setattr(matching_api.task_status, "read", _read)
    out = await matching_api.matching_task_status("task-123", _user=None)
    assert out.done is True
    assert out.state == "SUCCESS"


@pytest.mark.asyncio
async def test_matching_task_status_never_renders_a_failure_as_stages(monkeypatch):
    """A failed run draws the empty plan, not whatever the failure left behind.

    The Celery version of this endpoint read `result.info`, which is the stage
    payload only while the task is in PROGRESS and is the EXCEPTION on failure.
    The replacement records the class name in a separate field for the same
    reason: the stage list is rendered on a recruiter's screen.
    """
    from app.workers import status as task_status

    async def _read(run_id):
        return task_status.RunStatus(
            run_id=run_id,
            state=task_status.STATE_FAILURE,
            payload={},
            error="ValueError",
        )

    monkeypatch.setattr(matching_api.task_status, "read", _read)
    out = await matching_api.matching_task_status("task-123", _user=None)
    assert out.done is True
    assert out.state == "FAILURE"
    assert [stage.status for stage in out.stages] == ["pending"] * len(out.stages)


def test_client_matching_schemas_do_not_expose_numeric_scores():
    assert "match_score" not in MatchResultOut.model_fields
    assert "match_score" not in LinkOut.model_fields
