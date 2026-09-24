"""The server keeps the time: late answers, saved drafts, pauses and the evidence gap.

Appendix B section 3. Every turn has an allocation the SERVER snapshotted when
it opened; paused time is subtracted only from rows the server wrote; the
client's `paused_ms` no longer exists and is refused if sent. An answer that
arrives after the deadline plus the grace is not the candidate's: the server
submits the draft it holds (saved in time), and with no draft the turn is an
EVIDENCE GAP, recorded as such and never re-asked.

Every assertion about state reads a second connection after the request.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import get_settings
from app.services.assessment_conversation import turns
from tests import conversation_world as cw
from tests.candidate_session import close_candidate_session, create_candidate_session

LATE_BODY = "This answer arrived after the time was over and must not be filed."
DRAFT = "The draft the candidate saved while the clock was still running."


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


def _expired_age() -> float:
    settings = get_settings()
    return float(
        settings.assessment_time_prose_seconds + settings.assessment_submit_grace_seconds + 30
    )


def _respond(http, world, body: dict):
    return http.post(f"{cw.BASE}/conversations/{world.conversation}/respond", json=body)


async def test_a_client_reported_pause_is_refused(candidate, monkeypatch) -> None:
    cw.quiet_models(monkeypatch)
    factory = cw.sessions()
    world = await cw.seed(
        factory, candidate_id=candidate.candidate_id, started=True, open_turn=True
    )
    try:
        with cw.client(candidate) as http:
            refused = _respond(
                http, world, {"turn_seq": 1, "answer": cw.TEXT_ANSWER, "paused_ms": 60000}
            )
        assert refused.status_code == 422
        assert await cw.messages(factory, world) == []
        assert (await cw.conversation_row(factory, world)).turn_seq == 1
    finally:
        await cw.cleanup(factory, world)


async def test_a_late_answer_is_replaced_by_the_draft_saved_in_time(
    candidate, monkeypatch
) -> None:
    cw.quiet_models(monkeypatch)
    factory = cw.sessions()
    age = _expired_age()
    world = await cw.seed(
        factory, candidate_id=candidate.candidate_id, started=True, open_turn=True,
        turn_age_seconds=age,
    )
    try:
        await cw.set_conversation(
            factory, world,
            draft_answer_json=json.dumps({"answer": DRAFT, "answer_payload": None}),
            draft_turn_seq=1,
            draft_saved_at=datetime.now(timezone.utc) - timedelta(seconds=age - 60),
        )
        with cw.client(candidate) as http:
            answered = _respond(http, world, {"turn_seq": 1, "answer": LATE_BODY})
        assert answered.status_code == 200, answered.text

        transcript = await cw.messages(factory, world)
        filed = [row.content for row in transcript if row.speaker == "candidate"]
        assert filed == [DRAFT], "a late body was filed instead of the draft saved in time"
        records = await cw.committed(
            factory,
            "SELECT answer_json, time_spent_seconds FROM assessment_answers "
            "WHERE conversation_id = :c",
            c=str(world.conversation),
        )
        assert records[0].answer_json["timed_out"] is True
        assert records[0].time_spent_seconds == get_settings().assessment_time_prose_seconds
        row = await cw.conversation_row(factory, world)
        assert row.next_question_index == 1
        assert row.turn_seq == 2
        assert row.pending_prompt is None, "a timed-out answer was followed up or re-asked"
    finally:
        await cw.cleanup(factory, world)


async def test_a_turn_that_ran_out_with_nothing_is_an_evidence_gap(candidate, monkeypatch) -> None:
    cw.quiet_models(monkeypatch)
    factory = cw.sessions()
    world = await cw.seed(
        factory, candidate_id=candidate.candidate_id, started=True, open_turn=True,
        turn_age_seconds=_expired_age(),
    )
    try:
        with cw.client(candidate) as http:
            answered = _respond(http, world, {"turn_seq": 1, "answer": LATE_BODY})
        assert answered.status_code == 200, answered.text
        assert answered.json()["turn_seq"] == 2

        transcript = await cw.messages(factory, world)
        candidate_rows = [row for row in transcript if row.speaker == "candidate"]
        assert len(candidate_rows) == 1
        gap = candidate_rows[0]
        assert gap.content == turns.TIMED_OUT_TEXT
        assert gap.answer_label == turns.LABEL_TIMED_OUT
        assert gap.evidence_gap is True
        assert gap.question_key == str(world.questions[0])
        evidence = await cw.committed(
            factory,
            "SELECT id FROM evidence_items WHERE link_id = :l",
            l=str(world.link),
        )
        assert evidence == [], "an empty timed-out turn was filed as evidence"
    finally:
        await cw.cleanup(factory, world)


async def test_a_turn_that_expired_while_away_is_submitted_by_the_next_start(
    candidate, monkeypatch
) -> None:
    cw.quiet_models(monkeypatch)
    factory = cw.sessions()
    world = await cw.seed(
        factory, candidate_id=candidate.candidate_id, started=True, open_turn=True,
        turn_age_seconds=_expired_age(),
    )
    try:
        with cw.client(candidate) as http:
            resumed = http.post(f"{cw.BASE}/conversations/links/{world.link}/start")
        assert resumed.status_code == 200, resumed.text
        body = resumed.json()
        assert body["turn_seq"] == 2
        assert body["history"][0]["answer"] == turns.TIMED_OUT_TEXT
        row = await cw.conversation_row(factory, world)
        assert row.next_question_index == 1
    finally:
        await cw.cleanup(factory, world)


async def test_a_draft_belongs_to_one_open_turn(candidate, monkeypatch) -> None:
    cw.quiet_models(monkeypatch)
    factory = cw.sessions()
    world = await cw.seed(
        factory, candidate_id=candidate.candidate_id, started=True, open_turn=True
    )
    try:
        with cw.client(candidate) as http:
            saved = http.put(
                f"{cw.BASE}/conversations/{world.conversation}/draft",
                json={"turn_seq": 1, "answer": DRAFT},
            )
            wrong_turn = http.put(
                f"{cw.BASE}/conversations/{world.conversation}/draft",
                json={"turn_seq": 2, "answer": "not this turn"},
            )
            smuggled = http.put(
                f"{cw.BASE}/conversations/{world.conversation}/draft",
                json={"turn_seq": 1, "answer": DRAFT, "paused_ms": 1000},
            )
        assert saved.status_code == 204, saved.text
        assert wrong_turn.status_code == 409
        assert smuggled.status_code == 422
        row = await cw.conversation_row(factory, world)
        assert row.draft_turn_seq == 1
        assert row.draft_answer_json["answer"] == DRAFT
        assert row.draft_saved_at is not None

        await cw.set_conversation(
            factory, world,
            prompt_shown_at=datetime.now(timezone.utc) - timedelta(seconds=_expired_age()),
        )
        with cw.client(candidate) as http:
            late = http.put(
                f"{cw.BASE}/conversations/{world.conversation}/draft",
                json={"turn_seq": 1, "answer": "written after the time ran out"},
            )
        assert late.status_code == 409
        assert (await cw.conversation_row(factory, world)).draft_answer_json["answer"] == DRAFT
    finally:
        await cw.cleanup(factory, world)


async def test_server_paused_time_is_not_counted_against_the_candidate(
    candidate, monkeypatch
) -> None:
    """A transcription pause of ninety seconds inside a turn aged past its
    allocation: the turn has NOT expired, and the answer is the candidate's."""
    cw.quiet_models(monkeypatch)
    factory = cw.sessions()
    allocation = get_settings().assessment_time_prose_seconds
    world = await cw.seed(
        factory, candidate_id=candidate.candidate_id, started=True, open_turn=True,
        turn_age_seconds=allocation + 30,
    )
    try:
        await cw.add_pause(
            factory, world, reason="transcription", started_ago=allocation, ended_ago=allocation - 90
        )
        with cw.client(candidate) as http:
            answered = _respond(http, world, {"turn_seq": 1, "answer": cw.TEXT_ANSWER})
        assert answered.status_code == 200, answered.text
        filed = [
            row.content for row in await cw.messages(factory, world) if row.speaker == "candidate"
        ]
        assert filed == [cw.TEXT_ANSWER]
        records = await cw.committed(
            factory,
            "SELECT answer_json, time_spent_seconds FROM assessment_answers "
            "WHERE conversation_id = :c",
            c=str(world.conversation),
        )
        assert "timed_out" not in records[0].answer_json
        # Elapsed allocation + 30, of which 90 were paused.
        assert allocation - 62 <= records[0].time_spent_seconds <= allocation - 58
    finally:
        await cw.cleanup(factory, world)


async def test_a_device_pause_refuses_answers_until_it_ends(candidate, monkeypatch) -> None:
    cw.quiet_models(monkeypatch)
    factory = cw.sessions()
    world = await cw.seed(
        factory, candidate_id=candidate.candidate_id, started=True, open_turn=True,
        turn_age_seconds=20,
    )
    try:
        await cw.add_pause(factory, world, reason="device_loss", started_ago=5, expires_in=115)
        with cw.client(candidate) as http:
            refused = _respond(http, world, {"turn_seq": 1, "answer": cw.TEXT_ANSWER})
            drafted = http.put(
                f"{cw.BASE}/conversations/{world.conversation}/draft",
                json={"turn_seq": 1, "answer": DRAFT},
            )
            shown = http.post(f"{cw.BASE}/conversations/links/{world.link}/start")
        assert refused.status_code == 409
        assert refused.json()["detail"] == turns.PAUSED_DETAIL
        assert drafted.status_code == 409
        assert shown.status_code == 200, shown.text
        assert shown.json()["status"] == "paused"
        assert shown.json()["turn"]["paused"] is True
        assert shown.json()["turn"]["pause_reason"] == "device_loss"
        assert await cw.messages(factory, world) == []
    finally:
        await cw.cleanup(factory, world)
