"""Per-candidate question generation against the skills contract, end to end.

Real Postgres; the model router and the sandbox are scripted at their
boundaries (`llm_router.invoke_llm`, `code_execution.override_provider`), and
everything between them is the product's code. Every table assertion reads
from a SECOND connection after the generating transaction committed, because a
write that rolled back at commit is invisible to the connection that made it.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.db import superadmin_scope
from app.models.assessment import AssessmentConversation
from app.models.candidate import JobCandidateLink
from app.models.job import Job
from app.services import assessment_contract, llm_router
from app.services.assessment_formats import coding_generation, types
from app.services.assessment_questions import budget
from app.services.assessment_questions import generate as question_generation
from app.services.code_execution import override_provider
from app.services.code_execution.fake import FakeProvider
from app.services.coding_assessment import payload as coding_payload
from tests.test_coding_generation import SENTINELS, _question, _script

#: Planted in every place compensation could reach a prompt from.
CTC_SENTINEL = "4217000"
JD_SENTINEL = "zqjobdescriptionsentinel"

RESUME_LINES = tuple(
    f"Delivered initiative number {index} for Kestrel Retail in the Pune office."
    for index in range(1, 20)
)


@dataclass
class _World:
    tenant: uuid.UUID = field(default_factory=uuid.uuid4)
    job: uuid.UUID = field(default_factory=uuid.uuid4)
    candidate: uuid.UUID = field(default_factory=uuid.uuid4)
    profile: uuid.UUID = field(default_factory=uuid.uuid4)
    link: uuid.UUID = field(default_factory=uuid.uuid4)
    conversation: uuid.UUID = field(default_factory=uuid.uuid4)
    skills: dict[str, uuid.UUID] = field(default_factory=dict)


async def _factory():
    engine = create_async_engine(get_settings().database_url)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed(
    factory,
    *,
    title: str = "Senior Software Engineer",
    classification: str = "STEM",
    grade: str = "managerial",
    per_bucket: tuple[int, int, int] = (5, 5, 5),
    saved: bool = True,
    invited: bool = True,
) -> _World:
    w = _World()
    async with factory() as session:
        async with superadmin_scope(session):
            await session.execute(
                text(
                    "INSERT INTO tenants (id, name, domain, spf_dkim_status) "
                    "VALUES (:t, :n, :d, 'pending')"
                ),
                {"t": w.tenant, "n": f"questions-{w.tenant}", "d": f"{w.tenant}.questions.test"},
            )
            await session.execute(
                text(
                    "INSERT INTO jobs (id, tenant_id, title, jd_json, jd_markdown, "
                    "compensation_json, status, assessment_grade, role_classification, "
                    "framework_approved_at, assessment_context_json) VALUES (:j, :t, :title, "
                    "CAST(:jd AS jsonb), :md, CAST(:comp AS jsonb), 'approved', :g, :rc, "
                    ":approved, CAST(:ctx AS jsonb))"
                ),
                {
                    "j": w.job, "t": w.tenant, "title": title, "g": grade, "rc": classification,
                    "jd": json.dumps({"ctc": CTC_SENTINEL, "summary": JD_SENTINEL}),
                    "md": f"{JD_SENTINEL} Salary {CTC_SENTINEL} per annum.",
                    "comp": json.dumps({"ctc_min": int(CTC_SENTINEL), "ctc_max": 5000000}),
                    "approved": datetime.now(timezone.utc) if saved else None,
                    "ctx": json.dumps({"role_summary": "Owns the order platform.", "generated_by": "sutra"})
                    if saved else None,
                },
            )
            for bucket, count in zip(("must_have", "nice_to_have", "behavioural"), per_bucket):
                for priority in range(1, count + 1):
                    skill_id = uuid.uuid4()
                    name = f"{bucket} skill {priority}"
                    w.skills[name] = skill_id
                    await session.execute(
                        text(
                            "INSERT INTO job_competencies (id, tenant_id, job_id, category, "
                            "name, required_level, ordinal, is_active, observable_evidence, "
                            "force_rank) VALUES (:i, :t, :j, :c, :n, 82, :o, true, :e, :r)"
                        ),
                        {"i": skill_id, "t": w.tenant, "j": w.job, "c": bucket, "n": name,
                         "o": priority, "e": f"Has shown {name} in production work.", "r": priority},
                    )
            await session.execute(
                text(
                    "INSERT INTO candidates (id, tenant_id, full_name, email, consent_databank) "
                    "VALUES (:c, :t, 'Question Subject', :e, false)"
                ),
                {"c": w.candidate, "t": w.tenant, "e": f"{w.candidate}@questions.test"},
            )
            await session.execute(
                text(
                    "INSERT INTO profiles (id, candidate_id, source_tenant_id, resume_text) "
                    "VALUES (:p, :c, :t, :r)"
                ),
                {"p": w.profile, "c": w.candidate, "t": w.tenant, "r": "\n".join(RESUME_LINES)},
            )
            await session.execute(
                text(
                    "INSERT INTO job_candidate_links (id, tenant_id, job_id, candidate_id, "
                    "profile_id, source, validation_json) VALUES (:l, :t, :j, :c, :p, 'fresh', "
                    "CAST(:v AS jsonb))"
                ),
                {"l": w.link, "t": w.tenant, "j": w.job, "c": w.candidate, "p": w.profile,
                 "v": json.dumps({"current_ctc": CTC_SENTINEL, "expected_ctc": CTC_SENTINEL})},
            )
            if invited:
                await session.execute(
                    text(
                        "INSERT INTO assessment_conversations (id, tenant_id, job_id, "
                        "job_candidate_link_id, grade, status, next_question_index, "
                        "reminders_sent, follow_ups_used, reasks_used, mode) VALUES "
                        "(:i, :t, :j, :l, :g, 'active', 0, 0, 0, 0, 'conversational')"
                    ),
                    {"i": w.conversation, "t": w.tenant, "j": w.job, "l": w.link, "g": grade},
                )
            await session.commit()
    return w


async def _drop(factory, w: _World) -> None:
    async with factory() as session:
        async with superadmin_scope(session):
            await session.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": w.tenant})
            await session.commit()


class Router:
    """Answers each task type the way a well-behaved model would, and records
    every message it was sent."""

    def __init__(self, coding: list[dict[str, Any]] | None = None, *, down: set[str] = frozenset()) -> None:
        self.coding = list(coding or [])
        self.down = set(down)
        self.calls: list[tuple[str, list[dict[str, str]]]] = []

    async def __call__(self, task_type: str, messages: list[dict[str, str]], **kwargs: Any) -> str:
        self.calls.append((task_type, messages))
        system = messages[0]["content"]
        kind = task_type
        if task_type == "format_composition":
            kind = "anchoring" if "EVIDENCE-BASED" in system else "objective"
        if kind in self.down:
            raise llm_router.LLMUnavailableError(f"{kind} is down in this test")
        payload = json.loads(messages[1]["content"])
        if kind == "question_generation":
            return json.dumps({"questions": [
                {"index": slot["index"],
                 "prompt": f"Tell me about the hardest decision you made on {slot['skill']} "
                           f"and what happened next, angle {slot['index']}."}
                for slot in payload["slots"]
            ]})
        if kind == "anchoring":
            return json.dumps({"questions": [
                {"index": slot["index"],
                 "prompt": f"You wrote about initiative number {position + 1}. What did you decide?",
                 "resume_anchor": RESUME_LINES[position],
                 "sub_type": "project_deep_dive",
                 "anchor_source": "summary"}
                for position, slot in enumerate(payload["slots"])
            ]})
        if kind == "objective":
            if "fill-in-the-blank" in system:
                return json.dumps({"prompt": "Complete the sentence.", "payload": {
                    "template": "The ___ pattern notifies subscribers.",
                    "blanks": [{"index": 0, "accepted": ["observer"], "case_sensitive": False}]}})
            options = [{"id": key, "text": f"Option {key} about indexing"} for key in "abcd"]
            misconceptions = {key: f"Believes {key} always makes queries faster" for key in "abcd"}
            if "TWO OR MORE" in system:
                return json.dumps({"prompt": "Select all that apply to a composite index.",
                                   "payload": {"options": options, "correct_option_ids": ["a", "b"],
                                               "scoring": "partial"},
                                   "misconceptions": {k: v for k, v in misconceptions.items() if k not in "ab"}})
            return json.dumps({"prompt": "Which statement about a composite index is true?",
                               "payload": {"options": options, "correct_option_id": "a"},
                               "misconceptions": {k: v for k, v in misconceptions.items() if k != "a"}})
        if kind == "coding_question_generation":
            return json.dumps(self.coding.pop(0))
        raise AssertionError(f"unexpected task type {task_type}")


def _coding_questions(provider: FakeProvider, count: int) -> list[dict[str, Any]]:
    questions = []
    for n in range(1, count + 1):
        question = _question(
            title=f"Busiest failing endpoint {n}",
            reference_solution=f"# zqreferencesentinel {n}\nimport sys\nprint(sys.stdin.read())\n",
        )
        _script(provider, question)
        questions.append(question)
    return questions


async def _generate(factory, w: _World) -> question_generation.QuestionSet:
    async with factory() as session:
        async with superadmin_scope(session):
            job = await session.get(Job, w.job)
            link = await session.get(JobCandidateLink, w.link)
            result = await question_generation.generate_candidate_questions(session, job, link)
            await session.commit()
    return result


async def _rows(factory, w: _World) -> list[dict[str, Any]]:
    async with factory() as session:
        async with superadmin_scope(session):
            rows = (await session.execute(
                text(
                    "SELECT id, ordinal, question_type, competency_id, prompt, payload_json, "
                    "resume_anchor, prefilled_answer, prefill_source, generated_at, "
                    "time_allocation_seconds FROM candidate_questions "
                    "WHERE job_candidate_link_id = :l ORDER BY ordinal"
                ),
                {"l": w.link},
            )).mappings().all()
    return [dict(row) for row in rows]


def _family(question_type: str) -> str:
    return {"evidence_based": "prose", "short_answer": "prose", "coding": "coding"}.get(
        question_type, "objective"
    )


# ── A coding role with a working sandbox ─────────────────────────────────────


async def test_a_coding_role_is_served_seventy_twenty_ten_against_every_skill(monkeypatch, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    engine, factory = await _factory()
    w = await _seed(factory)
    try:
        provider = FakeProvider()
        router = Router(_coding_questions(provider, 3))
        monkeypatch.setattr(llm_router, "invoke_llm", router)
        with override_provider(provider):
            result = await _generate(factory, w)

        rows = await _rows(factory, w)
        families = [_family(row["question_type"]) for row in rows]
        assert len(rows) == 15
        assert {f: families.count(f) for f in ("prose", "coding", "objective")} == {
            "prose": 11, "coding": 3, "objective": 1,
        }
        # Every skill is asked, and nothing was answered before it was asked.
        assert {row["competency_id"] for row in rows} == set(w.skills.values())
        assert all(row["prefilled_answer"] is None and row["prefill_source"] is None for row in rows)
        assert all(row["generated_at"] is not None for row in rows)
        assert {row["time_allocation_seconds"] for row in rows if row["question_type"] == "coding"} == {1200}

        # The composition record: planned is served, nothing degraded.
        record = result.composition
        assert record["planned"] == record["served"] == {"prose": 11, "coding": 3, "objective": 1}
        assert record["degraded"] == [] and record["templated"] == []
        assert record["coding"] == {"eligible": True, "excluded_reason": None}
        async with factory() as session:
            async with superadmin_scope(session):
                contract = await assessment_contract.load_contract(session, w.job)
                keys = (await session.execute(
                    text("SELECT count(*) FROM coding_question_keys k JOIN candidate_questions q "
                         "ON q.id = k.question_id WHERE q.job_candidate_link_id = :l"),
                    {"l": w.link},
                )).scalar_one()
        assert record["contract_digest"] == contract.digest
        assert record["contract_version"] == contract.version == 0
        assert keys == 3

        # A coding row is the EXECUTED shape, and the candidate sees exactly
        # the named fields with no part of the answer key.
        for row in (r for r in rows if r["question_type"] == types.CODING):
            assert coding_payload.is_v2(row["payload_json"])
            view = types.candidate_view(row["id"], types.CODING, row["payload_json"])
            assert tuple(view) == coding_payload.CANDIDATE_FIELDS
            for sentinel in SENTINELS:
                assert sentinel not in json.dumps(view) and sentinel not in row["prompt"]

        # Compensation and the job description never reached a prompt, from
        # the job, its markdown or the application's validation answers.
        for task_type, messages in router.calls:
            sent = json.dumps(messages)
            assert CTC_SENTINEL not in sent, task_type
            assert JD_SENTINEL not in sent, task_type
        assert {task for task, _ in router.calls} >= {
            "question_generation", "format_composition", "coding_question_generation",
        }
        for record_line in caplog.records:
            for sentinel in SENTINELS:
                assert sentinel not in record_line.getMessage()
    finally:
        await _drop(factory, w)
        await engine.dispose()


async def test_a_redelivered_task_keeps_the_questions_it_already_wrote(monkeypatch) -> None:
    engine, factory = await _factory()
    w = await _seed(factory, title="HR Manager", classification="NON_STEM", per_bucket=(1, 1, 2))
    try:
        router = Router()
        monkeypatch.setattr(llm_router, "invoke_llm", router)
        first = await _generate(factory, w)
        calls = len(router.calls)
        second = await _generate(factory, w)
        assert first.created and not second.created
        assert [row.id for row in second.rows] == [row.id for row in first.rows]
        assert len(router.calls) == calls
    finally:
        await _drop(factory, w)
        await engine.dispose()


# ── No coding question, and the reason recorded ──────────────────────────────


@pytest.mark.parametrize(
    "title,classification,override,reason",
    [
        ("Senior Software Engineer", "STEM", False, budget.CODING_EXCLUDED_DISABLED),
        ("Civil Engineer", "STEM", True, budget.CODING_EXCLUDED_NOT_COMPUTING),
        ("Senior Software Engineer", "NON_STEM", True, budget.CODING_EXCLUDED_NOT_STEM),
    ],
)
async def test_a_role_that_is_not_a_coding_role_is_asked_prose_instead(
    monkeypatch, title, classification, override, reason
) -> None:
    engine, factory = await _factory()
    w = await _seed(factory, title=title, classification=classification, grade="cxo", per_bucket=(2, 1, 2))
    try:
        router = Router()
        monkeypatch.setattr(llm_router, "invoke_llm", router)
        if override:
            with override_provider(FakeProvider()):
                result = await _generate(factory, w)
        else:
            result = await _generate(factory, w)
        rows = await _rows(factory, w)
        assert len(rows) == 8  # cxo floor
        assert not any(row["question_type"] == types.CODING for row in rows)
        assert result.composition["coding"] == {"eligible": False, "excluded_reason": reason}
        assert result.composition["planned"] == {"prose": 7, "coding": 0, "objective": 1}
        assert "coding_question_generation" not in {task for task, _ in router.calls}
    finally:
        await _drop(factory, w)
        await engine.dispose()


async def test_a_sandbox_outage_serves_prose_with_the_reason_and_asks_the_model_once(monkeypatch) -> None:
    engine, factory = await _factory()
    w = await _seed(factory)
    try:
        router = Router([_question(), _question(), _question()])
        monkeypatch.setattr(llm_router, "invoke_llm", router)
        with override_provider(FakeProvider(unavailable="queue_full")):
            result = await _generate(factory, w)
        rows = await _rows(factory, w)
        assert not any(row["question_type"] == types.CODING for row in rows)
        degraded = result.composition["degraded"]
        assert [entry["reason"] for entry in degraded] == [
            coding_generation.REFUSAL_EXECUTION_UNAVAILABLE
        ] * 3
        assert {entry["planned"] for entry in degraded} == {"coding"}
        assert result.composition["planned"] == {"prose": 11, "coding": 3, "objective": 1}
        assert result.composition["served"] == {"prose": 14, "coding": 0, "objective": 1}
        # The outage latched: one coding model call, not one per slot.
        assert [task for task, _ in router.calls].count("coding_question_generation") == 1
    finally:
        await _drop(factory, w)
        await engine.dispose()


async def test_a_model_outage_serves_templates_and_never_records_them_as_generation(monkeypatch) -> None:
    engine, factory = await _factory()
    w = await _seed(factory, title="HR Manager", classification="NON_STEM", per_bucket=(2, 1, 3))
    try:
        router = Router(down={"question_generation", "anchoring", "objective"})
        monkeypatch.setattr(llm_router, "invoke_llm", router)
        result = await _generate(factory, w)
        rows = await _rows(factory, w)
        assert len(rows) == 10
        assert all(row["generated_at"] is None for row in rows)
        assert all(row["question_type"] == types.SHORT_ANSWER for row in rows)
        assert all(re.search(r"skill \d", row["prompt"]) for row in rows)
        record = result.composition
        assert record["templated"] == list(range(1, 11))
        assert [entry["reason"] for entry in record["degraded"]] == ["objective_generation_failed"]
        assert record["unanchored"] and all(
            rows[ordinal - 1]["resume_anchor"] is None for ordinal in record["unanchored"]
        )
    finally:
        await _drop(factory, w)
        await engine.dispose()


# ── Refusals ─────────────────────────────────────────────────────────────────


async def test_an_application_nobody_invited_is_never_given_questions(monkeypatch) -> None:
    engine, factory = await _factory()
    w = await _seed(factory, invited=False)
    try:
        router = Router()
        monkeypatch.setattr(llm_router, "invoke_llm", router)
        with pytest.raises(LookupError):
            await _generate(factory, w)
        assert await _rows(factory, w) == []
        assert router.calls == []
    finally:
        await _drop(factory, w)
        await engine.dispose()


async def test_a_job_whose_skills_are_not_saved_is_refused_before_any_model_call(monkeypatch) -> None:
    engine, factory = await _factory()
    w = await _seed(factory, saved=False)
    try:
        router = Router()
        monkeypatch.setattr(llm_router, "invoke_llm", router)
        with pytest.raises(assessment_contract.ContractNotReady):
            await _generate(factory, w)
        assert await _rows(factory, w) == []
        assert router.calls == []
    finally:
        await _drop(factory, w)
        await engine.dispose()


# ── The record on the invitation (migration 0123, Phase 3 WP3) ───────────────

_STAMP_COLUMNS = {"questions_contract_digest", "questions_contract_version", "composition_json"}


@pytest.mark.xfail(
    not _STAMP_COLUMNS <= set(AssessmentConversation.__table__.c.keys()),
    reason=(
        "the columns arrive with Phase 3 WP3's migration 0123; the orchestrator "
        "removes this marker when the two packages are integrated"
    ),
    strict=True,
)
async def test_the_invitation_records_the_contract_and_the_composition(monkeypatch) -> None:
    engine, factory = await _factory()
    w = await _seed(factory, title="HR Manager", classification="NON_STEM", per_bucket=(1, 1, 2))
    try:
        monkeypatch.setattr(llm_router, "invoke_llm", Router())
        result = await _generate(factory, w)
        async with factory() as session:
            async with superadmin_scope(session):
                stored = (await session.execute(
                    select(AssessmentConversation).where(AssessmentConversation.id == w.conversation)
                )).scalar_one()
                contract = await assessment_contract.load_contract(session, w.job)
        assert stored.questions_contract_digest == contract.digest
        assert stored.questions_contract_version == contract.version
        assert stored.composition_json == result.composition
    finally:
        await _drop(factory, w)
        await engine.dispose()
