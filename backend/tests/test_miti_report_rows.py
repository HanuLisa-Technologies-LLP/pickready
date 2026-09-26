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
  2. NOT ASSESSED WRITES NO REPORT. Since WP5-D a judging outage is not an
     exception: the run records the attempt on the evaluation row and writes
     no report, and the final attempt writes the skills "Not assessed"
     (`tests/test_miti_not_assessed.py` runs all three attempts on these
     rows).

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
    _EVIDENCE_VALUE,
    _INSUFFICIENT,
    _Script,
    _issue,
    _second_read,
)


#: Captured ONCE at import: a test that runs scoring several times patches
#: the entry point on every run, and wrapping the previous wrapper would hand
#: Miti the scripted models twice.
_REAL_EVALUATE = miti_live.evaluate_application


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
    real = _REAL_EVALUATE

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
                result = await fa.run_assessment(session, job, link)
            except BaseException:
                await session.rollback()
                raise
            await session.commit()
    return result, captured.get("miti")


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
        result, miti = await _run(
            factory, w, monkeypatch,
            judge=_Script('{"score": 82, "band": "75_89"}'),
            evaluators=_Script(_INSUFFICIENT),
        )
        assert miti is not None and miti.complete
        report_id = str(result.report_id)
        assert (result.status, result.attempts) == ("written", 1)

        # The transcript was indexed INLINE by the scoring run, before Miti
        # read related passages from it (PLAN-p5 3.6): nothing else in this
        # test indexes it, and the dispatched indexer never runs under record.
        chunks = await _second_read(
            factory,
            "SELECT count(*) FROM context_chunks WHERE source_type = 'assessment' "
            "AND source_id = :l",
            l=w.links[0],
        )
        assert chunks[0][0] > 0, "the scoring run indexed the transcript it grades"

        # 0. No AI Score row is written any more: that section is Yukti's
        # frozen snapshot on the report row (WP5-D).
        matching = await _second_read(
            factory,
            "SELECT count(*) FROM report_dimensions d JOIN functional_skills_reports r "
            "ON r.id = d.report_id WHERE r.job_candidate_link_id = :l AND d.category = :m",
            l=w.links[0], m=fa.CATEGORY_MATCHING,
        )
        assert matching[0][0] == 0

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
            "SELECT id, overall_score, scoring_mode, needs_human_review, "
            "must_have_failed, overall_status, contract_version, contract_digest, "
            "generation_provenance_json, category_grades_json "
            "FROM functional_skills_reports WHERE job_candidate_link_id = :l",
            l=w.links[0],
        ))
        assert len(report) == 1
        (stored_id, overall, mode, review, must_have_failed, overall_status,
         contract_version, contract_digest, provenance, category_grades) = report[0]
        assert str(stored_id) == report_id
        assert overall == int(round(miti.aggregate.stated_score))
        assert mode == fa.MODE_MITI
        # THE ONE PREDICATE, written on insert, and the contract copied.
        assert must_have_failed is miti.must_have_failed
        assert overall_status == miti.aggregate.overall_status
        assert (contract_version, contract_digest) == (miti.contract_version, miti.contract_digest)
        assert set(provenance) == {"models", "prompts", "templates"}
        assert category_grades == dict(miti.aggregate.category_grades)
        # Five insufficient evaluators fail G2: the report is written and
        # routed to a person, never withheld.
        assert review is True

        evaluation = await _second_read(
            factory,
            "SELECT scoring_mode, scorecard_version, competency_scores, "
            "gate_results_json, report_id, status, attempts, contract_digest "
            "FROM evaluations WHERE link_id = :l",
            l=w.links[0],
        )
        assert len(evaluation) == 1
        (scoring_mode, version, competency_scores, gate_results, linked,
         status, attempts, digest) = evaluation[0]
        assert (status, attempts, digest) == ("complete", 1, miti.contract_digest)
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
