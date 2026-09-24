"""The first start asks again whether the employer can pay; a running one always finishes.

The invitation checked the balance, but days can pass between an invitation and
the click, and other assessments finish and are charged in between. So the
FIRST start re-checks (`credits.can_start_assessment`) before anything is
locked or stamped, and refuses with a sentence that names no billing term: the
balance is the employer's business, and the candidate is simply told the
assessment cannot start yet. A conversation that has already started is never
stopped over credit (it is charged even into the negative, CLAUDE.md
2026-07-28), and a demonstration tenant is never refused.
"""
from __future__ import annotations

import pytest

from app.api.assessment_conversation import START_CREDIT_DETAIL
from tests import conversation_world as cw
from tests.candidate_session import close_candidate_session, create_candidate_session


@pytest.fixture
async def candidate():
    if not await cw.reachable():
        pytest.skip("no database reachable")
    factory = cw.sessions()
    person = await create_candidate_session(factory)
    try:
        yield person
    finally:
        await close_candidate_session(factory, person)


async def test_an_unpayable_first_start_is_refused_and_writes_nothing(
    candidate, monkeypatch
) -> None:
    cw.quiet_models(monkeypatch)
    factory = cw.sessions()
    world = await cw.seed(factory, candidate_id=candidate.candidate_id, demo=False)
    try:
        with cw.client(candidate) as http:
            refused = http.post(f"{cw.BASE}/conversations/links/{world.link}/start")
        assert refused.status_code == 402, refused.text
        assert refused.json()["detail"] == START_CREDIT_DETAIL
        for word in ("credit", "balance", "billing", "subscription"):
            assert word not in START_CREDIT_DETAIL.lower()

        row = await cw.conversation_row(factory, world)
        assert row.started_at is None
        assert row.turn_seq == 0
        assert row.skill_snapshot_id is None, "the contract was locked for a start that was refused"
        snapshots = await cw.committed(
            factory, "SELECT id FROM job_skill_snapshots WHERE job_id = :j", j=str(world.job)
        )
        assert snapshots == []
    finally:
        await cw.cleanup(factory, world)


async def test_a_demonstration_tenant_is_never_refused(candidate, monkeypatch) -> None:
    cw.quiet_models(monkeypatch)
    factory = cw.sessions()
    world = await cw.seed(factory, candidate_id=candidate.candidate_id, demo=True)
    try:
        with cw.client(candidate) as http:
            started = http.post(f"{cw.BASE}/conversations/links/{world.link}/start")
        assert started.status_code == 200, started.text
        assert (await cw.conversation_row(factory, world)).started_at is not None
    finally:
        await cw.cleanup(factory, world)


async def test_a_running_assessment_is_never_stopped_over_credit(candidate, monkeypatch) -> None:
    """The work is under way and will be charged; refusing the reload would
    strand a candidate half way through."""
    cw.quiet_models(monkeypatch)
    factory = cw.sessions()
    world = await cw.seed(
        factory, candidate_id=candidate.candidate_id, demo=False, started=True
    )
    try:
        with cw.client(candidate) as http:
            resumed = http.post(f"{cw.BASE}/conversations/links/{world.link}/start")
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["turn_seq"] == 1
    finally:
        await cw.cleanup(factory, world)
