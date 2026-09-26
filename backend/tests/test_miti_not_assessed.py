"""A model failure is "Not assessed", bounded by attempts, on REAL rows (WP5-D).

PLAN-p5 P5-D4, the integration half (the unit half is Miti's item stage in
`tests/test_miti_items.py`). A judging outage on every substantive answer:

  1. ATTEMPTS BEFORE THE LAST WRITE NO REPORT. The run returns
     `not_assessed`, the live evaluation row records the status and the
     attempt, and no evaluator is paid for. A fresh connection finds no
     report and no report dimension.
  2. THE FINAL ATTEMPT WRITES THE REPORT. Those skills carry no score and the
     `not_assessed` status, the report is `miti_partial`, routed to a person,
     the overall is withheld when a Must-have is among them, and the ERROR
     alarm token is logged. The evaluation row is still ONE live row, now
     `complete` with the attempt count.
  3. THE TOKEN IS THE ALARM'S. The CloudWatch metric filter in
     `infra/modules/observability` counts exactly the string the orchestrator
     logs; renaming one without the other would stop the count in silence.
"""
from __future__ import annotations

import logging
import pathlib
import re
import uuid

import pytest

from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import superadmin_scope
from app.services import agent_loop
from app.services import functional_assessment as fa
from app.services.assessment_formats import evaluation as format_evaluation
from app.services.miti import grades
from app.services.siddhi import synthesis as siddhi_synthesis
from tests.test_assessment_contract import _drop, _factory, _seed
from tests.test_miti_live_rows import _INSUFFICIENT, _Script, _issue, _second_read
from tests.test_miti_report_rows import _application, _run

ALARM_TOKEN = "miti.not_assessed_final_report"
OBSERVABILITY = (
    pathlib.Path(__file__).resolve().parents[2] / "infra" / "modules" / "observability" / "main.tf"
)


async def _report_route(factory, w):
    """The recruiter's report read, called as the route handler with a staff
    principal of the seeded tenant."""
    from app.api import assessments as assessments_mod
    from app.api.deps import CurrentUser
    from app.core.security import AUDIENCE_ORG
    from app.models import Role

    user = CurrentUser(
        user_id=uuid.uuid4(), tenant_id=w.tenant, role=Role.client, audience=AUDIENCE_ORG
    )
    async with factory() as session:
        async with session.begin():
            async with superadmin_scope(session):
                return await assessments_mod.get_report(
                    link_id=w.links[0], user=user, session=session
                )


async def _counts(factory, link_id) -> tuple[int, int]:
    reports = await _second_read(
        factory,
        "SELECT count(*) FROM functional_skills_reports WHERE job_candidate_link_id = :l",
        l=link_id,
    )
    dimensions = await _second_read(
        factory,
        "SELECT count(*) FROM report_dimensions d JOIN functional_skills_reports r "
        "ON r.id = d.report_id WHERE r.job_candidate_link_id = :l",
        l=link_id,
    )
    return reports[0][0], dimensions[0][0]


@pytest.mark.asyncio
async def test_an_outage_retries_without_a_report_then_writes_not_assessed(
    monkeypatch, caplog
) -> None:
    attempts_allowed = get_settings().miti_not_assessed_attempts
    assert attempts_allowed >= 2, "the test needs at least one retry before the last"
    engine, factory = await _factory()
    w = await _seed(factory)
    try:
        await _issue(factory, w)
        await _application(factory, w)

        async def _degraded(session, **kwargs):
            return agent_loop.LoopResult(value=None, degraded=True, error="TimeoutError")

        monkeypatch.setattr(format_evaluation, "evaluate", _degraded)

        # 1. Every attempt before the last: no report, the attempt recorded.
        for attempt in range(1, attempts_allowed):
            evaluators = _Script(_INSUFFICIENT)
            result, miti = await _run(
                factory, w, monkeypatch,
                judge=_Script(TimeoutError("provider down")),
                evaluators=evaluators,
            )
            assert (result.status, result.attempts, result.report_id) == (
                "not_assessed", attempt, None,
            )
            assert not miti.complete
            assert evaluators.calls == [], "no evaluator is paid for an incomplete run"
            assert await _counts(factory, w.links[0]) == (0, 0)
            live = await _second_read(
                factory,
                "SELECT status, attempts, report_id FROM evaluations "
                "WHERE link_id = :l AND superseded_at IS NULL",
                l=w.links[0],
            )
            assert live == [("not_assessed", attempt, None)]

        # The job's LIVE grade moves after the contract locked it: the report
        # must state the grade the candidate was assessed at, the contract's.
        async with factory() as session:
            async with superadmin_scope(session):
                await session.execute(
                    text(
                        "UPDATE jobs SET assessment_grade = CASE WHEN assessment_grade = 'cxo' "
                        "THEN 'non_managerial' ELSE 'cxo' END WHERE id = :j"
                    ),
                    {"j": w.job},
                )
                await session.commit()

        # 2. The final attempt writes the report, with the skills Not assessed.
        caplog.set_level(logging.ERROR, logger=fa.__name__)
        result, miti = await _run(
            factory, w, monkeypatch,
            judge=_Script(TimeoutError("provider down")),
            evaluators=_Script(_INSUFFICIENT),
        )
        assert (result.status, result.attempts) == ("written", attempts_allowed)
        not_assessed = {grade.name for grade in miti.skills if grade.status == grades.ANSWER_NOT_ASSESSED}
        assert not_assessed, "the outage really left skills not assessed"

        report = await _second_read(
            factory,
            "SELECT scoring_mode, needs_human_review, overall_status, overall_score, "
            "review_findings_json, grade FROM functional_skills_reports "
            "WHERE job_candidate_link_id = :l",
            l=w.links[0],
        )
        mode, review, overall_status, overall_score, findings, stated_grade = report[0]
        live_grade = await _second_read(
            factory, "SELECT assessment_grade FROM jobs WHERE id = :j", j=w.job
        )
        assert stated_grade == miti.contract.grade, "the locked grade, never the live one"
        assert stated_grade != live_grade[0][0]
        assert mode == "miti_partial"
        assert review is True
        if any(
            grade.bucket == "must_have" and grade.status == grades.ANSWER_NOT_ASSESSED
            for grade in miti.skills
        ):
            assert (overall_status, overall_score) == ("not_assessed", None)
        assert {finding["issue"] for finding in findings} >= {"not_assessed"}

        rows = await _second_read(
            factory,
            "SELECT d.name, d.score, d.assessment_status, d.remark_provenance "
            "FROM report_dimensions d JOIN functional_skills_reports r ON r.id = d.report_id "
            "WHERE r.job_candidate_link_id = :l",
            l=w.links[0],
        )
        stated = {name: (score, status, source) for name, score, status, source in rows}
        for name in not_assessed:
            assert stated[name] == (None, "not_assessed", "catalogue"), name

        # The report ROUTE states it in words: a missing score projected as a
        # grade would read Not Matching, a verdict nobody reached.
        out = await _report_route(factory, w)
        route_grades = {
            row.name: row.grade
            for section in (out.must_have, out.nice_to_have, out.behavioural)
            for row in section
        }
        for name in not_assessed:
            assert route_grades[name] == siddhi_synthesis.NOT_ASSESSED_WORD, name
        if overall_status == "not_assessed":
            assert out.overall_grade == siddhi_synthesis.NOT_ASSESSED_WORD

        evaluations = await _second_read(
            factory,
            "SELECT status, attempts, report_id, scoring_mode FROM evaluations WHERE link_id = :l",
            l=w.links[0],
        )
        assert len(evaluations) == 1, "one live row, upserted, never a second row"
        assert evaluations[0][:2] == ("complete", attempts_allowed)
        assert str(evaluations[0][2]) == str(result.report_id)
        assert evaluations[0][3] == "degraded"

        assert any(ALARM_TOKEN in record.getMessage() for record in caplog.records)
    finally:
        await _drop(factory, w)
        await engine.dispose()


def test_the_alarm_counts_the_token_the_orchestrator_logs() -> None:
    import inspect

    assert ALARM_TOKEN in inspect.getsource(fa.run_assessment)
    terraform = OBSERVABILITY.read_text(encoding="utf-8")
    pattern = re.search(
        r'resource "aws_cloudwatch_log_metric_filter" "miti_not_assessed_final" \{.*?pattern\s*=\s*"(.*?)"\n',
        terraform,
        re.S,
    )
    assert pattern, "the metric filter is missing"
    assert ALARM_TOKEN in pattern.group(1).replace('\\"', "")
    assert 'resource "aws_cloudwatch_metric_alarm" "miti_not_assessed_final"' in terraform
