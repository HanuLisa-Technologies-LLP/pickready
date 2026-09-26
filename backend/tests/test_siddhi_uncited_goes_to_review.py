"""An uncited statement goes to a PERSON with an alert; it no longer fails the task.

PLAN-p5 P5-D8. Before the Vivekium release the composer rendered in the
raising mode, so one uncited statement failed the whole scoring task, the task
retried twice, and the candidate who had finished the assessment got no report
at all. These tests pin the replacement, at the composer:

  * the statement is WITHHELD: absent from the rendered sections and from the
    persisted trail (nothing uncited is ever rendered as cited);
  * the composed report is still produced and says it needs human review;
  * the finding names a place and never the sentence;
  * the ERROR line the CloudWatch metric filter counts is emitted, with the
    exact event token the filter matches.
"""
from __future__ import annotations

import json
import logging

import pytest

from app.services.siddhi import citations
from app.services.siddhi import report as siddhi_report
from app.services.siddhi import synthesis

#: The metric filter pattern in infra/modules/observability matches this
#: token. If it changes, the alarm silently stops counting.
FILTER_TOKEN = "prism.statement_withheld_for_review"

SECRET = "an uncitable sentence nobody may read"


def _rows() -> list[dict]:
    return [
        {
            "category": "must_have",
            "name": "Distributed Systems",
            "grade": "Matching",
            "remark": "Owned the orders migration and wrote the rollback runbook.",
        }
    ]


def _exchanges() -> dict:
    return {
        "Distributed Systems": [
            {
                "question": "Walk me through a migration you owned.",
                "answer": "I moved the orders service and wrote the rollback runbook.",
                "question_id": "3f1c3c9e-9d1f-4c1b-8f55-3f6f7a1b2c3d",
                "message_ids": ["8a0d8f8e-6f2b-4a52-9f0c-2a4e1b7c9d10"],
            }
        ]
    }


def _gap_groups_with_an_uncited_probe() -> list[dict]:
    """A gap entry for an item the report does not rate, in a group with no
    aspect: its probe has neither an item record nor an aspect record to cite,
    which is the forced-uncited case the plan names."""
    return [
        {
            "category": None,
            "label": "Unfiled",
            "items": [
                {
                    "name": "Not On This Report",
                    "grade": "Not Matching",
                    "remark": None,
                    "probes": [SECRET],
                    "probes_source": "model",
                }
            ],
            "no_gaps_statement": None,
            "cap_statement": None,
        }
    ]


async def _compose(**kwargs):
    return await siddhi_report.compose_prism(
        dimensions=_rows(),
        evidence_by_item=_exchanges(),
        embed=None,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_a_forced_uncited_probe_is_withheld_and_the_report_still_composes(
    caplog,
) -> None:
    with caplog.at_level(logging.ERROR):
        composed = await _compose(gap_groups=_gap_groups_with_an_uncited_probe())

    rendered = json.dumps(composed.sections)
    assert SECRET not in rendered
    assert SECRET not in json.dumps(composed.trail())
    # The entry's grade line and its probe both have nothing to cite, and both
    # are withheld; nothing else in the report is.
    assert [held.as_dict() for held in composed.withheld] == [
        {
            "section": "gap_analysis",
            "kind": kind,
            "item": "Not On This Report",
            "problem": citations.PROBLEM_NO_CITATION,
        }
        for kind in (citations.KIND_GRADE, citations.KIND_PROBE)
    ]
    # The rest of the report is intact: the rated line still renders.
    assert any(
        statement["text"] == "Distributed Systems: Matching"
        for _, statement in composed.statements()
    )
    assert composed.needs_human_review is True

    findings = composed.review_findings()
    assert [finding["issue"] for finding in findings] == ["uncited_statement"] * 2
    assert {finding["location"] for finding in findings} == {
        "report.gap_analysis.Not On This Report"
    }
    assert SECRET not in json.dumps(findings)

    alerts = [
        record for record in caplog.records
        if record.levelno == logging.ERROR and FILTER_TOKEN in record.getMessage()
    ]
    assert len(alerts) == 2
    assert all(SECRET not in alert.getMessage() for alert in alerts)


def test_the_log_event_is_the_token_the_alarm_matches() -> None:
    assert siddhi_report.WITHHELD_LOG_EVENT == FILTER_TOKEN


@pytest.mark.asyncio
async def test_a_clean_report_is_not_sent_to_review_by_the_citation_rule() -> None:
    """The review flag has to mean something: a report with nothing withheld,
    nothing unsupported and no template text is not flagged by the composer."""
    composed = await _compose()
    assert composed.withheld == ()
    assert composed.templates == ()
    assert not composed.unsupported()
    assert composed.needs_human_review is False


@pytest.mark.asyncio
async def test_the_trail_carries_locators_and_support_and_never_the_transcript() -> None:
    composed = await _compose()
    trail = composed.trail()
    assert trail["version"] == siddhi_report.TRAIL_VERSION
    answers = [node for node in trail["evidence_nodes"] if node["kind"] == "answer"]
    assert answers[0]["locators"] == [
        "assessment_messages:8a0d8f8e-6f2b-4a52-9f0c-2a4e1b7c9d10"
    ]
    questions = [node for node in trail["evidence_nodes"] if node["kind"] == "question"]
    assert questions[0]["locators"] == [
        "candidate_questions:3f1c3c9e-9d1f-4c1b-8f55-3f6f7a1b2c3d"
    ]
    assert "rollback runbook myself" not in json.dumps(trail["evidence_nodes"])
    remark = next(
        statement for statement in trail["statements"]
        if statement["kind"] == citations.KIND_FINDING
        and statement["section"] == "must_have"
    )
    assert remark["support"] == {"level": "supported", "reason": "content_term_anchor"}
    assert remark["item"] == "Distributed Systems"


@pytest.mark.asyncio
async def test_an_unsupported_remark_is_marked_and_flagged() -> None:
    rows = _rows()
    rows[0]["remark"] = "They led the Kubernetes rollout for the payments platform."
    composed = await siddhi_report.compose_prism(
        dimensions=rows, evidence_by_item=_exchanges(), embed=None
    )
    [(section, statement)] = composed.unsupported()
    assert section == "must_have"
    assert statement["support"]["reason"] == "invented_term"
    assert composed.needs_human_review is True
    assert "citation_unsupported" in {f["issue"] for f in composed.review_findings()}


@pytest.mark.asyncio
async def test_template_text_is_a_finding_and_flags_review() -> None:
    rows = _rows()
    rows[0]["remark_provenance"] = "template"
    groups = [
        {
            "category": "must_have",
            "label": "Must-have",
            "items": [
                {
                    "name": "Distributed Systems",
                    "grade": "Moderately Matching",
                    "remark": rows[0]["remark"],
                    "probes": ["You mentioned the orders service, so walk me through the rollback."],
                    "probes_source": "template",
                }
            ],
        }
    ]
    composed = await siddhi_report.compose_prism(
        dimensions=rows,
        evidence_by_item=_exchanges(),
        gap_groups=groups,
        overall_summary="Owned the orders migration end to end.",
        overall_grade="Matching",
        overall_remark_source="template",
        embed=None,
    )
    assert composed.templates == (
        "remark:Distributed Systems",
        "remark:overall",
        "probes:Distributed Systems",
    )
    assert composed.needs_human_review is True
    issues = [f["issue"] for f in composed.review_findings()]
    assert issues.count("template_output") == 3


def test_grade_statements_and_catalogue_sections_are_not_support_checked() -> None:
    """A grade word is a verdict, not a paraphrase, and catalogue text no model
    wrote would flag every report: the checked set is the model's prose."""
    checked = siddhi_report.SUPPORT_CHECKED
    assert all(kind != citations.KIND_GRADE for _, kind in checked)
    assert ("overall", citations.KIND_FINDING) in checked
    assert ("gap_analysis", citations.KIND_PROBE) in checked
    for section in synthesis.RATED_SECTIONS:
        assert (section, citations.KIND_FINDING) in checked
    assert not any(section == "claim_evidence" for section, _ in checked)
