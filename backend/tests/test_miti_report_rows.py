"""The orchestrator writes what Miti concluded, on REAL rows, read back from a
SECOND connection (WP5-B).

`test_miti_live_rows.py` proves Miti's own writes survive the commit. This file
proves the next step does: `functional_assessment.run_assessment`, the one
entry the scoring task calls, persists Miti's grades as the report and Miti's
working as the evaluation, and persists NOTHING when a skill could not be
assessed.

  1. ONE JUDGE. Every assessed report row carries exactly the score Miti's
     item stage gave that skill, one row per contract skill, and the stored overall is
     Miti's `stated_score`. The evaluation row carries the locked contract's
     version, the per-skill statuses and all four gates, and passes the
     `evaluations.scoring_mode` CHECK that the report's own mode string could
     never have passed.
  2. NOT ASSESSED WRITES NOTHING. A judging outage raises `SkillsNotAssessed`,
     the transaction is rolled back, and a fresh connection finds no report,
     no report dimension and no evaluation for the application.

The item judge, the five evaluators and the evidence evaluation are scripted
at their injection points. Remarks and probes run with no model credential, so
they take their recorded template paths; nothing reaches a provider.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app.core.db import superadmin_scope
from app.models import Job, JobCandidateLink
from app.services import agent_loop
from app.services import functional_assessment as fa
from app.services.assessment_formats import evaluation as format_evaluation
from app.services.hiring import gates
from app.services.miti import grades
from app.services.miti import live as miti_live
from tests.test_assessment_contract import _drop, _factory, _seed
from tests.test_miti_live_rows import (
    _ANSWERS,
    _EVIDENCE_VALUE,
    _INSUFFICIENT,
    _Script,
    _issue,
    _second_read,
)


async def _application(factory, w) -> None:
    """The contract suite seeds `source='manual'`, a value the ORM's
    `LinkSource` enum does not carry. It never loads the link as an ORM row;
    `run_assessment` does, so the row is given a value the model can read."""
    async with factory() as session:
        async with superadmin_scope(session):
            await session.execute(
                text("UPDATE job_candidate_links SET source = 'fresh' WHERE id = :l"),
                {"l": w.links[0]},
            )
            await session.commit()


async def _run(factory, w, monkeypatch, *, judge, evaluators):
    """Scoring as the task runs it: one transaction, committed on success."""
    captured: dict = {}
    real = miti_live.evaluate_application

    async def _evaluate(session, **kwargs):
        result = await real(session, **kwargs, invoke=evaluators, item_invoke=judge)
        captured["miti"] = result
        return result

    monkeypatch.setattr(miti_live, "evaluate_application", _evaluate)
    async with factory() as session:
        async with superadmin_scope(session):
            job = await session.get(Job, w.job)
            link = await session.get(JobCandidateLink, w.links[0])
            try:
                report_id = await fa.run_assessment(session, job, link)
            except BaseException:
                await session.rollback()
                raise
            await session.commit()
    return report_id, captured.get("miti")


@pytest.mark.asyncio
async def test_the_report_carries_mitis_grades_and_nothing_else(monkeypatch) -> None:
    engine, factory = await _factory()
    w = await _seed(factory)
    try:
        await _issue(factory, w)
        await _application(factory, w)

        async def _evaluation(session, **kwargs):
            return agent_loop.LoopResult(value=dict(_EVIDENCE_VALUE), degraded=False)

        monkeypatch.setattr(format_evaluation, "evaluate", _evaluation)
        report_id, miti = await _run(
            factory, w, monkeypatch,
            judge=_Script('{"score": 82, "band": "75_89"}'),
            evaluators=_Script(_INSUFFICIENT),
        )
        assert miti is not None and miti.complete

        # 1. Every PPI report row IS Miti's grade for that skill, one per skill.
        rows = await _second_read(
            factory,
            "SELECT d.name, d.category, d.score FROM report_dimensions d "
            "JOIN functional_skills_reports r ON r.id = d.report_id "
            "WHERE r.job_candidate_link_id = :l AND d.category <> :m "
            "ORDER BY d.category, d.ordinal",
            l=w.links[0], m=fa.CATEGORY_MATCHING,
        )
        by_name = {grade.name: grade for grade in miti.skills}
        assert {row[0] for row in rows} == set(by_name)
        for name, category, score in rows:
            assert category == by_name[name].bucket, name
            assert score == by_name[name].score, name
        assert by_name["Terraform"].status == grades.ANSWER_UNANSWERED
        assert by_name["Kafka"].score == 100

        report = (await _second_read(
            factory,
            "SELECT id, overall_score, scoring_mode, needs_human_review "
            "FROM functional_skills_reports WHERE job_candidate_link_id = :l",
            l=w.links[0],
        ))
        assert len(report) == 1
        stored_id, overall, mode, review = report[0]
        assert str(stored_id) == report_id
        assert overall == int(round(miti.aggregate.stated_score))
        assert mode == fa.MODE_MITI
        # Five insufficient evaluators fail G2: the report is written and
        # routed to a person, never withheld.
        assert review is True

        evaluation = await _second_read(
            factory,
            "SELECT scoring_mode, scorecard_version, competency_scores, "
            "gate_results_json, report_id FROM evaluations WHERE link_id = :l",
            l=w.links[0],
        )
        assert len(evaluation) == 1
        scoring_mode, version, competency_scores, gate_results, linked = evaluation[0]
        assert scoring_mode == "full", "the CHECK admits full | degraded | stub"
        assert version == miti.contract_version
        assert str(linked) == report_id
        assert {
            name: entry["status"] for name, entry in competency_scores.items()
        } == {grade.name: grade.status for grade in miti.skills}
        assert [gate["gate"] for gate in gate_results] == [gates.G1, gates.G2, gates.G3, gates.G4]
    finally:
        await _drop(factory, w)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_judging_outage_commits_no_report_and_no_evaluation(monkeypatch) -> None:
    engine, factory = await _factory()
    w = await _seed(factory)
    try:
        await _issue(factory, w)
        await _application(factory, w)

        async def _degraded(session, **kwargs):
            return agent_loop.LoopResult(value=None, degraded=True, error="TimeoutError")

        monkeypatch.setattr(format_evaluation, "evaluate", _degraded)
        evaluators = _Script(_INSUFFICIENT)
        with pytest.raises(fa.SkillsNotAssessed):
            await _run(
                factory, w, monkeypatch,
                judge=_Script(TimeoutError("provider down")),
                evaluators=evaluators,
            )
        assert evaluators.calls == [], "no evaluator is paid for an incomplete run"

        for table, column in (
            ("functional_skills_reports", "job_candidate_link_id"),
            ("evaluations", "link_id"),
        ):
            rows = await _second_read(
                factory, f"SELECT count(*) FROM {table} WHERE {column} = :l", l=w.links[0],
            )
            assert rows[0][0] == 0, table
        dimensions = await _second_read(
            factory,
            "SELECT count(*) FROM report_dimensions d JOIN functional_skills_reports r "
            "ON r.id = d.report_id WHERE r.job_candidate_link_id = :l",
            l=w.links[0],
        )
        assert dimensions[0][0] == 0
        assert _ANSWERS  # the world really had answers to grade
    finally:
        await _drop(factory, w)
        await engine.dispose()
