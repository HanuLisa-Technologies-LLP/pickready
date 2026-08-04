"""What opens a job to candidates, after the technical-review step was removed.

CHANGED 2026-08-04, client decision: the technical question bank no longer gates
anything. Generated questions are usable immediately. The PPI FRAMEWORK review
is KEPT and is now the only thing standing between a job and its candidates.

These tests exist because the previous rule -- "BOTH halves, and approving one
does not open the job" -- was a documented hard rule with NO test anywhere in
the suite. Changing `_refresh_setup_status` left all 896 tests green, which
proved nothing either way. The rule that replaces it is pinned here so the next
change to it fails loudly rather than silently.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.api.assessments import (
    PENDING_REVIEW,
    READY_FOR_CANDIDATES,
    _refresh_setup_status,
)


class _Session:
    """Only `flush` is reached: _refresh_setup_status mutates the job in place
    and never queries."""

    def __init__(self) -> None:
        self.flushed = 0

    async def flush(self) -> None:
        self.flushed += 1


def _job(*, framework: bool, questions: bool, status: str = PENDING_REVIEW):
    stamp = datetime.now(timezone.utc)
    return SimpleNamespace(
        assessment_status=status,
        framework_approved_at=stamp if framework else None,
        questions_approved_at=stamp if questions else None,
    )


@pytest.mark.asyncio
async def test_framework_alone_opens_the_job() -> None:
    """The change, stated directly. Under the old rule this stayed pending."""
    job = _job(framework=True, questions=False)
    await _refresh_setup_status(_Session(), job)
    assert job.assessment_status == READY_FOR_CANDIDATES


@pytest.mark.asyncio
async def test_technical_approval_alone_does_not_open_the_job() -> None:
    """The technical bank is not a gate in EITHER direction.

    It no longer blocks a job, and it must not open one on its own either --
    otherwise removing one gate would have quietly removed both, and candidates
    would be assessed against a framework nobody confirmed.
    """
    job = _job(framework=False, questions=True)
    await _refresh_setup_status(_Session(), job)
    assert job.assessment_status == PENDING_REVIEW


@pytest.mark.asyncio
async def test_neither_approved_stays_pending() -> None:
    job = _job(framework=False, questions=False)
    await _refresh_setup_status(_Session(), job)
    assert job.assessment_status == PENDING_REVIEW


@pytest.mark.asyncio
async def test_unapproving_the_framework_closes_an_open_job() -> None:
    """Reopening the framework must walk the job back.

    `_refresh_setup_status` is called after the framework is cleared, and if it
    only ever moved a job forwards, a job would keep saying
    `ready_for_candidates` while its criteria were being edited.
    """
    job = _job(framework=False, questions=True, status=READY_FOR_CANDIDATES)
    await _refresh_setup_status(_Session(), job)
    assert job.assessment_status == PENDING_REVIEW


@pytest.mark.asyncio
async def test_the_session_is_always_flushed() -> None:
    """Including when the status did not change: the caller may have mutated
    the approval stamps on the same job and relies on this flush."""
    session = _Session()
    job = _job(framework=True, questions=True, status=READY_FOR_CANDIDATES)
    await _refresh_setup_status(session, job)
    assert job.assessment_status == READY_FOR_CANDIDATES
    assert session.flushed == 1
