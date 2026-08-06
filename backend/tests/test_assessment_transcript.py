"""The recruiter's Q&A view of an assessment.

A report states a grade; this is the evidence behind it, and before 2026-08-06
there was no way to read it outside a psql session.

What is asserted here is the PAIRING, because that is the part a client would
otherwise have to reimplement and get subtly wrong:

  * a question is paired with the answer that actually followed it, not with
    whatever row happens to be next;
  * an ABANDONED assessment's dangling last question is dropped rather than
    paired with someone else's answer;
  * a follow-up is recognised WITHOUT a new column, from the fact that it reuses
    its parent's question_key -- which is exactly how the scorers file it;
  * the criterion is resolved to a WORD, and no score, rubric or number crosses
    the boundary.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text


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
        self.tech_id = uuid.uuid4()
        self.comp_id = uuid.uuid4()


async def _seed(factory, fx: _Fx, messages: list[tuple[str, str, str, str]]) -> None:
    """`messages` is [(speaker, domain, question_key, content)] in order."""
    from app.core.db import superadmin_scope
    from app.models import Candidate, Job, JobStatus, LinkSource, Tenant
    from app.models.assessment import (
        AssessmentConversation,
        AssessmentMessage,
        CandidateTechnicalQuestion,
        JobCompetency,
    )
    from app.models.candidate import JobCandidateLink

    now = datetime.now(timezone.utc)
    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                s.add(Tenant(id=fx.tenant_id, name=f"Tx {fx.tenant_id.hex[:6]}",
                             domain=f"{fx.tenant_id}.tx.test"))
                await s.flush()
                s.add(Job(id=fx.job_id, tenant_id=fx.tenant_id, title="Engineer",
                          jd_json={}, status=JobStatus.ratified, ratified_at=now,
                          assessment_status="ready_for_candidates",
                          assessment_grade="non_managerial"))
                s.add(Candidate(id=fx.cand_id, email=f"c{fx.cand_id.hex[:8]}@t.test",
                                full_name="Transcript Candidate", consent_databank=False))
                await s.flush()
                s.add(JobCandidateLink(id=fx.link_id, tenant_id=fx.tenant_id,
                                       job_id=fx.job_id, candidate_id=fx.cand_id,
                                       source=LinkSource.fresh, status="applied"))
                s.add(JobCompetency(id=fx.comp_id, tenant_id=fx.tenant_id,
                                    job_id=fx.job_id, category="primary_skill",
                                    name="Incident response", ordinal=1,
                                    required_level=82))
                await s.flush()
                s.add(CandidateTechnicalQuestion(
                    id=fx.tech_id, tenant_id=fx.tenant_id, job_id=fx.job_id,
                    job_candidate_link_id=fx.link_id, ordinal=1, skill="Kafka",
                    prompt="stored", rubric_json={},
                ))
                s.add(AssessmentConversation(
                    id=fx.conv_id, tenant_id=fx.tenant_id, job_id=fx.job_id,
                    job_candidate_link_id=fx.link_id, grade="non_managerial",
                    status="active", next_question_index=0, started_at=now,
                ))
                await s.flush()
                for ordinal, (speaker, domain, key, content) in enumerate(messages, 1):
                    s.add(AssessmentMessage(
                        tenant_id=fx.tenant_id, conversation_id=fx.conv_id,
                        ordinal=ordinal, speaker=speaker, domain=domain,
                        question_key=key, content=content,
                    ))


async def _cleanup(factory, fx: _Fx) -> None:
    from app.core.db import superadmin_scope

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                await s.execute(text("DELETE FROM tenants WHERE id = :t"),
                                {"t": str(fx.tenant_id)})
                await s.execute(text("DELETE FROM candidates WHERE id = :c"),
                                {"c": str(fx.cand_id)})


def _user(fx: _Fx):
    from app.api.deps import CurrentUser
    from app.core.security import AUDIENCE_ORG
    from app.models import Role

    return CurrentUser(user_id=fx.user_id, tenant_id=fx.tenant_id, role=Role.recruiter,
                       audience=AUDIENCE_ORG)


async def _fetch(fx: _Fx, factory, **kwargs):
    from app.api import assessments as mod
    from app.core.db import superadmin_scope

    async with factory() as s:
        async with superadmin_scope(s):
            return await mod.get_transcript(
                fx.link_id, user=_user(fx), session=s, **kwargs
            )


@pytest.mark.asyncio
async def test_questions_pair_with_their_own_answers() -> None:
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    tech, comp = str(fx.tech_id), str(fx.comp_id)
    try:
        await _seed(factory, fx, [
            ("agent", "technical", tech, "How did you tune Kafka consumer lag?"),
            ("candidate", "technical", tech, "I moved to a larger consumer group."),
            ("agent", "ppi", comp, "Tell me about an incident you led."),
            ("candidate", "ppi", comp, "The payments outage in March."),
        ])
        out = await _fetch(fx, factory)

        assert out.total == 2
        assert [e.ordinal for e in out.exchanges] == [1, 2]
        assert out.exchanges[0].question.startswith("How did you tune Kafka")
        assert out.exchanges[0].answer == "I moved to a larger consumer group."
        assert out.exchanges[1].answer == "The payments outage in March."
        # Criteria resolved to WORDS, from both scorers' key spaces.
        assert out.exchanges[0].criterion == "Kafka"
        assert out.exchanges[1].criterion == "Incident response"
        assert out.candidate_name == "Transcript Candidate"
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_follow_up_is_recognised_by_its_reused_key() -> None:
    """No new column. A probe shares its parent's question_key -- which is
    exactly how `answers_by_key` files it as more evidence for one question --
    so that is what identifies it here too."""
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    tech = str(fx.tech_id)
    try:
        await _seed(factory, fx, [
            ("agent", "technical", tech, "How did you tune Kafka consumer lag?"),
            ("candidate", "technical", tech, "I moved to a larger consumer group."),
            ("agent", "technical", tech, "What broke first when you did that?"),
            ("candidate", "technical", tech, "Rebalance storms on deploy."),
        ])
        out = await _fetch(fx, factory)

        assert out.total == 2
        assert out.exchanges[0].follow_up is False
        assert out.exchanges[1].follow_up is True
        # A probe shares its parent's criterion, and should say so rather than
        # appearing uncategorised.
        assert out.exchanges[1].criterion == "Kafka"
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_an_abandoned_assessments_dangling_question_is_dropped() -> None:
    """Zipping alternate rows would pair the unanswered last question with the
    NEXT candidate line, which in an abandoned interview is nothing and in a
    resumed one is someone else's answer to a different question."""
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    tech, comp = str(fx.tech_id), str(fx.comp_id)
    try:
        await _seed(factory, fx, [
            ("agent", "technical", tech, "How did you tune Kafka consumer lag?"),
            ("candidate", "technical", tech, "I moved to a larger consumer group."),
            ("agent", "ppi", comp, "Tell me about an incident you led."),
            # ...and they closed the tab.
        ])
        out = await _fetch(fx, factory)

        assert out.total == 1
        assert out.exchanges[0].answer == "I moved to a larger consumer group."
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_an_uninvited_application_reports_not_started_rather_than_404() -> None:
    """The recruiter asked a reasonable question about a real application, and
    "nothing yet" is the true answer to it. A 404 reads as a broken screen."""
    from app.core.db import superadmin_scope
    from app.models import Candidate, Job, JobStatus, LinkSource, Tenant
    from app.models.candidate import JobCandidateLink

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        now = datetime.now(timezone.utc)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    s.add(Tenant(id=fx.tenant_id, name=f"Tx {fx.tenant_id.hex[:6]}",
                                 domain=f"{fx.tenant_id}.tx.test"))
                    await s.flush()
                    s.add(Job(id=fx.job_id, tenant_id=fx.tenant_id, title="Engineer",
                              jd_json={}, status=JobStatus.ratified, ratified_at=now,
                              assessment_grade="non_managerial"))
                    s.add(Candidate(id=fx.cand_id, email=f"c{fx.cand_id.hex[:8]}@t.test",
                                    full_name="Never Invited", consent_databank=False))
                    await s.flush()
                    s.add(JobCandidateLink(id=fx.link_id, tenant_id=fx.tenant_id,
                                           job_id=fx.job_id, candidate_id=fx.cand_id,
                                           source=LinkSource.fresh, status="applied"))
        out = await _fetch(fx, factory)
        assert out.status == "not_started"
        assert out.exchanges == []
        assert out.total == 0
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_another_tenants_application_is_not_readable() -> None:
    """Tenant isolation on a route that returns a candidate's raw answers."""
    from fastapi import HTTPException

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    tech = str(fx.tech_id)
    try:
        await _seed(factory, fx, [
            ("agent", "technical", tech, "Q?"),
            ("candidate", "technical", tech, "A."),
        ])
        fx.tenant_id = uuid.uuid4()  # the CALLER is now somebody else
        with pytest.raises(HTTPException) as exc:
            await _fetch(fx, factory)
        assert exc.value.status_code == 404
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_pagination_is_bounded_and_cannot_be_argued_out_of() -> None:
    """A long interview is 120 messages, and this endpoint must not become the
    one that returns an entire tenant's interview history."""
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    tech = str(fx.tech_id)
    try:
        messages: list[tuple[str, str, str, str]] = []
        for index in range(6):
            messages.append(("agent", "technical", f"{tech}:{index}", f"Q{index}?"))
            messages.append(("candidate", "technical", f"{tech}:{index}", f"A{index}."))
        await _seed(factory, fx, messages)

        page = await _fetch(fx, factory, limit=2, offset=2)
        assert page.total == 6
        assert len(page.exchanges) == 2
        assert page.exchanges[0].answer == "A2."

        # A caller asking for more than the ceiling gets the ceiling, not more.
        from app.api import assessments as mod

        clamped = await _fetch(fx, factory, limit=10_000)
        assert clamped.limit == mod.TRANSCRIPT_MAX_LIMIT
        # And a negative offset is a clamp, not a Python tail slice.
        negative = await _fetch(fx, factory, offset=-5)
        assert negative.offset == 0
        assert negative.exchanges[0].answer == "A0."
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


def test_no_score_or_rubric_can_cross_this_boundary() -> None:
    """The no-numbers rule covers this response like every other. The rubric is
    internal scoring machinery and is not evidence a recruiter needs."""
    from app.schemas.assessments import TranscriptExchangeOut, TranscriptOut

    fields = set(TranscriptExchangeOut.model_fields) | set(TranscriptOut.model_fields)
    for banned in ("score", "rubric", "rubric_json", "required_level", "grade"):
        assert banned not in fields
