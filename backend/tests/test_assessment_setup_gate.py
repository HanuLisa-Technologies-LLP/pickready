"""What opens a job to candidates.

The rule has moved twice and both moves are worth stating, because the second
one restores a shape the first one removed for a different reason.

2026-08-04: the technical question bank stopped gating anything. The PPI matrix
review was kept, and was for a while the only gate.

Draft v4: the setup session has TWO halves finalised together (spec §10) -- the
PPI matrix and the job's Matching category list -- and a job is open only when
both are stamped. That is NOT a return to the 2026-08-04 rule. The technical
bank was withdrawn because a weak question cost one item on one report; a
matching category list nobody confirmed decides how every sourced resume on the
job is ranked, which is a comparability guarantee of exactly the kind the matrix
review exists to give.

These tests exist because the ORIGINAL both-halves rule was a documented hard
rule with NO test anywhere in the suite. Changing `_refresh_setup_status` left
all 896 tests green, which proved nothing either way. Every rule that has
replaced it since is pinned here so the next change fails loudly.
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


def _job(
    *,
    framework: bool,
    categories: bool,
    questions: bool = False,
    swot: bool = True,
    status: str = PENDING_REVIEW,
):
    stamp = datetime.now(timezone.utc)
    return SimpleNamespace(
        assessment_status=status,
        framework_approved_at=stamp if framework else None,
        matching_categories_finalized_at=stamp if categories else None,
        questions_approved_at=stamp if questions else None,
        swot_completed_at=stamp if swot else None,
    )


@pytest.mark.asyncio
async def test_both_halves_open_the_job() -> None:
    job = _job(framework=True, categories=True)
    await _refresh_setup_status(_Session(), job)
    assert job.assessment_status == READY_FOR_CANDIDATES


@pytest.mark.asyncio
async def test_the_matrix_alone_does_not_open_the_job() -> None:
    """Draft v4's change, stated directly. Under the previous rule this opened.

    A job whose matching categories were never confirmed would rank its entire
    pipeline against a list nobody read.
    """
    job = _job(framework=True, categories=False)
    await _refresh_setup_status(_Session(), job)
    assert job.assessment_status == PENDING_REVIEW


@pytest.mark.asyncio
async def test_the_categories_alone_do_not_open_the_job() -> None:
    """The other direction, and the more dangerous one: candidates would be
    assessed against a matrix nobody confirmed."""
    job = _job(framework=False, categories=True)
    await _refresh_setup_status(_Session(), job)
    assert job.assessment_status == PENDING_REVIEW


@pytest.mark.asyncio
async def test_the_retired_technical_approval_is_not_a_gate_in_either_direction() -> None:
    """`questions_approved_at` is stamped by nothing and read by nothing.

    It must not block a job and must not open one, or removing one gate would
    have quietly removed another.
    """
    job = _job(framework=False, categories=False, questions=True)
    await _refresh_setup_status(_Session(), job)
    assert job.assessment_status == PENDING_REVIEW


@pytest.mark.asyncio
async def test_the_swot_intake_is_not_a_separate_gate() -> None:
    """Spec §5.1 feeds the intake INTO the matrix rather than gating on it.

    An intake nobody completed already shows up as a matrix nobody approved.
    Gating separately would give one problem two error messages and two places
    to fix it -- and would strand a job whose hiring manager left mid-setup even
    after someone else reviewed and approved the matrix by hand.
    """
    job = _job(framework=True, categories=True, swot=False)
    await _refresh_setup_status(_Session(), job)
    assert job.assessment_status == READY_FOR_CANDIDATES


@pytest.mark.asyncio
async def test_neither_approved_stays_pending() -> None:
    job = _job(framework=False, categories=False)
    await _refresh_setup_status(_Session(), job)
    assert job.assessment_status == PENDING_REVIEW


@pytest.mark.asyncio
async def test_unapproving_the_matrix_closes_an_open_job() -> None:
    """Reopening the matrix must walk the job back.

    `_refresh_setup_status` is called after the approval is cleared, and if it
    only ever moved a job forwards, a job would keep saying
    `ready_for_candidates` while its criteria were being edited.
    """
    job = _job(framework=False, categories=True, status=READY_FOR_CANDIDATES)
    await _refresh_setup_status(_Session(), job)
    assert job.assessment_status == PENDING_REVIEW


@pytest.mark.asyncio
async def test_the_session_is_always_flushed() -> None:
    """Including when the status did not change: the caller may have mutated
    the approval stamps on the same job and relies on this flush."""
    session = _Session()
    job = _job(framework=True, categories=True, status=READY_FOR_CANDIDATES)
    await _refresh_setup_status(session, job)
    assert job.assessment_status == READY_FOR_CANDIDATES
    assert session.flushed == 1
