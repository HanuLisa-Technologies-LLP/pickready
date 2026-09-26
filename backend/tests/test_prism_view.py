"""`services/prism_view`: the ONE serializer the report route and the PDF share.

Pure-unit pins for the rules a reader relies on (PLAN-p5 WP5-F):

* a skill the evaluation could not complete states "Not assessed" and the
  server's sentence, never a grade, and draws no radar spoke (plotting it at
  the innermost band would state Not Matching);
* a spoke with no recorded requirement carries no requirement shape;
* a withheld overall states "Not assessed" and is never recomputed;
* a report written before migration 0030 with no stored overall is
  recomputed from its rows, and one with NO assessed row states "Not
  assessed" rather than a zero read as Not Matching.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.services import prism_view


def _row(**overrides):
    base = dict(
        name="Event streaming",
        description=None,
        category="must_have",
        score=82,
        required_level=75,
        remark="Named the partitions and the rollback.",
        remark_provenance=None,
        assessment_status="graded",
        evidence_confidence=None,
        evidence_sources=None,
        ordinal=1,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _report(**overrides):
    base = dict(overall_status=None, overall_score=None)
    base.update(overrides)
    return SimpleNamespace(**base)


def test_a_not_assessed_row_states_it_in_words_and_carries_no_grade() -> None:
    out = prism_view.dimension_out(_row(score=None, assessment_status="not_assessed"))
    assert out.grade == prism_view.NOT_ASSESSED_WORD
    assert out.status == "not_assessed"
    assert out.status_note == prism_view.STATUS_NOTE_NOT_ASSESSED


def test_a_graded_row_is_a_word() -> None:
    out = prism_view.dimension_out(_row(score=82))
    assert out.grade == "Matching"
    assert out.status == "graded"
    assert out.status_note is None
    assert out.required_level == "Matching"


def test_a_not_assessed_skill_draws_no_spoke_and_a_missing_requirement_no_shape() -> None:
    charts = prism_view.build_radar_charts(
        [
            {"category": "must_have", "name": "A", "score": 90, "required_level": 75, "ordinal": 1},
            {"category": "must_have", "name": "B", "score": None, "required_level": 75, "ordinal": 2},
            {"category": "must_have", "name": "C", "score": 70, "required_level": None, "ordinal": 3},
        ]
    )
    must_have = next(chart for chart in charts if chart["key"] == "must_have")
    assert [axis["axis"] for axis in must_have["axes"]] == ["A", "C"]
    spoke_c = must_have["axes"][1]
    assert spoke_c["requirement_band"] is None
    assert spoke_c["requirement_index"] is None


def test_a_withheld_overall_is_never_recomputed() -> None:
    word, status = prism_view._overall(
        _report(overall_status="not_assessed", overall_score=None), [_row(score=95)]
    )
    assert (word, status) == (prism_view.NOT_ASSESSED_WORD, "not_assessed")


def test_a_pre_0030_overall_is_recomputed_from_its_rows() -> None:
    word, status = prism_view._overall(_report(), [_row(score=92), _row(score=90)])
    assert (word, status) == ("Highly Matching", "graded")


def test_a_pre_0030_report_with_nothing_assessed_is_not_read_as_not_matching() -> None:
    """The recompute used to fall back to 0 here, which grades Not Matching:
    a verdict about a candidate nobody graded."""
    word, status = prism_view._overall(_report(), [])
    assert (word, status) == (prism_view.NOT_ASSESSED_WORD, "not_assessed")
