"""Recommended Human Validation Points: what it selects, and what it never says.

The two properties worth a test file of their own:

  * IT NEVER STATES OR IMPLIES A HIRING DECISION. It recommends looking. A
    section that could say "do not advance" would be an automated hiring
    decision wearing an advisory feature's clothes, and the copy is swept for
    that reading rather than trusted to a review.
  * IT IS NOT THE GAP ANALYSIS. That section is grade driven and unbounded;
    this one is confidence and contradiction driven and stops at five. The
    difference is visible in one case: a HIGHLY MATCHING item resting on the
    candidate's own unchecked account belongs here and cannot appear there.
"""
from __future__ import annotations

import re

import pytest

from app.services import evidence_confidence, gap_analysis
from app.services.siddhi import validation_points


def _row(
    name: str,
    *,
    grade: str = "Matching",
    required: str | None = "Matching",
    confidence: str = evidence_confidence.CONFIDENCE_HIGH,
) -> dict:
    return {
        "name": name,
        "grade": grade,
        "required_level": required,
        "evidence_confidence": confidence,
    }


# -- What it selects ---------------------------------------------------------

def test_a_strong_grade_on_a_thin_record_is_the_first_row():
    """THE CASE THAT JUSTIFIES THE SECTION EXISTING.

    `gap_analysis.gap_items` selects on the grade, so a Highly Matching item is
    invisible to it however weakly it is evidenced. This section selects on the
    evidence, so the same item is its first entry. Both facts are asserted
    here, because the value is the difference between them.
    """
    rows = [
        _row(
            "Distributed Systems",
            grade="Highly Matching",
            confidence=evidence_confidence.CONFIDENCE_LOW,
        )
    ]
    section = validation_points.build(rows)
    assert [point["area"] for point in section["points"]] == ["Distributed Systems"]

    graded = gap_analysis.gap_items(
        [{"name": "Distributed Systems", "category": "must_have", "score": 95}],
        "must_have",
    )
    assert graded == []


def test_high_confidence_alone_is_not_a_validation_point():
    section = validation_points.build([_row("Observability")])
    assert section["points"] == []
    assert section["no_points_statement"] == validation_points.NO_POINTS_STATEMENT


def test_the_worst_evidence_is_listed_first():
    """Ordering carries the section's whole ranking, because it states no
    severity of its own. An area nothing addressed is a sharper call on an
    interviewer's time than one that was answered but uncorroborated."""
    rows = [
        _row("Moderate one", confidence=evidence_confidence.CONFIDENCE_MODERATE),
        _row("Low one", confidence=evidence_confidence.CONFIDENCE_LOW),
        _row("Nothing at all", confidence=evidence_confidence.CONFIDENCE_INSUFFICIENT),
    ]
    section = validation_points.build(rows)
    assert [point["area"] for point in section["points"]] == [
        "Nothing at all",
        "Low one",
        "Moderate one",
    ]


def test_a_grade_one_band_below_the_requirement_is_borderline():
    rows = [
        _row(
            "Observability",
            grade="Matching",
            required="Highly Matching",
        )
    ]
    section = validation_points.build(rows)
    assert section["points"][0]["driver"] == validation_points.DRIVER_BORDERLINE


def test_a_row_with_no_requirement_can_never_be_borderline():
    """An AI Score parameter and a technical row carry no requirement at all,
    so there is nothing for borderline to mean on them and guessing one would
    manufacture a point."""
    section = validation_points.build([_row("Skills present", required=None)])
    assert section["points"] == []


def test_a_grade_two_bands_below_the_requirement_is_not_borderline():
    """Borderline means NEARLY met. Two bands short is a gap, which the Gap
    Analysis already reports, and duplicating it here would make the section a
    second copy of that list."""
    rows = [_row("Observability", grade="Moderately Matching", required="Highly Matching")]
    assert validation_points.build(rows)["points"] == []


def test_a_contradiction_is_listed_even_when_the_matrix_does_not_grade_it():
    """An inconsistency in an ungraded claim is still an inconsistency, and
    dropping it would lose the only driver that reports a record disagreeing
    with itself."""
    section = validation_points.build(
        [_row("Observability")], contradicted_areas=["Tenure at Acme"]
    )
    assert [point["area"] for point in section["points"]] == ["Tenure at Acme"]
    assert section["points"][0]["driver"] == validation_points.DRIVER_CONTRADICTION


def test_one_area_is_listed_once_with_one_reason():
    """A reader told to check the same area twice, for two different stated
    causes, reads the second as a different area."""
    rows = [_row("Observability", confidence=evidence_confidence.CONFIDENCE_LOW,
                 grade="Matching", required="Highly Matching")]
    section = validation_points.build(rows, contradicted_areas=["Observability"])
    assert len(section["points"]) == 1
    assert section["points"][0]["driver"] == validation_points.DRIVER_CONFIDENCE


def test_the_list_is_capped_and_the_cap_keeps_the_worst():
    rows = [
        _row(f"Area {word}", confidence=evidence_confidence.CONFIDENCE_MODERATE)
        for word in ("one", "two", "three", "four", "five", "six", "seven")
    ]
    rows[6] = _row("Area seven", confidence=evidence_confidence.CONFIDENCE_INSUFFICIENT)
    section = validation_points.build(rows)
    assert len(section["points"]) == validation_points.MAX_POINTS
    assert section["points"][0]["area"] == "Area seven"


def test_fewer_than_three_is_reported_rather_than_padded():
    """Three is the brief's target, not a floor. Inventing a third point would
    send an interviewer after a question the record does not justify."""
    rows = [
        _row("Observability", confidence=evidence_confidence.CONFIDENCE_LOW),
        _row("Distributed Systems"),
    ]
    section = validation_points.build(rows)
    assert len(section["points"]) == 1
    assert section["no_points_statement"] is None


def test_the_empty_state_sentence_appears_only_when_the_list_is_empty():
    """Beside a populated list it would read as a summary of it, and it is
    not one: it is the alternative to blank space."""
    populated = validation_points.build(
        [_row("Observability", confidence=evidence_confidence.CONFIDENCE_LOW)]
    )
    assert populated["no_points_statement"] is None


# -- What it may never say ---------------------------------------------------

#: Every word that would turn a recommendation to LOOK into a recommendation to
#: DECIDE. `hiring/layers.INVARIANTS` refuses auto-rejection outright; this is
#: the copy-level form of the same rule.
_DECISION_LANGUAGE = (
    "reject", "rejected", "advance", "advanced", "shortlist", "hire",
    "do not hire", "decline", "unsuitable", "suitable", "not a fit",
    "recommend hiring", "pass on", "disqualif", "proceed with", "offer them",
)


def _every_sentence(section: dict) -> list[str]:
    """Everything the module writes EXCEPT its own disclaimer.

    The section note is the one string that has to contain the words "advance"
    and "reject", because it is the sentence saying the section never does
    either. Exempting it by NAME, and pinning it to one constant in the test
    below, is the only exemption that cannot quietly widen: a second sentence
    picking up decision language would still fail.
    """
    body = [str(section.get("no_points_statement") or "")]
    for point in section.get("points") or []:
        body.extend([str(point.get("reason") or ""), str(point.get("probe") or "")])
    return [sentence for sentence in body if sentence]


def _all_sections() -> list[dict]:
    """One section per driver, plus the empty state, so the sweep covers every
    sentence the module is capable of writing."""
    sections = [validation_points.build([_row("Observability")])]
    for level in (
        evidence_confidence.CONFIDENCE_INSUFFICIENT,
        evidence_confidence.CONFIDENCE_LOW,
        evidence_confidence.CONFIDENCE_MODERATE,
    ):
        sections.append(validation_points.build([_row("Observability", confidence=level)]))
    sections.append(
        validation_points.build(
            [_row("Observability", grade="Matching", required="Highly Matching")]
        )
    )
    sections.append(
        validation_points.build([], contradicted_areas=["Tenure at Acme"])
    )
    return sections


def test_the_only_sentence_naming_a_decision_is_the_one_refusing_to_make_one():
    """The disclaimer, and nothing else, may say the words. It is the same
    sentence the Gap Analysis already prints at the foot of its own section."""
    for section in _all_sections():
        assert section["note"] == validation_points.SECTION_NOTE
    lowered = validation_points.SECTION_NOTE.casefold()
    assert "never whether to advance or reject" in lowered


def test_no_sentence_the_module_can_write_states_a_hiring_decision():
    for section in _all_sections():
        for sentence in _every_sentence(section):
            lowered = sentence.casefold()
            for banned in _DECISION_LANGUAGE:
                assert banned not in lowered, (banned, sentence)


def test_no_sentence_carries_a_number_or_an_em_dash():
    """A delivered report states words. The dash rule covers generated content,
    not only labels."""
    dash = chr(8212)
    for section in _all_sections():
        for sentence in _every_sentence(section) + [str(section["note"])]:
            assert not re.search(r"\d", sentence), sentence
            assert dash not in sentence


def test_every_point_names_its_area_in_its_probe():
    """A probe that could have been written before the assessment is generic
    advice by definition, which is the thing the gap section's banned-phrase
    corpus exists to stop producing."""
    for level in (
        evidence_confidence.CONFIDENCE_INSUFFICIENT,
        evidence_confidence.CONFIDENCE_LOW,
        evidence_confidence.CONFIDENCE_MODERATE,
    ):
        section = validation_points.build([_row("Incident judgement", confidence=level)])
        assert "Incident judgement" in section["points"][0]["probe"]


def test_a_point_carries_no_severity_score_or_decision_field():
    """The field list is the guard. A field an outcome could be written into
    is a field an outcome eventually appears in."""
    section = validation_points.build(
        [_row("Observability", confidence=evidence_confidence.CONFIDENCE_LOW)]
    )
    assert set(section["points"][0]) == {
        "area",
        "driver",
        "confidence",
        "reason",
        "probe",
    }


def test_the_module_calls_no_model():
    """Structural, by AST. This is the surface that exists BECAUSE the evidence
    was weak, and a provider outage is exactly when weak evidence most needs
    saying."""
    import ast
    import pathlib

    source = pathlib.Path(validation_points.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "llm_router" not in node.module
            assert "agent_loop" not in node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert "llm_router" not in alias.name
