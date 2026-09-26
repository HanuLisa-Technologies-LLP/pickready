"""`pickready.reconcile_job_setup`: repair a lost skills draft, never refill an
emptied set.

Audit #8: the previous sweep selected jobs with no ACTIVE competency, so a
hiring manager who removed every generated item had Sutra put them back fifteen
minutes later. The rewrite selects a job only when its SWOT is SAVED and either
no draft was ever asked for and it has ZERO rows of ANY kind, or a draft was
asked for and never reported back.

The real task body runs against Postgres under the `record` dispatch backend,
and the assertions are about THIS test's jobs only: the sweep is platform-wide,
so another suite's leftovers may be examined too.

MUTATION CHECK, recorded: making the sweep's row test ask for ACTIVE rows only
(`p1b_mutate.py reconcile_active_only`, the old query) fails
`test_an_emptied_set_is_never_redrafted_and_nothing_is_dispatched`.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.services import skills
from app.workers import dispatch
from tests import skills_fixtures as fx

MUST = "must_have"


@pytest.fixture
async def world():
    worlds: list[fx.World] = []

    async def _make(**kwargs) -> fx.World:
        w = await fx.seed(**kwargs)
        worlds.append(w)
        return w

    yield _make
    for w in worlds:
        await fx.drop(w)


async def _sweep() -> None:
    from app.workers.tasks import reconcile_job_setup

    await asyncio.to_thread(reconcile_job_setup)


def _drafted_jobs() -> set[str]:
    return {d.args[0] for d in dispatch.recorded() if d.name == skills.DRAFT_TASK}


async def test_an_emptied_set_is_never_redrafted_and_nothing_is_dispatched(world) -> None:
    w = await world(skills=[(MUST, "Kafka", False, "sutra", None, None)])

    await _sweep()

    assert str(w.job) not in _drafted_jobs()
    assert (await fx.committed_job(w))["skills_draft_status"] == "not_started"


async def test_a_never_drafted_job_with_a_saved_swot_is_drafted(world) -> None:
    w = await world()

    await _sweep()

    assert str(w.job) in _drafted_jobs()
    assert (await fx.committed_job(w))["skills_draft_status"] == "drafting"


async def test_a_draft_that_never_reported_back_is_sent_again(world) -> None:
    w = await world(
        draft_status="drafting",
        draft_requested_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )

    await _sweep()

    assert str(w.job) in _drafted_jobs()
    job = await fx.committed_job(w)
    assert job["skills_draft_status"] == "drafting"


async def test_a_draft_still_running_is_left_alone(world) -> None:
    w = await world(
        draft_status="drafting",
        draft_requested_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )

    await _sweep()

    assert str(w.job) not in _drafted_jobs()


async def test_an_unsaved_swot_is_ignored(world) -> None:
    w = await world(swot_saved=False)

    await _sweep()

    assert str(w.job) not in _drafted_jobs()


async def test_a_lost_redraft_over_the_teams_skills_is_finished_as_failed(world) -> None:
    """The sweep cannot confirm a replacement on the team's behalf, so a lost
    re-draft over their own skills becomes "draft again", once, and is not
    selected on every later tick."""
    w = await world(
        skills=[(MUST, "Typed by the team", True, "human", None, None)],
        draft_status="drafting",
        draft_requested_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )

    await _sweep()

    assert str(w.job) not in _drafted_jobs()
    job = await fx.committed_job(w)
    assert job["skills_draft_status"] == "failed"
    assert job["skills_draft_error"] == skills.DRAFT_FAILED_DETAIL
