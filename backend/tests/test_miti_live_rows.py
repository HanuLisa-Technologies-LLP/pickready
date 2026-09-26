"""Miti end to end on REAL rows, read back from a SECOND connection (WP5-B).

The unit suites prove each rule over values. This file proves the rules
survive the database: a locked contract bound to a real conversation, the
candidate's own `candidate_questions` rows with the rubric written with each,
real transcript messages, real `assessment_answers`, and the ledger and the
evaluation record Miti writes, COMMITTED and then read by a different
connection. An assertion on the returned objects cannot see a write that
vanished at commit, and this repository has shipped exactly that.

  1. THE STORED RUBRIC. The rubric in the judging prompt is the one stored on
     the question row, read back independently.
  2. MITI GRADES EVERY SKILL, BY FORMAT. Objective from `auto_score`, evidence
     from the evaluation with reasoning (persisted), prose from the stored
     rubric, Behavioural from the standard, gibberish unanswered. The ledger
     files what was read, and nothing for a non-answer.
  3. AN OUTAGE IS NOT A SCORE. Every model-backed skill not assessed, with no
     score and no grade, the evaluators never paid for, no reasoning stored;
     the deterministic skills still graded.
  4. G4 TAKES THE RECORDED DISPOSITION. A flagged run fails G4 with no
     disposition and passes it once a person has decided, through the whole
     live path rather than through the read helper alone.

Model calls are scripted at the injection points; nothing reaches a provider.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.core.db import superadmin_scope
from app.services import agent_loop, rating
from app.services import functional_assessment as fa
from app.services import ppi_interview
from app.services.assessment_formats import evaluation as format_evaluation
from app.services.assessment_pipeline import evidence as answer_evidence
from app.services.hiring import gates
from app.services.miti import grades, items, live
from tests.test_assessment_contract import _drop, _factory, _lock, _seed

_SQL_RUBRIC = {
    "0_39": "Cannot say what a query plan is.",
    "60_74": "Reads a plan and names the slow node.",
    "90_100": "Rewrote a join order from the plan and measured the change.",
}
_ANSWERS = {
    "SQL": "I read the plan, saw a sequential scan on orders and added a covering index.",
    "Python": "I moved the CPU bound parsing to a process pool after profiling the GIL.",
    "Kafka": "Selected: A write-ahead log",
    "Terraform": "ewidjverip",
    "Ownership": "I took the migration from an unclear brief to a shipped cutover myself.",
}
_EVIDENCE_VALUE = {
    "score": 77,
    "criteria": {},
    "reasoning": "The answer names the profiling step and the pool it chose.",
    "citations": [],
    "rubric": {},
}


class _Script:
    """A scripted model at one injection point. Records every call."""

    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[tuple[str, list[dict[str, str]]]] = []

    async def __call__(self, task, messages, **kwargs):
        self.calls.append((task, messages))
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


_INSUFFICIENT = json.dumps({"band": "partial", "insufficient_evidence": True})


async def _issue(factory, w) -> dict[str, uuid.UUID]:
    """Lock the contract, issue one question per skill, record the answers."""
    conversation, link = w.conversations[0], w.links[0]
    await _lock(factory, w, conversation)
    questions: dict[str, uuid.UUID] = {}
    formats = {
        "SQL": ("short_answer", _SQL_RUBRIC),
        "Python": ("evidence_based", {"0_39": "none", "90_100": "profiled and chose"}),
        "Kafka": ("mcq_single", None),
        "Terraform": ("short_answer", {"0_39": "none", "90_100": "module used by others"}),
        "Ownership": ("short_answer", None),
    }
    async with factory() as session:
        async with superadmin_scope(session):
            for ordinal, (name, (question_type, rubric)) in enumerate(formats.items(), start=1):
                question = uuid.uuid4()
                questions[name] = question
                await session.execute(
                    text(
                        "INSERT INTO candidate_questions (id, tenant_id, job_id, "
                        "job_candidate_link_id, competency_id, ordinal, prompt, rubric_json, "
                        "question_type, payload_json, weight) VALUES (:i, :t, :j, :l, :c, :o, "
                        ":p, CAST(:r AS jsonb), :qt, CAST(:pl AS jsonb), 1.0)"
                    ),
                    {"i": question, "t": w.tenant, "j": w.job, "l": link,
                     "c": w.skills[name], "o": ordinal, "p": f"Tell me about {name}.",
                     "r": json.dumps(rubric) if rubric else None, "qt": question_type,
                     "pl": json.dumps(
                         {"options": [{"id": "a", "text": "A write-ahead log"}],
                          "correct_option_ids": ["a"]}
                         if question_type == "mcq_single" else {}
                     )},
                )
                message = uuid.uuid4()
                await session.execute(
                    text(
                        "INSERT INTO assessment_messages (id, tenant_id, conversation_id, "
                        "ordinal, speaker, domain, question_key, content, evidence_gap) "
                        "VALUES (:i, :t, :c, :o, 'candidate', 'technical', :q, :content, false)"
                    ),
                    {"i": message, "t": w.tenant, "c": conversation, "o": ordinal,
                     "q": str(question), "content": _ANSWERS[name]},
                )
                if question_type in ("mcq_single", "evidence_based"):
                    await session.execute(
                        text(
                            "INSERT INTO assessment_answers (id, tenant_id, conversation_id, "
                            "question_id, message_id, question_type, answer_json, "
                            "submitted_at, auto_score) VALUES (:i, :t, :c, :q, :m, :qt, "
                            "CAST(:a AS jsonb), :at, :s)"
                        ),
                        {"i": uuid.uuid4(), "t": w.tenant, "c": conversation, "q": question,
                         "m": message, "qt": question_type,
                         "a": json.dumps(
                             {"selected_option_id": "a"} if question_type == "mcq_single"
                             else {"text": _ANSWERS[name]}
                         ),
                         "at": datetime.now(timezone.utc),
                         "s": 1.0 if question_type == "mcq_single" else None},
                    )
            await session.commit()
    return questions


async def _grade(factory, w, *, judge, evaluators):
    """Run Miti through the live entry point on the committed rows, and commit."""
    conversation, link_id = w.conversations[0], w.links[0]
    link = SimpleNamespace(id=link_id, candidate_id=w.candidates[0])
    async with factory() as session:
        async with superadmin_scope(session):
            questions = await ppi_interview.load_for_link(session, link_id)
            locators = await answer_evidence.answer_records(session, link_id)
            answers = {key: [record.text for record in records] for key, records in locators.items()}
            structured = await fa._structured_answers(session, link)
            result = await live.evaluate_application(
                session,
                job=SimpleNamespace(id=w.job, tenant_id=w.tenant, title="Data Engineer"),
                link=link,
                conversation_id=conversation,
                questions=questions,
                answers=answers,
                locators=locators,
                structured=structured,
                subject_names=("Contract", "Subject"),
                invoke=evaluators,
                item_invoke=judge,
            )
            await session.commit()
    return result


async def _second_read(factory, sql: str, **params):
    """A FRESH connection, after the commit, so a write that rolled back shows."""
    async with factory() as session:
        async with superadmin_scope(session):
            return (await session.execute(text(sql), params)).all()


def _by_name(result) -> dict[str, grades.SkillGrade]:
    return {grade.name: grade for grade in result.skills}


# ── 1 and 2 ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_miti_grades_every_skill_on_real_rows_and_the_writes_are_committed(monkeypatch) -> None:
    engine, factory = await _factory()
    w = await _seed(factory)
    try:
        questions = await _issue(factory, w)

        async def _evaluation(session, **kwargs):
            return agent_loop.LoopResult(value=dict(_EVIDENCE_VALUE), degraded=False)

        monkeypatch.setattr(format_evaluation, "evaluate", _evaluation)
        judge = _Script('{"score": 82, "band": "75_89"}')
        evaluators = _Script(_INSUFFICIENT)
        result = await _grade(factory, w, judge=judge, evaluators=evaluators)

        by_name = _by_name(result)
        assert [grade.name for grade in result.skills] == [
            skill.name for skill in result.contract.skills
        ], "one grade per contract skill, in contract order"
        assert by_name["Kafka"].score == 100
        assert by_name["Kafka"].items[0].method == grades.METHOD_OBJECTIVE
        assert by_name["Python"].score == 77
        assert by_name["Python"].items[0].method == grades.METHOD_EVIDENCE
        assert by_name["SQL"].score == 82
        assert by_name["SQL"].items[0].method == grades.METHOD_RUBRIC
        assert by_name["Ownership"].items[0].method == grades.METHOD_BEHAVIOURAL_STANDARD
        assert by_name["Terraform"].status == grades.ANSWER_UNANSWERED
        assert by_name["Terraform"].grade == rating.GRADE_NOT
        assert result.complete and result.outcome is not None
        assert {task for task, _messages in judge.calls} == {items.EVALUATION_TASK}
        assert len(evaluators.calls) == 5

        # 1. THE STORED RUBRIC, read back from the row, is in the judging prompt.
        stored = (await _second_read(
            factory, "SELECT rubric_json FROM candidate_questions WHERE id = :q",
            q=questions["SQL"],
        ))[0][0]
        sql_prompt = next(
            messages[0]["content"] for _task, messages in judge.calls
            if json.loads(messages[1]["content"])["question"] == "Tell me about SQL."
        )
        for band_text in stored.values():
            assert band_text in sql_prompt

        # The evaluation with reasoning is PERSISTED on the answer row.
        persisted = await _second_read(
            factory,
            "SELECT ai_evaluation_json FROM assessment_answers WHERE question_id = :q",
            q=questions["Python"],
        )
        assert persisted[0][0]["reasoning"] == _EVIDENCE_VALUE["reasoning"]

        # The ledger filed every substantive answer Miti READ, and none of the
        # gibberish; the objective answer is settled from `auto_score` and
        # reads no prose.
        filed = await _second_read(
            factory,
            "SELECT m.content FROM evidence_items e JOIN assessment_messages m "
            "ON m.id = e.source_id WHERE e.link_id = :l",
            l=w.links[0],
        )
        filed_text = {row[0] for row in filed}
        assert filed_text == {_ANSWERS["SQL"], _ANSWERS["Python"], _ANSWERS["Ownership"]}
    finally:
        await _drop(factory, w)
        await engine.dispose()


# ── 3 ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_model_outage_on_real_rows_writes_no_score_and_pays_for_no_evaluator(
    monkeypatch, caplog
) -> None:
    engine, factory = await _factory()
    w = await _seed(factory)
    try:
        questions = await _issue(factory, w)

        async def _degraded(session, **kwargs):
            return agent_loop.LoopResult(value=None, degraded=True, error="TimeoutError")

        monkeypatch.setattr(format_evaluation, "evaluate", _degraded)
        caplog.set_level(logging.WARNING, logger=items.__name__)
        judge = _Script(TimeoutError("provider down"))
        evaluators = _Script(_INSUFFICIENT)
        result = await _grade(factory, w, judge=judge, evaluators=evaluators)

        by_name = _by_name(result)
        for name in ("SQL", "Python", "Ownership"):
            grade = by_name[name]
            assert grade.status == grades.ANSWER_NOT_ASSESSED, name
            assert grade.score is None and grade.grade is None, name
            assert all(item.score is None for item in grade.items), name
            assert grade.items[0].failure in ("TimeoutError", items.FAILURE_EVALUATION_DEGRADED)
        # The deterministic skills are still what the candidate did.
        assert by_name["Kafka"].score == 100
        assert by_name["Terraform"].status == grades.ANSWER_UNANSWERED
        # Not assessed is not failed, so this run cannot cap anybody.
        assert not result.must_have_failed
        assert not result.complete and result.outcome is None
        assert evaluators.calls == [], "the evaluators are not paid for and thrown away"
        assert result.model_calls() == ()
        assert any(
            record.getMessage().startswith("miti.item_not_assessed")
            for record in caplog.records
        )

        persisted = await _second_read(
            factory,
            "SELECT ai_evaluation_json FROM assessment_answers WHERE question_id = :q",
            q=questions["Python"],
        )
        assert persisted[0][0] is None, "a degraded evaluation stores nothing"

        # EVIDENCE IS WHAT WAS READ, A GRADE IS WHAT WAS CONCLUDED FROM IT. The
        # outage cost the conclusions, not the trail: every substantive answer
        # the judge was about to read is filed and COMMITTED, and the
        # gibberish still is not.
        filed = await _second_read(
            factory,
            "SELECT m.content FROM evidence_items e JOIN assessment_messages m "
            "ON m.id = e.source_id WHERE e.link_id = :l",
            l=w.links[0],
        )
        assert {row[0] for row in filed} == {
            _ANSWERS["SQL"], _ANSWERS["Python"], _ANSWERS["Ownership"],
        }
        # And nothing Miti wrote anywhere carries a score for the outage: no
        # evaluation row, no report row exists for this application.
        for table, column in (
            ("evaluations", "link_id"),
            ("functional_skills_reports", "job_candidate_link_id"),
        ):
            rows = await _second_read(
                factory, f"SELECT count(*) FROM {table} WHERE {column} = :l", l=w.links[0],
            )
            assert rows[0][0] == 0, table
    finally:
        await _drop(factory, w)
        await engine.dispose()


# ── 4 ────────────────────────────────────────────────────────────────────────


async def _record_disposition(factory, w) -> uuid.UUID:
    reviewer = uuid.uuid4()
    async with factory() as session:
        async with superadmin_scope(session):
            await session.execute(
                text(
                    "INSERT INTO users (id, tenant_id, email, full_name, role, status) "
                    "VALUES (:u, :t, :e, 'Reviewing Person', 'client', 'active')"
                ),
                {"u": reviewer, "t": w.tenant, "e": f"{reviewer.hex[:10]}@miti.test"},
            )
            await session.execute(
                text(
                    "INSERT INTO review_dispositions (id, tenant_id, job_id, link_id, "
                    "disposition, decided_by, flags_json) VALUES "
                    "(:i, :t, :j, :l, :d, :by, '[]'::jsonb)"
                ),
                {"i": uuid.uuid4(), "t": w.tenant, "j": w.job, "l": w.links[0],
                 "d": gates.DISPOSITION_CLEARED, "by": reviewer},
            )
            await session.commit()
    return reviewer


async def _clear_dispositions(factory, w) -> None:
    async with factory() as session:
        async with superadmin_scope(session):
            await session.execute(
                text("DELETE FROM review_dispositions WHERE link_id = :l"), {"l": w.links[0]}
            )
            await session.commit()


def _g4(result) -> gates.GateResult:
    return next(gate for gate in result.outcome.gate_results if gate.gate == gates.G4)


@pytest.mark.asyncio
async def test_a_recorded_disposition_reaches_g4_through_the_live_path(monkeypatch) -> None:
    engine, factory = await _factory()
    w = await _seed(factory)
    try:
        await _issue(factory, w)

        async def _evaluation(session, **kwargs):
            return agent_loop.LoopResult(value=dict(_EVIDENCE_VALUE), degraded=False)

        monkeypatch.setattr(format_evaluation, "evaluate", _evaluation)

        # Five insufficient evaluators fail G2, which routes the run to a person.
        flagged = await _grade(
            factory, w, judge=_Script('{"score": 82}'), evaluators=_Script(_INSUFFICIENT)
        )
        assert flagged.aggregate.needs_human_review
        assert not _g4(flagged).passed
        assert not flagged.outcome.deliverable

        await _record_disposition(factory, w)
        decided = await _grade(
            factory, w, judge=_Script('{"score": 82}'), evaluators=_Script(_INSUFFICIENT)
        )
        assert decided.aggregate.needs_human_review, "the flag stands; a person decided on it"
        assert _g4(decided).passed
        assert decided.outcome.deliverable
    finally:
        await _clear_dispositions(factory, w)
        await _drop(factory, w)
        await engine.dispose()
