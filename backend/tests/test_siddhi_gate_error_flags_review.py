"""A quality gate that cannot run has checked nothing, so its verdict FAILS.

The adapter this replaced (`functional_assessment._gate_report`) returned a
PASSING verdict with a low finding on its own error, so a gate that crashed on
every report was indistinguishable from a gate that approved every report.
`siddhi.quality_gate.evaluate` returns a failing verdict with a HIGH
`gate_unavailable` finding: the report is still written, and it goes to a
person.
"""
from __future__ import annotations

import asyncio
import logging

from app.services.agents import gates
from app.services.siddhi import quality_gate
from app.services.siddhi import report as siddhi_report


def _composed():
    return asyncio.run(
        siddhi_report.compose_prism(
            dimensions=[
                {
                    "category": "must_have",
                    "name": "Distributed Systems",
                    "grade": "Matching",
                    "remark": "Owned the orders migration.",
                }
            ],
            evidence_by_item={
                "Distributed Systems": [
                    {"question": "A migration?", "answer": "I moved the orders service."}
                ]
            },
            embed=None,
        )
    )


def _evaluate():
    return quality_gate.evaluate(
        gap_analysis_json={"groups": [], "siddhi": _composed().siddhi_namespace()},
        dimensions=[{"category": "must_have", "name": "Distributed Systems", "grade": "Matching"}],
        overall_summary="",
        validation={},
        validation_source={},
        evidence_by_item={},
        miti_grades={"Distributed Systems": "Matching"},
        miti_overall_grade=None,
    )


def test_a_gate_crash_yields_a_failing_verdict_that_flags_review(monkeypatch, caplog) -> None:
    def _broken(agent_id, payload):
        raise KeyError("a field the gate expected")

    monkeypatch.setattr(gates, "run_gate", _broken)
    with caplog.at_level(logging.ERROR):
        verdict = _evaluate()
    assert not verdict.passed
    [finding] = verdict.findings
    assert finding.issue == "gate_unavailable"
    assert finding.severity == "high"
    assert any(
        "siddhi.quality_gate_unavailable" in record.getMessage()
        for record in caplog.records
    )


def test_a_section_with_no_trail_is_unchecked_and_says_so() -> None:
    """A gate handed nothing to read has checked nothing. It must not pass."""
    verdict = quality_gate.evaluate(
        gap_analysis_json={"groups": []},
        dimensions=[],
        overall_summary="",
        validation={},
        validation_source={},
        evidence_by_item={},
        miti_grades={},
        miti_overall_grade=None,
    )
    assert not verdict.passed
    assert [finding.issue for finding in verdict.findings] == ["gate_unavailable"]


def test_a_working_gate_is_not_replaced_by_the_failure_verdict() -> None:
    verdict = _evaluate()
    assert "gate_unavailable" not in {finding.issue for finding in verdict.findings}
