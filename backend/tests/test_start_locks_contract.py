"""The first start locks the contract; questions from another contract are rewritten first.

Appendix A: Vaada "receives JD + skills + hidden assessment context at
conversation start", and Miti grades against the SAME context. So the start is
where the contract is frozen for this candidate (`assessment_contract.
lock_contract`, in the transaction that stamps `started_at`), and where the
questions are checked against it: a question set written against a different
contract (or before the digest existed, a NULL) is thrown away and written
again BEFORE the first answer, never answered and then graded against a
contract it was not written for.

Every assertion about state reads a second connection after the request.
"""
from __future__ import annotations

import pytest

from app.services import assessment_contract
from app.workers import dispatch as dispatch_mod
from tests import conversation_world as cw
from tests.candidate_session import close_candidate_session, create_candidate_session

GENERATE = "pickready.generate_candidate_questions"


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


async def _world(candidate, **kwargs):
    return await cw.seed(cw.sessions(), candidate_id=candidate.candidate_id, **kwargs)


def _spy_digests(monkeypatch) -> list[tuple[str, str, str]]:
    """Every digest line the request writes, as (stage, conversation, digest).

    The app's lifespan reconfigures logging when the test client starts it, so
    the call is observed at the one function that writes the line; its FORMAT
    is pinned by `tests/test_assessment_contract.py`."""
    calls: list[tuple[str, str, str]] = []
    real = assessment_contract.log_digest

    def _record(stage, conversation_id, contract):
        calls.append((stage, str(conversation_id), contract.digest))
        real(stage, conversation_id, contract)

    monkeypatch.setattr(assessment_contract, "log_digest", _record)
    return calls


async def test_the_first_start_locks_the_contract_and_logs_the_vaada_digest(
    candidate, monkeypatch
) -> None:
    cw.quiet_models(monkeypatch)
    digests = _spy_digests(monkeypatch)
    factory = cw.sessions()
    world = await _world(candidate)
    try:
        with cw.client(candidate) as http:
            started = http.post(f"{cw.BASE}/conversations/links/{world.link}/start")
        assert started.status_code == 200, started.text
        body = started.json()
        assert body["status"] == "active"
        assert body["turn_seq"] == 1
        assert body["turn"]["kind"] == "base"

        row = await cw.conversation_row(factory, world)
        assert row.started_at is not None
        assert row.skill_snapshot_id is not None, "the start did not bind a contract"
        snapshots = await cw.committed(
            factory,
            "SELECT id, digest FROM job_skill_snapshots WHERE job_id = :j",
            j=str(world.job),
        )
        assert len(snapshots) == 1
        assert row.contract_digest == snapshots[0].digest
        assert row.questions_contract_digest == snapshots[0].digest, (
            "the questions this candidate is asked were not written against the "
            "contract the start locked"
        )
        assert digests == [
            (assessment_contract.STAGE_VAADA, str(world.conversation), snapshots[0].digest)
        ], "Vaada's half of the digest pair was not logged once, over the locked contract"
        links = await cw.committed(
            factory, "SELECT status FROM job_candidate_links WHERE id = :l", l=str(world.link)
        )
        assert links[0].status == "assessment_in_progress"
    finally:
        await cw.cleanup(factory, world)


async def test_a_second_start_neither_relocks_nor_restamps(candidate, monkeypatch) -> None:
    """A reload is the same session: the same contract, the same start time,
    the same turn and the same clock."""
    cw.quiet_models(monkeypatch)
    factory = cw.sessions()
    world = await _world(candidate)
    try:
        with cw.client(candidate) as http:
            first = http.post(f"{cw.BASE}/conversations/links/{world.link}/start")
            assert first.status_code == 200, first.text
            before = await cw.conversation_row(factory, world)
            digests = _spy_digests(monkeypatch)
            second = http.post(f"{cw.BASE}/conversations/links/{world.link}/start")
        assert second.status_code == 200, second.text
        after = await cw.conversation_row(factory, world)
        assert after.started_at == before.started_at
        assert after.skill_snapshot_id == before.skill_snapshot_id
        assert after.turn_seq == before.turn_seq == 1
        assert after.prompt_shown_at == before.prompt_shown_at, (
            "a reload re-stamped the question clock and handed the candidate "
            "a fresh allocation"
        )
        assert second.json()["turn"]["deadline_at"] == first.json()["turn"]["deadline_at"]
        assert digests == [], "a reload logged a second Vaada digest"
        snapshots = await cw.committed(
            factory, "SELECT id FROM job_skill_snapshots WHERE job_id = :j", j=str(world.job)
        )
        assert len(snapshots) == 1
    finally:
        await cw.cleanup(factory, world)


@pytest.mark.parametrize("digest", ["null", "stale"])
async def test_questions_from_another_contract_are_rewritten_before_the_first_answer(
    candidate, monkeypatch, digest
) -> None:
    """NULL counts as a mismatch: it is how a question set written before the
    digest existed (an apply-time generation) reads."""
    cw.quiet_models(monkeypatch)
    factory = cw.sessions()
    world = await _world(candidate, digest=digest)
    try:
        with cw.client(candidate) as http:
            started = http.post(f"{cw.BASE}/conversations/links/{world.link}/start")
        assert started.status_code == 200, started.text
        assert started.json()["status"] == "preparing"
        assert started.json()["turn_seq"] == 0

        questions = await cw.committed(
            factory,
            "SELECT id FROM candidate_questions WHERE job_candidate_link_id = :l",
            l=str(world.link),
        )
        assert questions == [], "the questions written against another contract survived"
        row = await cw.conversation_row(factory, world)
        assert row.started_at is None, "a start that has to rewrite the questions began anyway"
        assert row.skill_snapshot_id is None, (
            "the contract was locked by a start that did not begin"
        )
        assert row.questions_contract_digest is None
        assert row.questions_requested_at is not None
        snapshots = await cw.committed(
            factory, "SELECT id FROM job_skill_snapshots WHERE job_id = :j", j=str(world.job)
        )
        assert snapshots == []
        assert dispatch_mod.recorded_names().count(GENERATE) == 1, (
            "the rewrite was not requested, or was requested before the commit "
            "that deleted the old questions"
        )
    finally:
        await cw.cleanup(factory, world)


async def test_missing_questions_are_requested_once_per_window(candidate, monkeypatch) -> None:
    """Generation is its own Fargate task. A start polled while it runs must
    not start another each time."""
    cw.quiet_models(monkeypatch)
    factory = cw.sessions()
    world = await _world(candidate, plan=())
    try:
        with cw.client(candidate) as http:
            first = http.post(f"{cw.BASE}/conversations/links/{world.link}/start")
            second = http.post(f"{cw.BASE}/conversations/links/{world.link}/start")
        assert first.status_code == second.status_code == 200, first.text
        assert first.json()["status"] == second.json()["status"] == "preparing"
        assert dispatch_mod.recorded_names().count(GENERATE) == 1
    finally:
        await cw.cleanup(factory, world)
