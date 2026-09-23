"""Reopening a saved matrix is refused once an assessment contract exists.

WHAT THIS PINS, AND WHY THE BOUNDARY MOVED TWICE

The guard asked for a `functional_skills_reports` row until 2026-09-22. A
report exists only at the END of an assessment, so between the invitation and
synthesis the matrix was reopenable underneath somebody already answering
questions derived from it: their questions came from one version and their
grade would have been written against another.

The repair over-corrected to "any `job_candidate_links` row", which refuses to
reopen a matrix the moment the first CV lands on a job nobody has been invited
to. There is no reopen after that, so a typo caught on the day the posting
went live became permanent.

The line is the ISSUED CONTRACT: an `assessment_conversations` row, which IS
the invitation, or a `candidate_questions` row written against the frozen
matrix. Applying is not being assessed, and somebody who applies before a
revision and is invited after it is assessed against the revision, which is
the only contract that was ever used on them.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import assessments


class _Result:
    def __init__(self, value: int):
        self.value = value

    def scalar_one(self) -> int:
        return self.value


class _Session:
    def __init__(self, contracted: int):
        self.contracted = contracted
        self.query = None

    async def execute(self, query):
        self.query = query
        return _Result(self.contracted)


def _setup(monkeypatch, *, contracted: int):
    approved_at = datetime(2026, 9, 1, tzinfo=timezone.utc)
    job = SimpleNamespace(id=uuid.uuid4(), framework_approved_at=approved_at)
    user = SimpleNamespace(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    session = _Session(contracted)
    calls: list[str] = []

    async def staff_job(*_args):
        return job

    async def refresh(*_args):
        calls.append("refresh")

    async def audit(*_args, **_kwargs):
        calls.append("audit")

    async def invalidate(*_args):
        calls.append("invalidate")

    async def framework_out(*_args):
        return "reopened"

    monkeypatch.setattr(assessments, "_staff_job", staff_job)
    monkeypatch.setattr(assessments, "_refresh_setup_status", refresh)
    monkeypatch.setattr(assessments, "audit", audit)
    monkeypatch.setattr(assessments, "_invalidate_framework", invalidate)
    monkeypatch.setattr(assessments, "_framework_out", framework_out)
    return job, user, session, calls, approved_at


def test_reopen_refuses_once_an_assessment_contract_has_been_issued(monkeypatch):
    job, user, session, calls, approved_at = _setup(monkeypatch, contracted=1)

    with pytest.raises(HTTPException) as error:
        asyncio.run(assessments.reopen_framework(job.id, user, session))

    assert error.value.status_code == 409
    # Nothing moved. A refusal that had already cleared the stamp would leave
    # the job unfrozen and unreopenable at once.
    assert job.framework_approved_at == approved_at
    assert calls == []


def test_reopen_is_allowed_while_nobody_has_been_invited(monkeypatch):
    job, user, session, calls, _ = _setup(monkeypatch, contracted=0)

    result = asyncio.run(assessments.reopen_framework(job.id, user, session))

    assert result == "reopened"
    assert job.framework_approved_at is None
    assert calls == ["refresh", "audit", "invalidate"]


def test_the_guard_counts_the_contract_and_not_the_application(monkeypatch):
    """The regression this file exists for, asserted on the query itself.

    Both halves matter and the negative one matters most: a guard that counts
    `job_candidate_links` refuses every job with an applicant, which reads as
    the same 409 and is a different rule.
    """
    job, user, session, _calls, _ = _setup(monkeypatch, contracted=0)

    asyncio.run(assessments.reopen_framework(job.id, user, session))

    query = str(session.query)
    assert "assessment_conversations" in query
    assert "candidate_questions" in query
    assert "job_candidate_links" not in query
