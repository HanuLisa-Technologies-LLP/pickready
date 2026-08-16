"""Change 6: evidence authenticity, differentiated narratives, and PDF export."""
from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher
from types import SimpleNamespace

import pytest
from pypdf import PdfReader
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.db import superadmin_scope
from app.models.assessment import (
    AssessmentConversation,
    JobCompetency,
    ReportSkillEvidence,
)
from app.services import agent_loop
from app.services import functional_assessment as fa
from app.services.report_evidence import build_evidence_payload, persist_skill_evidence
from app.services.report_pdf import FOOTER, render_report_pdf


FIXTURE_CASES = [
    (
        "Kafka",
        "The candidate traced Kafka partition skew to one overloaded consumer, rebalanced ownership, and verified recovery through lag metrics during a checkout incident. That concrete diagnosis demonstrates strong distributed-systems judgement. A follow-up should test whether the same reasoning holds during cross-region failure and partial broker availability.",
    ),
    (
        "PostgreSQL",
        "While describing PostgreSQL lock contention, the candidate identified a long transaction, changed the write sequence, and confirmed duplicate billing stopped under peak traffic. The account supports dependable systems reasoning. Interviewers should probe how deadlock risk was measured and which rollback path the candidate personally designed.",
    ),
    (
        "Redis",
        "The Redis example named eviction pressure and a cache-key redesign, but it did not explain how stale reads were detected or which consistency trade-off was accepted at scale. This is partial evidence. A focused discussion should examine invalidation ownership, failure behaviour, and verification under concurrent updates.",
    ),
    (
        "OpenTelemetry",
        "The conversation mentioned OpenTelemetry traces but supplied no owned incident, diagnostic sequence, or resulting production change during a release. Capability therefore remains unresolved. A direct role-specific probe should ask the candidate to isolate one latency regression and distinguish missing observability knowledge from an example they omitted.",
    ),
    (
        "Cloud Run",
        "In the Cloud Run account, the candidate linked concurrency changes to traffic spikes, described testing under load, and explained why minimum capacity was retained. The evidence is relevant and specific. Interviewers should verify cost trade-offs, cold-start measurement, and how the candidate handled a failed rollout.",
    ),
]


@pytest.mark.asyncio
async def test_five_fixture_candidates_have_distinct_banned_phrase_free_narratives(
    monkeypatch,
) -> None:
    responses = iter(text for _, text in FIXTURE_CASES)

    async def _chat(*args, **kwargs):
        return next(responses)

    monkeypatch.setattr(fa.llm_router, "chat_completion", _chat)
    narratives: list[str] = []
    ratings = [
        "Highly Matching",
        "Matching",
        "Moderately Matching",
        "Not Matching",
        "Matching",
    ]
    for (anchor, _), rating in zip(FIXTURE_CASES, ratings, strict=True):
        narratives.append(
            await fa.bounded_remark(
                None,
                "Distributed Systems",
                f"{anchor} production evidence",
                45,
                50,
                rating=rating,
            )
        )

    assert all(45 <= fa.word_count(value) <= 50 for value in narratives)
    assert all(
        agent_loop.banned_phrase_gate(value, fa.REPORT_BANNED_PHRASES).ok
        for value in narratives
    )
    similarities = [
        SequenceMatcher(None, left.casefold(), right.casefold()).ratio()
        for index, left in enumerate(narratives)
        for right in narratives[index + 1 :]
    ]
    assert max(similarities) < 0.72


def test_structured_evidence_names_precision_examples_structure_and_gaps() -> None:
    payload = build_evidence_payload(
        skill="Distributed Systems",
        category="technical",
        question_keys=["question-1"],
        questions=["How did you recover the service?"],
        answers=[
            "I traced the queue latency to a locked transaction. First I "
            "changed the retry sequence, then verified the result with traces "
            "and reduced checkout latency by 40 ms."
        ],
    )
    assert payload["technical_precision"]
    assert payload["problem_solving_structure"]
    assert payload["concrete_examples"]
    assert payload["role_relevance"]
    assert payload["explicit_gaps"] == []

    empty = build_evidence_payload(
        skill="Redis",
        category="technical",
        question_keys=["question-2"],
        questions=["Explain cache invalidation."],
        answers=[],
    )
    assert "No substantive answer" in empty["explicit_gaps"][0]


@pytest.mark.asyncio
async def test_five_fixture_assessments_are_created_in_real_postgres_and_cleaned() -> None:
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001
        await engine.dispose()
        pytest.skip("no real PostgreSQL available for Change 6 fixture verification")

    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id, job_id, competency_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with factory() as session:
        transaction = await session.begin()
        try:
            async with superadmin_scope(session):
                await session.execute(
                    text(
                        "INSERT INTO tenants (id, name, domain, spf_dkim_status) "
                        "VALUES (:id, 'Change 6 Fixture Tenant', :domain, 'pending')"
                    ),
                    {"id": str(tenant_id), "domain": f"{tenant_id}.change6.test"},
                )
                await session.execute(
                    text(
                        "INSERT INTO jobs (id, tenant_id, title, jd_json, status) "
                        "VALUES (:id, :tenant, 'Platform Engineer', '{}'::jsonb, 'draft')"
                    ),
                    {"id": str(job_id), "tenant": str(tenant_id)},
                )
                await session.execute(
                    text(
                        "INSERT INTO job_competencies "
                        "(id, tenant_id, job_id, category, name, ordinal) "
                        "VALUES (:id, :tenant, :job, 'must_have', "
                        "'Distributed Systems', 1)"
                    ),
                    {
                        "id": str(competency_id),
                        "tenant": str(tenant_id),
                        "job": str(job_id),
                    },
                )
                competency = await session.get(JobCompetency, competency_id)
                assert competency is not None

                for index, (anchor, narrative) in enumerate(FIXTURE_CASES, 1):
                    candidate_id = uuid.uuid4()
                    profile_id = uuid.uuid4()
                    link_id = uuid.uuid4()
                    conversation_id = uuid.uuid4()
                    await session.execute(
                        text(
                            "INSERT INTO candidates "
                            "(id, tenant_id, full_name, email, consent_databank) "
                            "VALUES (:id, :tenant, :name, :email, false)"
                        ),
                        {
                            "id": str(candidate_id),
                            "tenant": str(tenant_id),
                            "name": f"Change 6 Fixture Candidate {index}",
                            "email": f"change6-fixture-{index}@example.test",
                        },
                    )
                    await session.execute(
                        text(
                            "INSERT INTO profiles "
                            "(id, candidate_id, source_tenant_id, resume_text) "
                            "VALUES (:id, :candidate, :tenant, :resume)"
                        ),
                        {
                            "id": str(profile_id),
                            "candidate": str(candidate_id),
                            "tenant": str(tenant_id),
                            "resume": f"Platform engineer with {anchor} experience.",
                        },
                    )
                    await session.execute(
                        text(
                            "INSERT INTO job_candidate_links "
                            "(id, tenant_id, job_id, candidate_id, profile_id, source) "
                            "VALUES (:id, :tenant, :job, :candidate, :profile, 'manual')"
                        ),
                        {
                            "id": str(link_id),
                            "tenant": str(tenant_id),
                            "job": str(job_id),
                            "candidate": str(candidate_id),
                            "profile": str(profile_id),
                        },
                    )
                    await session.execute(
                        text(
                            "INSERT INTO assessment_conversations "
                            "(id, tenant_id, job_id, job_candidate_link_id, grade, "
                            " status, next_question_index, follow_ups_used, reminders_sent) "
                            "VALUES (:id, :tenant, :job, :link, 'non_managerial', "
                            " 'completed', 1, 0, 0)"
                        ),
                        {
                            "id": str(conversation_id),
                            "tenant": str(tenant_id),
                            "job": str(job_id),
                            "link": str(link_id),
                        },
                    )
                    transcript = [
                        {
                            "speaker": "agent",
                            "domain": "ppi",
                            "question_key": str(competency_id),
                            "content": "Describe the distributed-systems decision you owned.",
                        },
                        {
                            "speaker": "candidate",
                            "domain": "ppi",
                            "question_key": str(competency_id),
                            "content": narrative,
                        },
                    ]
                    await session.execute(
                        text(
                            "INSERT INTO assessment_messages "
                            "(id, tenant_id, conversation_id, ordinal, speaker, "
                            " domain, question_key, content) VALUES "
                            "(:agent_id, :tenant, :conversation, 1, 'agent', "
                            " 'ppi', :key, :question), "
                            "(:candidate_id, :tenant, :conversation, 2, 'candidate', "
                            " 'ppi', :key, :answer)"
                        ),
                        {
                            "agent_id": str(uuid.uuid4()),
                            "candidate_id": str(uuid.uuid4()),
                            "tenant": str(tenant_id),
                            "conversation": str(conversation_id),
                            "key": str(competency_id),
                            "question": transcript[0]["content"],
                            "answer": transcript[1]["content"],
                        },
                    )
                    conversation = await session.get(
                        AssessmentConversation,
                        conversation_id,
                    )
                    assert conversation is not None
                    await persist_skill_evidence(
                        session,
                        conversation=conversation,
                        transcript=transcript,
                        technical_questions=[],
                        competencies=[competency],
                    )

                count = (
                    await session.execute(
                        select(func.count(ReportSkillEvidence.id)).where(
                            ReportSkillEvidence.tenant_id == tenant_id
                        )
                    )
                ).scalar_one()
                assert count == 5
        finally:
            await transaction.rollback()
            await engine.dispose()


def _report_fixture():
    dimension = {
        "name": "Distributed Systems",
        "grade": "Matching",
        "required_level": "Matching",
        "remark": FIXTURE_CASES[0][1],
    }
    return SimpleNamespace(
        overall_grade="Matching",
        overall_summary=FIXTURE_CASES[4][1],
        radar_charts=[
            {
                "title": title,
                "axes": [
                    {
                        "axis": name,
                        "requirement_index": 3,
                        "candidate_index": 1 + ((chart_index + axis_index) % 4),
                    }
                    for axis_index, name in enumerate(
                        ("Architecture", "Delivery", "Judgement")
                    )
                ],
            }
            for chart_index, title in enumerate(
                (
                    "Overall",
                    "Must-have",
                    "Nice-to-have",
                    "Behavioural Competencies",
                )
            )
        ],
        ai_score=[dimension],
        must_have=[dimension],
        nice_to_have=[dimension],
        behavioural=[dimension],
        suggested_interview_questions=[
            "For Distributed Systems, explain the hardest recovery trade-off you owned."
        ],
        validation={
            "fields": [
                {"label": "Notice period", "value": "Thirty days"},
                {"label": "Role interest", "value": "Platform reliability ownership"},
            ]
        },
    )


def test_pdf_contains_branding_all_sections_charts_and_confidential_footer() -> None:
    payload = render_report_pdf(
        _report_fixture(),
        candidate_name="Change Six Fixture Candidate",
        job_title="Platform Engineer",
        tenant_name="Fixture Tenant — Test Data",
        generated_at=datetime(2026, 8, 7, tzinfo=timezone.utc),
    )
    assert payload.startswith(b"%PDF-")
    reader = PdfReader(io.BytesIO(payload))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert "ReadyPick" in text
    assert "Change Six Fixture Candidate" in text
    assert "Platform Engineer" in text
    assert "Must-have" in text
    assert "Nice-to-have" in text
    # Draft v4 removed the retired section completely, including legacy payloads.
    assert "Suggested interview questions" not in text
    assert FOOTER in text

    image_objects = 0
    for page in reader.pages:
        resources = page.get("/Resources")
        xobjects = resources.get("/XObject", {}) if resources else {}
        for value in xobjects.values():
            resolved = value.get_object()
            if resolved.get("/Subtype") == "/Image":
                image_objects += 1
    assert image_objects >= 4
