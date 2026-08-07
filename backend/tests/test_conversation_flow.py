"""`respond` end to end, with a follow-up in the middle of it.

There was NO test anywhere touching `respond` or `AssessmentConversation` before
this file, which is why adaptive follow-ups needed one: the endpoint decides when
a customer is CHARGED and which key every answer is filed under, and both of
those now sit next to a branch that did not exist before.

What is asserted here is not "the follow-up works". It is that the three things
around it did not move:

  * a follow-up's answer is filed under the SAME question_key, so
    `answers_by_key` hands the scorer one richer answer and never an unknown key;
  * a follow-up does NOT advance `next_question_index`, so the customer is
    charged after exactly the same set of base questions as before;
  * a follow-up outstanding on the LAST base question holds completion open, so
    billing and scoring do not fire while the candidate is still typing.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select, text


@pytest.fixture(autouse=True)
def _stub_classifier(monkeypatch):
    """Every test in this file is about FLOW, not about judging an answer.

    This became necessary the moment `conversation_turn` gained a route. Until
    then `invoke_llm("conversation_turn")` raised ValueError on an unknown task
    type, the classifier degraded to "substantive" without touching anything,
    and these tests passed while exercising none of the real path -- which is
    precisely how the missing route survived a green suite for a day.

    With the route in place the classifier reaches the router, which loads keys
    through the SAME session these tests drive by hand, inside a transaction
    they have already closed. Stubbing it keeps the file testing what it says it
    tests; `tests/test_answer_classification.py` covers the judgement itself.
    """
    from app.services import answer_classification as ac

    async def _substantive(**kwargs):
        return ac.Classification(
            label="substantive",
            confidence="high",
            reason="stubbed for a flow test",
            needs_rechallenge=False,
            scorable=True,
        )

    monkeypatch.setattr(ac, "classify", _substantive)
    # `api.assessments` imported the module, not the symbol, so patching the
    # module attribute is enough and there is no second binding to keep in step.

    # The same reasoning, for the same reason, applied to the technical question
    # writer added on 2026-08-06. `respond` now writes the NEXT base question
    # before it returns, and for a technical slot that is a live model call
    # through `technical_interview.write_question`. Unstubbed it reaches the
    # router on this hand-driven session and, when every provider is down, the
    # router's own key bookkeeping rolls the transaction back underneath the
    # test -- which fails as a confusing "closed transaction" error a long way
    # from anything these tests are about.
    from app.services import agent_loop
    from app.services import technical_interview as ti

    async def _written(*, row, **kwargs):
        return agent_loop.LoopResult(
            value={"question": row.prompt, "rubric": dict(row.rubric_json or {})},
            degraded=False,
            attempts=1,
        )

    monkeypatch.setattr(ti, "write_question", _written)

    # And the PPI half's writer, for the same reason: a blended conversation
    # alternates the two, so stubbing only one still leaves every other turn
    # making a live call.
    from app.services import interviewer

    async def _composed(*, question, **kwargs):
        return question

    monkeypatch.setattr(interviewer, "compose_next_question", _composed)


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


class _Fx:
    def __init__(self) -> None:
        self.tenant_id = uuid.uuid4()
        self.job_id = uuid.uuid4()
        self.cand_id = uuid.uuid4()
        self.user_id = uuid.uuid4()
        self.link_id = uuid.uuid4()
        self.conv_id = uuid.uuid4()
        self.q_ids: list[uuid.UUID] = []


async def _seed(factory, fx: _Fx, question_count: int) -> None:
    from app.core.db import superadmin_scope
    from app.models import Candidate, Job, JobStatus, LinkSource, Tenant
    from app.models.assessment import AssessmentConversation, CandidateTechnicalQuestion
    from app.models.candidate import JobCandidateLink

    now = datetime.now(timezone.utc)
    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                s.add(Tenant(id=fx.tenant_id, name=f"Conv {fx.tenant_id.hex[:6]}",
                             domain=f"{fx.tenant_id}.conv.test"))
                # Flushed before the job: `jobs.tenant_id` is a foreign key, and
                # SQLAlchemy has no ordering obligation between two independent
                # add() calls in one unit of work.
                await s.flush()
                s.add(Job(id=fx.job_id, tenant_id=fx.tenant_id, title="Engineer",
                          jd_json={}, status=JobStatus.ratified, ratified_at=now,
                          assessment_status="ready_for_candidates",
                          assessment_grade="non_managerial"))
                s.add(Candidate(id=fx.cand_id, email=f"c{fx.cand_id.hex[:8]}@t.test",
                                full_name="Conv Candidate", consent_databank=False))
                await s.flush()
                s.add(JobCandidateLink(id=fx.link_id, tenant_id=fx.tenant_id,
                                       job_id=fx.job_id, candidate_id=fx.cand_id,
                                       source=LinkSource.fresh, status="applied"))
                # The link must exist before anything references it.
                await s.flush()
                # Technical questions are PER CANDIDATE as of 2026-08-06, so
                # they hang off the link rather than the job. Seeded directly
                # rather than through `technical_interview.ensure_slots`: these
                # tests drive `respond` alone, and a fixture that called the
                # real slot builder would couple every flow assertion to the
                # JD-skill plan, which is tested on its own elsewhere.
                for ordinal in range(question_count):
                    qid = uuid.uuid4()
                    fx.q_ids.append(qid)
                    s.add(CandidateTechnicalQuestion(
                        id=qid, tenant_id=fx.tenant_id, job_id=fx.job_id,
                        job_candidate_link_id=fx.link_id,
                        skill=f"Skill {ordinal}", prompt=f"Question {ordinal}?",
                        ordinal=ordinal, rubric_json={},
                    ))
                await s.flush()
                s.add(AssessmentConversation(
                    id=fx.conv_id, tenant_id=fx.tenant_id, job_id=fx.job_id,
                    job_candidate_link_id=fx.link_id, grade="non_managerial",
                    status="active", next_question_index=0, started_at=now,
                ))


async def _cleanup(factory, fx: _Fx) -> None:
    from app.core.db import superadmin_scope

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                for table, column in (
                    ("assessment_messages", "conversation_id"),
                ):
                    await s.execute(text(f"DELETE FROM {table} WHERE {column} = :v"),
                                    {"v": str(fx.conv_id)})
                await s.execute(text("DELETE FROM credit_ledger WHERE tenant_id = :t"),
                                {"t": str(fx.tenant_id)})
                await s.execute(text("DELETE FROM tenants WHERE id = :t"),
                                {"t": str(fx.tenant_id)})
                await s.execute(text("DELETE FROM candidates WHERE id = :c"),
                                {"c": str(fx.cand_id)})


def _user(fx: _Fx):
    from app.api.deps import CurrentUser
    from app.core.security import AUDIENCE_CANDIDATE
    from app.models import Role

    return CurrentUser(user_id=fx.user_id, tenant_id=None, role=Role.candidate,
                       audience=AUDIENCE_CANDIDATE)


_ANSWER = "I rebuilt the ingest pipeline and cut the nightly batch to minutes."


async def _respond(mod, fx, s, answer=_ANSWER):
    from app.schemas.assessments import ConversationMessageIn

    return await mod.respond(
        fx.conv_id, ConversationMessageIn(answer=answer),
        user=_user(fx), session=s,
    )


@pytest.mark.asyncio
async def test_a_follow_up_is_filed_under_the_same_question_key(monkeypatch) -> None:
    """The grouping hinge. A new key here would be silently dropped by every
    scorer, because nothing iterates keys the framework did not define."""
    from app.api import assessments as mod
    from app.core.db import superadmin_scope
    from app.models.assessment import AssessmentMessage
    from app.services.functional_assessment import answers_by_key

    monkeypatch.setattr(mod, "_candidate_link", _link_stub)
    monkeypatch.setattr(mod.celery_app, "send_task", lambda *a, **k: None)

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx, question_count=3)
        _patch_link(monkeypatch, fx)

        async def _probe(**kwargs):
            return "What broke first when you cut the batch?"

        monkeypatch.setattr(mod.interviewer, "next_follow_up", _probe)

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    first = await _respond(mod, fx, s)      # base question 0
                    second = await _respond(mod, fx, s)     # the follow-up

        assert first.prompt == "What broke first when you cut the batch?"

        async with factory() as s:
            async with superadmin_scope(s):
                rows = (await s.execute(
                    select(AssessmentMessage.speaker, AssessmentMessage.question_key,
                           AssessmentMessage.content)
                    .where(AssessmentMessage.conversation_id == fx.conv_id)
                    .order_by(AssessmentMessage.ordinal)
                )).all()

        transcript = [
            {"speaker": sp, "question_key": key, "content": content}
            for sp, key, content in rows
        ]
        grouped = answers_by_key(transcript)
        base_key = str(fx.q_ids[0])
        assert base_key in grouped, "the base question's key vanished"
        assert len(grouped[base_key]) == 2, (
            "the follow-up's answer was not filed with its base question; the "
            f"scorer would lose it (groups: { {k: len(v) for k, v in grouped.items()} })"
        )
        assert len(grouped) == 1, "a follow-up invented a question_key of its own"
        assert second is not None
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_follow_up_does_not_advance_the_index(monkeypatch) -> None:
    """The billing hinge. The index reaching len(prompts) is what charges the
    customer, so a probe must not push it along."""
    from app.api import assessments as mod
    from app.core.db import superadmin_scope
    from app.models.assessment import AssessmentConversation

    monkeypatch.setattr(mod.celery_app, "send_task", lambda *a, **k: None)

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx, question_count=3)
        _patch_link(monkeypatch, fx)

        async def _probe(**kwargs):
            return "Say more about that?"

        monkeypatch.setattr(mod.interviewer, "next_follow_up", _probe)

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    await _respond(mod, fx, s)     # base 0 -> index 1, probe set
                    conv = await s.get(AssessmentConversation, fx.conv_id)
                    after_base = conv.next_question_index
                    await _respond(mod, fx, s)     # answers the probe
                    await s.refresh(conv)
                    after_probe = conv.next_question_index

        assert after_base == 1
        assert after_probe == 1, (
            "answering a follow-up advanced the question index; the customer "
            "would be charged after fewer base questions than they paid for"
        )
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_pending_follow_up_holds_completion_open(monkeypatch) -> None:
    """The completion hinge. A probe outstanding on the LAST base question must
    not let billing and scoring fire while the candidate is still typing."""
    from app.api import assessments as mod
    from app.core.db import superadmin_scope
    from app.models.assessment import AssessmentConversation

    dispatched: list[str] = []
    monkeypatch.setattr(mod.celery_app, "send_task",
                        lambda name, *a, **k: dispatched.append(name))

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx, question_count=1)   # one base question only
        _patch_link(monkeypatch, fx)

        probes = ["One more thing, what would you change?"]

        async def _probe(**kwargs):
            return probes.pop() if probes else None

        monkeypatch.setattr(mod.interviewer, "next_follow_up", _probe)

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    out = await _respond(mod, fx, s)   # last base question
                    conv = await s.get(AssessmentConversation, fx.conv_id)
                    mid_status = conv.status
                    mid_dispatched = list(dispatched)
                    await _respond(mod, fx, s)         # answers the probe
                    await s.refresh(conv)
                    final_status = conv.status

        assert mid_status == "active", (
            "the conversation completed while a follow-up was still outstanding"
        )
        assert "pickready.run_functional_assessment" not in mid_dispatched, (
            "scoring was dispatched before the candidate finished answering"
        )
        assert out.prompt == "One more thing, what would you change?"
        assert final_status == "completed"
        assert "pickready.run_functional_assessment" in dispatched
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_without_a_follow_up_the_flow_is_unchanged(monkeypatch) -> None:
    """The regression guard for every conversation that never gets probed:
    one base question, answered once, completes exactly as it always did."""
    from app.api import assessments as mod
    from app.core.db import superadmin_scope
    from app.models.assessment import AssessmentConversation

    dispatched: list[str] = []
    monkeypatch.setattr(mod.celery_app, "send_task",
                        lambda name, *a, **k: dispatched.append(name))

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx, question_count=1)
        _patch_link(monkeypatch, fx)

        async def _none(**kwargs):
            return None

        monkeypatch.setattr(mod.interviewer, "next_follow_up", _none)

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    out = await _respond(mod, fx, s)
                    conv = await s.get(AssessmentConversation, fx.conv_id)

        assert conv.status == "completed"
        assert conv.next_question_index == 1
        assert out.prompt is None
        assert "pickready.run_functional_assessment" in dispatched
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


# ── Fixture plumbing ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_irrelevant_answer_holds_counter_then_valid_reask_advances_one(
    monkeypatch,
) -> None:
    """A rejected turn must not consume a paid base-question slot."""
    from app.api import assessments as mod
    from app.core.db import superadmin_scope
    from app.services.answer_classification import Classification

    monkeypatch.setattr(mod.celery_app, "send_task", lambda *a, **k: None)
    verdicts = iter(
        [
            Classification(
                label="off_topic",
                confidence="high",
                reason="answers a different question",
                needs_rechallenge=True,
                scorable=True,
            ),
            Classification(
                label="substantive",
                confidence="high",
                reason="direct and specific",
                needs_rechallenge=False,
                scorable=True,
            ),
        ]
    )

    async def _classify(**kwargs):
        return next(verdicts)

    async def _challenge(**kwargs):
        return (
            "That describes a database migration, not consumer lag. "
            "Please answer the Kafka question."
        )

    async def _none(**kwargs):
        return None

    monkeypatch.setattr(mod.answer_classification, "classify", _classify)
    monkeypatch.setattr(mod.interviewer, "challenge_non_answer", _challenge)
    monkeypatch.setattr(mod.interviewer, "next_follow_up", _none)

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx, question_count=3)
        _patch_link(monkeypatch, fx)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    rejected = await _respond(
                        mod,
                        fx,
                        s,
                        answer="I migrated Postgres to another region.",
                    )
                    accepted = await _respond(mod, fx, s, answer=_ANSWER)

        assert rejected.answered_questions == 0
        assert rejected.is_reask is True
        assert rejected.progress_label == "Question 1 of 3"
        assert accepted.answered_questions == 1
        assert accepted.is_reask is False
        assert accepted.progress_label == "Question 2 of 3"
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_reask_cap_records_evidence_gap_and_moves_on(monkeypatch) -> None:
    """Repeated non-answers are bounded and remain explicit in evidence."""
    from app.api import assessments as mod
    from app.core.db import superadmin_scope
    from app.models.assessment import AssessmentMessage
    from app.services.answer_classification import Classification

    monkeypatch.setattr(mod.celery_app, "send_task", lambda *a, **k: None)

    async def _invalid(**kwargs):
        return Classification(
            label="shallow",
            confidence="high",
            reason="names the topic but supplies no requested evidence",
            needs_rechallenge=True,
            scorable=True,
        )

    async def _challenge(**kwargs):
        return (
            "You named the topic but not the evidence requested. "
            "Please give one specific example."
        )

    monkeypatch.setattr(mod.answer_classification, "classify", _invalid)
    monkeypatch.setattr(mod.interviewer, "challenge_non_answer", _challenge)

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx, question_count=3)
        _patch_link(monkeypatch, fx)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    first = await _respond(mod, fx, s, answer="Kafka.")
                    second = await _respond(mod, fx, s, answer="Consumer lag.")
                    capped = await _respond(mod, fx, s, answer="Performance.")

        assert first.answered_questions == 0
        assert second.answered_questions == 0
        assert capped.answered_questions == 1
        assert capped.is_reask is False

        async with factory() as s:
            async with superadmin_scope(s):
                last_answer = (
                    await s.execute(
                        select(AssessmentMessage)
                        .where(
                            AssessmentMessage.conversation_id == fx.conv_id,
                            AssessmentMessage.speaker == "candidate",
                        )
                        .order_by(AssessmentMessage.ordinal.desc())
                        .limit(1)
                    )
                ).scalar_one()
        assert last_answer.answer_label == "shallow"
        assert last_answer.evidence_gap is True
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


async def _link_stub(*args, **kwargs):  # replaced per-test by _patch_link
    raise NotImplementedError


def _patch_link(monkeypatch, fx: _Fx) -> None:
    """`_candidate_link` resolves the caller's candidate identity, which these
    tests do not exercise: they drive the conversation, not the auth path."""
    from app.api import assessments as mod
    from app.models import Job
    from app.models.candidate import JobCandidateLink

    async def _resolve(session, user, link_id):
        link = await session.get(JobCandidateLink, fx.link_id)
        job = await session.get(Job, fx.job_id)
        return link, job

    monkeypatch.setattr(mod, "_candidate_link", _resolve)
