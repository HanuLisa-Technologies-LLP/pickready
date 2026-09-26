"""What the grading split deleted stays deleted (PLAN-p5 WP5-D).

`functional_assessment` was a LangGraph whose nodes scored, wrote prose, ran
the gate and UPDATEd the delivered report. It is now an orchestrator over the
`assessment_pipeline` stages, and these names went with the old shape:

  * the graph and its nodes (`assessment_graph`, `build_assessment_graph`,
    `ppi_scoring_node`, `validation_node`, `synthesis_node`, `AssessmentState`),
    the report adapter (`_gate_report`), the evaluation writer
    (`_write_evaluation`) and the interim refusal (`SkillsNotAssessed`);
  * the AI Score rows (`_matching_dimensions`, `_matching_score`) and the
    module they read, `services/matching_categories`;
  * `services/report_evidence` (a second keyword-regex reading of the
    transcript beside Miti's ledger that nothing read) and its
    `persist_skill_evidence`;
  * the obsolete operator scripts `backfill_functional_reports` and
    `validate_functional_assessment`.

Whitespace-normalised through `removal_sweep`, so a mention wrapped across a
line is still found. The patterns match CODE shapes (a call, an attribute, an
import path), so a docstring may still say what was removed.
"""
from __future__ import annotations

import pathlib
import re

from tests.removal_sweep import sweep

THIS_FILE = pathlib.Path(__file__).resolve()

REMOVED = re.compile(
    r"\bassessment_graph\b|\bbuild_assessment_graph\(|\bppi_scoring_node\(|"
    r"\bvalidation_node\(|\bsynthesis_node\(|\bAssessmentState\b|\b_gate_report\(|"
    r"\b_write_evaluation\(|\bSkillsNotAssessed\b|\b_matching_dimensions\(|"
    r"\b_matching_score\(|services[./]matching_categories\b|import matching_categories\b|"
    r"services[./]report_evidence\b|\bpersist_skill_evidence\(|"
    r"scripts[./]backfill_functional_reports\b|scripts[./]validate_functional_assessment\b"
)


def test_no_live_source_names_a_removed_grading_symbol() -> None:
    hits = sweep(REMOVED, exempt=(THIS_FILE,))
    assert not hits, "a removed grading symbol is back:\n" + "\n".join(hits)


def test_the_sweep_is_not_vacuous() -> None:
    """The guard on the guard: every alternative finds a planted hit."""
    assert len(sweep(REMOVED, roots=(THIS_FILE,))) >= 10
