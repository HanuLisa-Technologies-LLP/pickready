"""Every format, end to end, against the database.

    "New question types must flow through every existing stage -- generation,
     rendering, answer capture, submission, evaluation, Executive Profile
     output, recruiter Q&A view -- not just the parts that are convenient."
    (spec section 0.3)

So this file drives the real endpoints against the real schema: the question
is delivered as the candidate sees it, the answer is submitted in its own
shape, the server scores and renders and records it, and the recruiter's Q&A
view reads it back. Each of those steps has already been unit-tested in
isolation; what is asserted here is that they are actually wired to each
other, which is the failure this repository keeps having to repair.

Three properties get most of the attention:

  * THE ANSWER KEY NEVER CROSSES THE BOUNDARY. Asserted on the payload the
    endpoint actually returns, not on `candidate_view` in isolation.
  * THE SERVER MEASURES THE TIME. `time_spent_seconds` comes from
    `prompt_shown_at`, so a client that reports a pause longer than the
    question was on screen cannot drive it below zero.
  * PROCTORING IS MANDATORY. Both routes refuse without a consented session,
    and a terminated conversation takes no further answer.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text

from app.services.assessment_formats import types


async def _factory_or_skip():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001
        await engine.dispose()
        pytest.skip("no database reachable")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


RESUME = (
    "Senior Engineer, Northwind Payments. Led the checkout migration from the "
    "session table to edge-verified tokens, cutting read amplification. "
    "Built the reconciliation service that settles card captures nightly."
)

ANCHOR = "Led the checkout migration from the session table to edge-verified tokens"

MCQ_SINGLE_PAYLOAD = {
    "options": [
        {"id": "a", "text": "A write-ahead log"},
        {"id": "b", "text": "A read replica"},
        {"id": "c", "text": "A connection pool"},
        {"id": "d", "text": "A materialised view"},
    ],
    "correct_option_id": "a",
}

MCQ_MULTI_PAYLOAD = {
    "options": [
        {"id": "a", "text": "Partition the topic"},
        {"id": "b", "text": "Add consumers"},
        {"id": "c", "text": "Raise the batch size"},
        {"id": "d", "text": "Disable acknowledgements"},
    ],
    "correct_option_ids": ["a", "b"],
    "scoring": "partial",
}

FILL_BLANK_PAYLOAD = {
    "template": "A ___ index speeds up a lookup on ___.",
    "blanks": [
        {"index": 0, "accepted": ["covering"], "case_sensitive": False},
        {"index": 1, "accepted": ["the orders table"], "case_sensitive": False},
    ],
}

CODING_PAYLOAD = {
    "language": "python",
    "starter_code": "def solve(items):",
    "constraints": "No external libraries.",
    "expected_approach": "Track seen elements in a set and preserve order.",
    "language_options": ["python", "javascript"],
}

#: (question_type, payload, resume_anchor, category)
FORMATS: list[tuple[str, dict, str | None, str]] = [
    (types.EVIDENCE_BASED, {"sub_type": "project_deep_dive", "anchor_source": "employment_history[0]"}, ANCHOR, "must_have"),
    (types.MCQ_SINGLE, MCQ_SINGLE_PAYLOAD, None, "must_have"),
    (types.MCQ_MULTI, MCQ_MULTI_PAYLOAD, None, "must_have"),
    (types.FILL_BLANK, FILL_BLANK_PAYLOAD, None, "nice_to_have"),
    (types.CODING, CODING_PAYLOAD, None, "nice_to_have"),
    (types.SHORT_ANSWER, {}, None, "behavioural"),
]

TEXT_ANSWER = (
    "I owned the checkout migration end to end, moved reads to the token first "
    "and backfilled behind it, and rolled back once when the window slipped."
)

CODE_ANSWER = "def solve(items):\n    seen = set()\n    return [x for x in items if x not in seen and not seen.add(x)]"


class _Fx:
    def __init__(self) -> None:
        self.tenant_id = uuid.uuid4()
        self.job_id = uuid.uuid4()
        self.cand_id = uuid.uuid4()
        self.user_id = uuid.uuid4()
        self.profile_id = uuid.uuid4()
        self.link_id = uuid.uuid4()
        self.conv_id = uuid.uuid4()
        self.proctoring_id = uuid.uuid4()
        self.q_ids: list[uuid.UUID] = []


async def _seed(factory, fx: _Fx, *, with_proctoring: bool = True) -> None:
    from app.core.db import superadmin_scope
    from app.models import Candidate, Job, JobStatus, LinkSource, Tenant
    from app.models.assessment import (
        AssessmentConversation,
        CandidateQuestion,
        JobCompetency,
    )
    from app.models.candidate import JobCandidateLink, Profile
    from app.models.proctoring import OUTCOME_ACTIVE, ProctoringSession

    now = datetime.now(timezone.utc)
    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                s.add(Tenant(id=fx.tenant_id, name=f"Fmt {fx.tenant_id.hex[:6]}",
                             domain=f"{fx.tenant_id}.fmt.test"))
                await s.flush()
                s.add(Job(id=fx.job_id, tenant_id=fx.tenant_id, title="Backend Engineer",
                          jd_json={}, jd_markdown="Build payment services.",
                          status=JobStatus.ratified, ratified_at=now,
                          assessment_status="ready_for_candidates",
                          assessment_grade="non_managerial",
                          role_classification="STEM"))
                s.add(Candidate(id=fx.cand_id, email=f"c{fx.cand_id.hex[:8]}@t.test",
                                full_name="Format Candidate", consent_databank=False))
                await s.flush()
                s.add(Profile(id=fx.profile_id, candidate_id=fx.cand_id, resume_text=RESUME))
                await s.flush()
                s.add(JobCandidateLink(id=fx.link_id, tenant_id=fx.tenant_id,
                                       job_id=fx.job_id, candidate_id=fx.cand_id,
                                       profile_id=fx.profile_id,
                                       source=LinkSource.fresh, status="applied"))
                await s.flush()
                competency_ids = {}
                for index, category in enumerate(("must_have", "nice_to_have", "behavioural"), 1):
                    competency_id = uuid.uuid4()
                    competency_ids[category] = competency_id
                    s.add(JobCompetency(
                        id=competency_id, tenant_id=fx.tenant_id, job_id=fx.job_id,
                        category=category, name=f"Item {index}",
                        description=f"What item {index} measures.",
                        required_level=82, ordinal=index,
                    ))
                await s.flush()
                for ordinal, (question_type, payload, anchor, category) in enumerate(FORMATS):
                    question_id = uuid.uuid4()
                    fx.q_ids.append(question_id)
                    s.add(CandidateQuestion(
                        id=question_id, tenant_id=fx.tenant_id, job_id=fx.job_id,
                        job_candidate_link_id=fx.link_id,
                        competency_id=competency_ids[category],
                        prompt=f"Question {ordinal} about the work.",
                        ordinal=ordinal, rubric_json={},
                        question_type=question_type, payload_json=payload,
                        resume_anchor=anchor, time_allocation_seconds=180, weight=1.0,
                    ))
                await s.flush()
                s.add(AssessmentConversation(
                    id=fx.conv_id, tenant_id=fx.tenant_id, job_id=fx.job_id,
                    job_candidate_link_id=fx.link_id, grade="non_managerial",
                    status="active", next_question_index=0, started_at=now,
                    invitation_sent_at=now,
                ))
                await s.flush()
                if with_proctoring:
                    s.add(ProctoringSession(
                        id=fx.proctoring_id, tenant_id=fx.tenant_id,
                        conversation_id=fx.conv_id, job_candidate_link_id=fx.link_id,
                        candidate_id=fx.cand_id, job_id=fx.job_id,
                        consented_at=now, started_at=now, outcome=OUTCOME_ACTIVE,
                    ))


async def _cleanup(factory, fx: _Fx) -> None:
    from app.core.db import superadmin_scope

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                await s.execute(text("DELETE FROM credit_ledger WHERE tenant_id = :t"),
                                {"t": str(fx.tenant_id)})
                await s.execute(text("DELETE FROM tenants WHERE id = :t"),
                                {"t": str(fx.tenant_id)})
                await s.execute(text("DELETE FROM candidates WHERE id = :c"),
                                {"c": str(fx.cand_id)})


def _candidate(fx: _Fx):
    from app.api.deps import CurrentUser
    from app.core.security import AUDIENCE_CANDIDATE
    from app.models import Role

    return CurrentUser(user_id=fx.user_id, tenant_id=None, role=Role.candidate,
                       audience=AUDIENCE_CANDIDATE)


def _staff(fx: _Fx):
    from app.api.deps import CurrentUser
    from app.core.security import AUDIENCE_ORG
    from app.models import Role

    return CurrentUser(user_id=fx.user_id, tenant_id=fx.tenant_id, role=Role.recruiter,
                       audience=AUDIENCE_ORG)


@pytest.fixture
def wired(monkeypatch):
    """Everything the conversation reaches that is not the subject here."""
    from app.api import assessments as mod
    from app.services import agent_loop, answer_classification, ppi_interview
    from app.services.assessment_formats import scoring as format_scoring

    dispatched: list[str] = []
    monkeypatch.setattr(mod, "dispatch",
                        lambda name, *a, **k: dispatched.append(name))

    async def _substantive(**kwargs):
        return answer_classification.Classification(
            label="substantive", confidence="high", reason="stubbed for a flow test",
            needs_rechallenge=False, scorable=True,
        )

    monkeypatch.setattr(answer_classification, "classify", _substantive)

    async def _written(*, row, **kwargs):
        return agent_loop.LoopResult(
            value={"question": row.prompt, "rubric": dict(row.rubric_json or {})},
            degraded=False, attempts=1,
        )

    monkeypatch.setattr(ppi_interview, "write_question", _written)

    async def _no_follow_up(**kwargs):
        return None

    monkeypatch.setattr(mod.interviewer, "next_follow_up", _no_follow_up)

    # A wrong blank must not reach a provider from a test.
    async def _not_equivalent(session, **kwargs):
        return False

    monkeypatch.setattr(format_scoring, "semantically_equivalent", _not_equivalent)
    return dispatched


def _patch_link(monkeypatch, fx: _Fx) -> None:
    from app.api import assessments as mod
    from app.models import Job
    from app.models.candidate import JobCandidateLink

    async def _resolve(session, user, link_id):
        return (
            await session.get(JobCandidateLink, fx.link_id),
            await session.get(Job, fx.job_id),
        )

    monkeypatch.setattr(mod, "_candidate_link", _resolve)


async def _start(mod, fx, s):
    return await mod.start_conversation(fx.link_id, user=_candidate(fx), session=s)


async def _respond(mod, fx, s, *, answer="", payload=None, paused_ms=0, behaviour=None):
    from app.schemas.assessments import ConversationMessageIn

    return await mod.respond(
        fx.conv_id,
        ConversationMessageIn(
            answer=answer, answer_payload=payload, paused_ms=paused_ms, behaviour=behaviour
        ),
        user=_candidate(fx),
        session=s,
    )


#: The answer for each seeded format, in order.
ANSWERS: list[tuple[str, dict | None]] = [
    (TEXT_ANSWER, None),
    ("", {"selected_option_id": "a"}),
    ("", {"selected_option_ids": ["a"]}),
    ("", {"values": ["covering", "the wrong table"]}),
    ("", {"language": "python", "code": CODE_ANSWER}),
    (TEXT_ANSWER, None),
]


@pytest.mark.asyncio
async def test_every_format_is_delivered_answered_scored_and_recorded(monkeypatch, wired) -> None:
    """One assessment, six formats, end to end."""
    from app.api import assessments as mod
    from app.core.db import superadmin_scope
    from app.models.assessment import AssessmentAnswer, AssessmentConversation

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        _patch_link(monkeypatch, fx)

        delivered: list = []
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    opened = await _start(mod, fx, s)
                    delivered.append(opened.question)
                    for answer, payload in ANSWERS:
                        out = await _respond(mod, fx, s, answer=answer, payload=payload)
                        delivered.append(out.question)

        # DELIVERY: every base question arrived as a QuestionOut of its own
        # format, and the last response has none because the conversation is
        # over.
        assert [q.question_type for q in delivered[:-1]] == [row[0] for row in FORMATS]
        assert delivered[-1] is None
        assert out.status == "completed"

        # THE ANSWER KEY NEVER CROSSED THE BOUNDARY.
        for question in delivered[:-1]:
            blob = repr(question.payload)
            for secret in ("correct_option_id", "accepted", "expected_approach"):
                assert secret not in blob, (question.question_type, secret)
        single = delivered[1]
        assert {option["id"] for option in single.payload["options"]} == {"a", "b", "c", "d"}
        coding = delivered[4]
        assert coding.payload["starter_code"] == "def solve(items):"
        assert coding.payload["language_options"] == ["python", "javascript"]

        async with factory() as s:
            async with superadmin_scope(s):
                records = {
                    str(row.question_id): row
                    for row in (
                        await s.execute(
                            select(AssessmentAnswer).where(
                                AssessmentAnswer.conversation_id == fx.conv_id
                            )
                        )
                    ).scalars().all()
                }
                conversation = await s.get(AssessmentConversation, fx.conv_id)

        # CAPTURE: one row per question, typed, with the submission in it.
        assert len(records) == len(FORMATS)
        assert [records[str(qid)].question_type for qid in fx.q_ids] == [row[0] for row in FORMATS]

        # SCORING: the objective formats carry a deterministic score and the
        # subjective ones do not.
        assert records[str(fx.q_ids[1])].auto_score == 1.0                  # correct MCQ
        assert records[str(fx.q_ids[2])].auto_score == 0.5                  # one of two correct
        assert records[str(fx.q_ids[3])].auto_score == 0.5                  # one blank right
        assert records[str(fx.q_ids[3])].ai_evaluation_json["blank_results"] == [
            "exact", "incorrect",
        ]
        for text_question in (fx.q_ids[0], fx.q_ids[4], fx.q_ids[5]):
            assert records[str(text_question)].auto_score is None

        # The submission is stored in its own shape.
        assert records[str(fx.q_ids[1])].answer_json == {"selected_option_id": "a"}
        assert records[str(fx.q_ids[4])].answer_json["code"] == CODE_ANSWER
        assert records[str(fx.q_ids[0])].answer_json == {"text": TEXT_ANSWER}

        # TIMING is the server's, and is never negative.
        for record in records.values():
            assert record.time_spent_seconds is not None
            assert record.time_spent_seconds >= 0
            assert record.revision_count == 0
            assert record.submitted_at is not None

        assert conversation.next_question_index == len(FORMATS)
        assert conversation.status == "completed"
        assert "pickready.run_functional_assessment" in wired
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_structured_answer_becomes_a_readable_transcript_line(monkeypatch, wired) -> None:
    """The transcript is what every scorer and the recruiter read, and an
    option id is not something a person can read."""
    from app.api import assessments as mod
    from app.core.db import superadmin_scope
    from app.models.assessment import AssessmentMessage

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        _patch_link(monkeypatch, fx)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    await _start(mod, fx, s)
                    for answer, payload in ANSWERS[:5]:
                        await _respond(mod, fx, s, answer=answer, payload=payload)

        async with factory() as s:
            async with superadmin_scope(s):
                rows = (
                    await s.execute(
                        select(AssessmentMessage.question_key, AssessmentMessage.content)
                        .where(
                            AssessmentMessage.conversation_id == fx.conv_id,
                            AssessmentMessage.speaker == "candidate",
                        )
                        .order_by(AssessmentMessage.ordinal)
                    )
                ).all()
        lines = {key: content for key, content in rows}
        assert lines[str(fx.q_ids[1])] == "Selected: A write-ahead log"
        assert lines[str(fx.q_ids[2])] == "Selected: Partition the topic"
        assert lines[str(fx.q_ids[3])] == "A [covering] index speeds up a lookup on [the wrong table]."
        assert CODE_ANSWER in lines[str(fx.q_ids[4])]
        # Every structured answer is filed under its own question's key, so
        # `answers_by_key` hands the scorer the same shape it always had.
        assert set(lines) == {str(qid) for qid in fx.q_ids[:5]}
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_server_measures_the_time_and_a_long_pause_cannot_go_negative(
    monkeypatch, wired
) -> None:
    """`time_spent_seconds` is measured from `prompt_shown_at`, less a pause
    BOUNDED by the elapsed time. A client-reported duration would be a number
    the client chose."""
    from app.api import assessments as mod
    from app.core.db import superadmin_scope
    from app.models.assessment import AssessmentAnswer, AssessmentConversation

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        _patch_link(monkeypatch, fx)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    await _start(mod, fx, s)
                    conversation = await s.get(AssessmentConversation, fx.conv_id)
                    assert conversation.prompt_shown_at is not None
                    # Wind the clock back: the question has been on screen for
                    # two minutes.
                    conversation.prompt_shown_at = datetime.now(timezone.utc) - timedelta(minutes=2)
                    await s.flush()
                    await _respond(mod, fx, s, answer=TEXT_ANSWER)
                    # And now a client claims an implausible pause.
                    conversation = await s.get(AssessmentConversation, fx.conv_id)
                    conversation.prompt_shown_at = datetime.now(timezone.utc) - timedelta(seconds=30)
                    await s.flush()
                    # Ten minutes of "pause" against thirty seconds on screen.
                    await _respond(mod, fx, s, payload={"selected_option_id": "a"},
                                   paused_ms=600_000)

        async with factory() as s:
            async with superadmin_scope(s):
                records = {
                    str(row.question_id): row
                    for row in (
                        await s.execute(
                            select(AssessmentAnswer).where(
                                AssessmentAnswer.conversation_id == fx.conv_id
                            )
                        )
                    ).scalars().all()
                }
        assert 110 <= records[str(fx.q_ids[0])].time_spent_seconds <= 130
        # The pause is clamped to the time the question was actually shown.
        assert records[str(fx.q_ids[1])].time_spent_seconds == 0
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_structured_question_refuses_prose_and_an_option_it_never_offered(
    monkeypatch, wired
) -> None:
    """A selected option the question does not carry is a defect in the client,
    not a wrong answer, so it is refused rather than scored zero."""
    from app.api import assessments as mod
    from app.core.db import superadmin_scope

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        _patch_link(monkeypatch, fx)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    await _start(mod, fx, s)
                    await _respond(mod, fx, s, answer=TEXT_ANSWER)   # the evidence question
                    with pytest.raises(HTTPException) as prose:
                        await _respond(mod, fx, s, answer="I would pick the first one")
                    with pytest.raises(HTTPException) as stray:
                        await _respond(mod, fx, s, payload={"selected_option_id": "z"})
        assert prose.value.status_code == 422
        assert stray.value.status_code == 422
        assert "not one of this question's options" in str(stray.value.detail)
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_follow_up_revises_the_base_answer_rather_than_adding_a_row(
    monkeypatch, wired
) -> None:
    """One row per (conversation, question), by the unique constraint. A
    follow-up is more evidence for a question already counted, and the
    transcript is where it lives."""
    from app.api import assessments as mod
    from app.core.db import superadmin_scope
    from app.models.assessment import AssessmentAnswer

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        _patch_link(monkeypatch, fx)

        probes = ["What broke when the window slipped?"]

        async def _probe(**kwargs):
            return probes.pop() if probes else None

        monkeypatch.setattr(mod.interviewer, "next_follow_up", _probe)

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    await _start(mod, fx, s)
                    await _respond(mod, fx, s, answer=TEXT_ANSWER)      # base
                    await _respond(mod, fx, s, answer="The backfill lagged by an hour.")

        async with factory() as s:
            async with superadmin_scope(s):
                rows = (
                    await s.execute(
                        select(AssessmentAnswer).where(
                            AssessmentAnswer.conversation_id == fx.conv_id
                        )
                    )
                ).scalars().all()
        assert len(rows) == 1
        record = rows[0]
        assert record.question_id == fx.q_ids[0]
        # The type comes from the question, not from the turn that wrote it.
        assert record.question_type == types.EVIDENCE_BASED
        assert record.revision_count == 1
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_editing_the_latest_answer_counts_as_a_revision(monkeypatch, wired) -> None:
    from app.api import assessments as mod
    from app.core.db import superadmin_scope
    from app.models.assessment import AssessmentAnswer
    from app.schemas.assessments import ConversationAnswerEditIn

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        _patch_link(monkeypatch, fx)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    await _start(mod, fx, s)
                    out = await _respond(mod, fx, s, answer=TEXT_ANSWER)
                    await mod.edit_latest_answer(
                        fx.conv_id, out.answer_message_id,
                        ConversationAnswerEditIn(answer=TEXT_ANSWER + " I also wrote the runbook."),
                        user=_candidate(fx), session=s,
                    )

        async with factory() as s:
            async with superadmin_scope(s):
                record = (
                    await s.execute(
                        select(AssessmentAnswer).where(
                            AssessmentAnswer.conversation_id == fx.conv_id
                        )
                    )
                ).scalars().one()
        assert record.revision_count == 1
        assert "runbook" in record.answer_json["text"]
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


# ── Proctoring is mandatory (proctoring spec, principle P4) ──────────────────


@pytest.mark.asyncio
async def test_neither_route_runs_without_a_consented_proctoring_session(
    monkeypatch, wired
) -> None:
    from app.api import assessments as mod
    from app.core.db import superadmin_scope

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx, with_proctoring=False)
        _patch_link(monkeypatch, fx)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    with pytest.raises(HTTPException) as opening:
                        await _start(mod, fx, s)
                    with pytest.raises(HTTPException) as answering:
                        await _respond(mod, fx, s, answer=TEXT_ANSWER)
        for raised in (opening, answering):
            assert raised.value.status_code == 409
            assert "monitored" in str(raised.value.detail)
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_terminated_conversation_takes_no_further_answer(monkeypatch, wired) -> None:
    """The candidate's answers up to that point were saved; what is refused is
    the next one."""
    from app.api import assessments as mod
    from app.core.db import superadmin_scope
    from app.models.assessment import AssessmentConversation

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        _patch_link(monkeypatch, fx)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    await _start(mod, fx, s)
                    await _respond(mod, fx, s, answer=TEXT_ANSWER)
                    conversation = await s.get(AssessmentConversation, fx.conv_id)
                    conversation.status = "terminated"
                    await s.flush()
                    with pytest.raises(HTTPException) as raised:
                        await _respond(mod, fx, s, payload={"selected_option_id": "a"})
        assert raised.value.status_code == 409
        assert "ended" in str(raised.value.detail)
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_answer_behaviour_reaches_proctoring_and_moves_no_score(
    monkeypatch, wired
) -> None:
    """The timings go to the proctoring tables and nowhere near a grade."""
    from app.api import assessments as mod
    from app.core.db import superadmin_scope
    from app.models.assessment import AssessmentAnswer
    from app.models.proctoring import ProctoringEvent
    from app.schemas.assessments import AnswerBehaviourIn

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        _patch_link(monkeypatch, fx)
        behaviour = AnswerBehaviourIn(
            keydown_offsets_ms=list(range(0, 4000, 20)),
            backspace_offsets_ms=[],
            focus_ms=4000,
            blocked_action_count=1,
        )
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    await _start(mod, fx, s)
                    await _respond(mod, fx, s, answer=TEXT_ANSWER, behaviour=behaviour)

        async with factory() as s:
            async with superadmin_scope(s):
                record = (
                    await s.execute(
                        select(AssessmentAnswer).where(
                            AssessmentAnswer.conversation_id == fx.conv_id
                        )
                    )
                ).scalars().one()
                events = (
                    await s.execute(
                        select(ProctoringEvent).where(
                            ProctoringEvent.proctoring_session_id == fx.proctoring_id
                        )
                    )
                ).scalars().all()
        # The answer is recorded exactly as it would be without any behaviour.
        assert record.answer_json == {"text": TEXT_ANSWER}
        assert record.auto_score is None
        # Whatever proctoring made of the timings, it never carries the text.
        for event in events:
            assert TEXT_ANSWER not in repr(event.metadata_json)
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


# ── The recruiter's Q&A view (spec section 7) ────────────────────────────────


@pytest.mark.asyncio
async def test_the_transcript_renders_every_format_for_a_recruiter(monkeypatch, wired) -> None:
    """"Per question, show ... which option they chose, which was correct,
    marked clearly ... their input against accepted answers ... the resume
    anchor that prompted the question ... time spent"."""
    from app.api import assessments as mod
    from app.core.db import superadmin_scope
    from app.models.assessment import AssessmentAnswer
    from app.services.siddhi import numbers

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        _patch_link(monkeypatch, fx)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    await _start(mod, fx, s)
                    for answer, payload in ANSWERS:
                        await _respond(mod, fx, s, answer=answer, payload=payload)
                    # The coding evaluation is written by the scoring task; put
                    # one on the row so the view has something to render.
                    record = (
                        await s.execute(
                            select(AssessmentAnswer).where(
                                AssessmentAnswer.question_id == fx.q_ids[4]
                            )
                        )
                    ).scalars().one()
                    record.ai_evaluation_json = {
                        "reasoning": "The set-based scan appears to preserve order.",
                        "citations": ["seen = set()"],
                        "not_executed_note": "This code was read and judged, not executed.",
                    }
                    await s.flush()

        async with factory() as s:
            async with superadmin_scope(s):
                out = await mod.get_transcript(fx.link_id, user=_staff(fx), session=s)

        by_type = {
            exchange.question_type: exchange
            for exchange in out.exchanges
            if exchange.question_type
        }
        assert set(by_type) == {row[0] for row in FORMATS}

        # EVIDENCE: the anchor is what was being probed, and it is shown.
        evidence = by_type[types.EVIDENCE_BASED]
        assert evidence.resume_anchor == ANCHOR

        # MCQ: the choice beside the key, and correctness as a WORD.
        single = by_type[types.MCQ_SINGLE]
        assert single.detail.answer == {"selected_option_id": "a"}
        assert single.detail.answer_key == {"correct_option_id": "a"}
        assert single.detail.correctness == "correct"
        multi = by_type[types.MCQ_MULTI]
        assert multi.detail.correctness == "partially_correct"
        assert multi.detail.answer_key["correct_option_ids"] == ["a", "b"]

        # FILL-BLANK: their input against the accepted answers, per blank.
        blank = by_type[types.FILL_BLANK]
        assert blank.detail.answer == {"values": ["covering", "the wrong table"]}
        assert blank.detail.answer_key["accepted"] == [["covering"], ["the orders table"]]
        assert blank.detail.blank_results == ["exact", "incorrect"]

        # CODING: the code, the reasoning, and the note that it was not run.
        coding = by_type[types.CODING]
        assert coding.detail.answer["code"] == CODE_ANSWER
        assert coding.detail.evaluation_reasoning
        assert "not executed" in coding.detail.not_executed_note.casefold()
        assert coding.detail.correctness is None, "a coding answer has no auto-correctness"

        # TIME SPENT is a phrase, and no number crosses the boundary anywhere
        # in this payload.
        assert single.detail.time_spent
        assert not any(character.isdigit() for character in single.detail.time_spent)
        for exchange in out.exchanges:
            if exchange.detail is not None:
                assert numbers.scan(exchange.detail.model_dump(exclude={"payload", "answer", "answer_key"})) == []
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()
