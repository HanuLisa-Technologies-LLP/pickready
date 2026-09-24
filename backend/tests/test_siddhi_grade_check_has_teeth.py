"""The Miti == Siddhi grade check compares two sources now, so it can fire.

Until the Vivekium release the only caller handed the Siddhi gate
`"grades": graded, "miti_grades": graded`, one dict under two names, and the
check could not fire for any report ever written. `siddhi.quality_gate` reads
what the document RENDERED on one side and Miti's grades on the other.

Every test composes a real report through the real composer and then disagrees
with it from Miti's side, because a hand-built `grades` dict would prove only
that the gate compares two dicts, which it always did.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.services.siddhi import quality_gate, synthesis, trail
from app.services.siddhi import report as siddhi_report

VALIDATION = {"notice_period": "30 days"}


def _rows() -> list[dict]:
    return [
        {
            "category": "must_have",
            "name": "Distributed Systems",
            "grade": "Matching",
            "remark": "Owned the orders migration and wrote the rollback runbook.",
        },
        {
            "category": "behavioural",
            "name": "Judgement under pressure",
            "grade": "Highly Matching",
            "remark": "Called the rollback first during the payments outage.",
        },
    ]


def _exchanges() -> dict:
    return {
        "Distributed Systems": [
            {"question": "A migration you owned?", "answer": "I moved the orders service and wrote the rollback runbook."}
        ],
        "Judgement under pressure": [
            {"question": "A hard call?", "answer": "During the payments outage I called the rollback first."}
        ],
    }


def _composed(overall_grade: str = "Matching"):
    return asyncio.run(
        siddhi_report.compose_prism(
            dimensions=_rows(),
            evidence_by_item=_exchanges(),
            overall_summary="Owned the orders migration and called the rollback first.",
            overall_grade=overall_grade,
            embed=None,
        )
    )


def _verdict(miti: dict[str, str], *, miti_overall: str | None = "Matching", composed=None):
    return quality_gate.evaluate(
        gap_analysis_json={"groups": [], "siddhi": (composed or _composed()).siddhi_namespace()},
        dimensions=_rows(),
        overall_summary="Owned the orders migration and called the rollback first.",
        validation=VALIDATION,
        validation_source=VALIDATION,
        evidence_by_item=_exchanges(),
        miti_grades=miti,
        miti_overall_grade=miti_overall,
    )


def _issues(verdict) -> set[str]:
    return {finding.issue for finding in verdict.findings}


AGREEING = {"Distributed Systems": "Matching", "Judgement under pressure": "Highly Matching"}


def test_a_report_that_states_mitis_grades_passes() -> None:
    verdict = _verdict(AGREEING)
    assert "grade_disagrees_with_scoring" not in _issues(verdict)
    assert verdict.passed, verdict.as_dict()


def test_a_rendered_grade_that_differs_from_mitis_fails_the_gate() -> None:
    """THE CHECK THAT COULD NOT FIRE. Miti graded Not Matching; the document
    says Matching. The report must not ship unmarked."""
    verdict = _verdict({**AGREEING, "Distributed Systems": "Not Matching"})
    assert "grade_disagrees_with_scoring" in _issues(verdict)
    assert not verdict.passed


def test_a_grade_miti_decided_that_the_document_never_states_fails() -> None:
    verdict = _verdict({**AGREEING, "Kafka": "Matching"})
    assert "grade_missing_from_report" in _issues(verdict)
    assert not verdict.passed


def test_a_stated_grade_miti_never_decided_fails() -> None:
    verdict = _verdict({"Distributed Systems": "Matching"})
    assert "grade_without_scoring" in _issues(verdict)
    assert not verdict.passed


def test_the_overall_grade_is_compared_too() -> None:
    verdict = _verdict(AGREEING, miti_overall="Moderately Matching")
    assert "overall_grade_disagrees_with_scoring" in _issues(verdict)
    assert not verdict.passed


def test_a_not_assessed_skill_must_be_stated_as_not_assessed() -> None:
    """Miti could not evaluate the skill: the document may state no grade for
    it, only the words Not assessed."""
    skills = [
        SimpleNamespace(name="Distributed Systems", grade=None),
        SimpleNamespace(name="Judgement under pressure", grade="Highly Matching"),
    ]
    miti = quality_gate.miti_grades_from(skills)
    assert miti["Distributed Systems"] == synthesis.NOT_ASSESSED_WORD
    # The rows still say Matching, so the document states a grade Miti never
    # gave: the gate says so.
    assert "grade_disagrees_with_scoring" in _issues(_verdict(miti))

    rows = _rows()
    rows[0]["assessment_status"] = "not_assessed"
    composed = asyncio.run(
        siddhi_report.compose_prism(
            dimensions=rows,
            evidence_by_item=_exchanges(),
            overall_grade="Matching",
            embed=None,
        )
    )
    assert "grade_disagrees_with_scoring" not in _issues(_verdict(miti, composed=composed))


def test_the_grades_are_read_from_the_rendered_statements() -> None:
    composed = _composed()
    stated = quality_gate.stated_grades(trail.read_trail({"siddhi": composed.siddhi_namespace()}))
    assert ("must_have", "Distributed Systems", "Matching") in stated
    assert ("behavioural", "Judgement under pressure", "Highly Matching") in stated
    # An evidence-confidence line is a different statement and is not a grade.
    assert all("confidence" not in grade for _, _, grade in stated)
