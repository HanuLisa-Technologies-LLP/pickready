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

WHAT IS SWEPT, AND WHAT IS NOT
------------------------------
Live code and configuration, the same scope `test_yukti_legacy_removed.py`
states and for the same reason: NOT `backend/tests/` or `backend/harness/`,
because the guards there name these symbols precisely to forbid them
(`assert not hasattr(fa, "assessment_graph")`), and a sweep that failed on its
own guards would be deleted by the first person it annoyed. Whitespace
normalised through `removal_sweep`, so a mention wrapped across a line is
still found. The patterns match CODE shapes (a call, an attribute, an import
path), so a docstring may still say what was removed.
"""
from __future__ import annotations

import importlib.util
import inspect
import pathlib
import re

import pytest

from app.services import functional_assessment as fa
from tests.removal_sweep import BACKEND, REPO, sweep

THIS_FILE = pathlib.Path(__file__).resolve()

#: Live roots only (see the module docstring for why tests are not swept).
LIVE_ROOTS = (
    BACKEND / "app",
    BACKEND / "scripts",
    REPO / "frontend" / "app",
    REPO / "frontend" / "components",
    REPO / "frontend" / "lib",
    REPO / "infra",
    REPO / "scripts",
    REPO / ".github",
    REPO / ".env.example",
)

#: One pattern per removed shape, so the guard on the guard can require that
#: EACH finds a planted hit rather than that the union finds some.
REMOVED_SHAPES = (
    r"\bassessment_graph\b",
    r"\bbuild_assessment_graph\(",
    r"\bppi_scoring_node\(",
    r"\bvalidation_node\(",
    r"\bsynthesis_node\(",
    r"\bAssessmentState\b",
    r"\b_gate_report\(",
    r"\b_write_evaluation\(",
    r"\bSkillsNotAssessed\b",
    r"\b_matching_dimensions\(",
    r"\b_matching_score\(",
    r"services[./]matching_categories\b",
    r"import matching_categories\b",
    r"services[./]report_evidence\b",
    r"\bpersist_skill_evidence\(",
    r"scripts[./]backfill_functional_reports\b",
    r"scripts[./]validate_functional_assessment\b",
)
REMOVED = re.compile("|".join(REMOVED_SHAPES))

# The planted hits the guard on the guard reads. Never executed.
_PLANTED = """
assessment_graph ; build_assessment_graph( ; ppi_scoring_node( ;
validation_node( ; synthesis_node( ; AssessmentState ; _gate_report( ;
_write_evaluation( ; SkillsNotAssessed ; _matching_dimensions( ;
_matching_score( ; services/matching_categories ; import matching_categories ;
services/report_evidence ; persist_skill_evidence( ;
scripts/backfill_functional_reports ; scripts/validate_functional_assessment
"""


def test_no_live_source_names_a_removed_grading_symbol() -> None:
    hits = sweep(REMOVED, roots=LIVE_ROOTS)
    assert not hits, "a removed grading symbol is back:\n" + "\n".join(hits)


@pytest.mark.parametrize("shape", REMOVED_SHAPES)
def test_the_sweep_is_not_vacuous(shape: str) -> None:
    """The guard on the guard: every alternative finds its planted hit, or a
    broken helper (or a typo in one pattern) would report a clean tree for ever."""
    assert sweep(re.compile(shape), roots=(THIS_FILE,)), shape


def test_the_removed_modules_cannot_be_imported() -> None:
    for name in (
        "app.services.matching_categories",
        "app.services.report_evidence",
        "app.scripts.backfill_functional_reports",
        "app.scripts.validate_functional_assessment",
    ):
        assert importlib.util.find_spec(name) is None, name


def test_the_graph_is_gone_and_the_orchestrator_calls_the_four_stages() -> None:
    """What survives is an orchestrator calling the stage modules in order;
    the direction itself is `test_assessment_pipeline_direction.py`."""
    for name in (
        "assessment_graph", "build_assessment_graph", "synthesis_node",
        "ppi_scoring_node", "validation_node", "AssessmentState",
        "_gate_report", "_write_evaluation", "_matching_dimensions", "_matching_score",
    ):
        assert not hasattr(fa, name), name
    source = inspect.getsource(fa.run_assessment)
    order = [
        source.index("stage_evidence.load_inputs"),
        source.index("grading.grade"),
        source.index("composition.compose"),
        source.index("persistence.write_report"),
    ]
    assert order == sorted(order)
