"""Citation enforcement: the acceptance criterion, executed.

spec-doc5's Part A acceptance list:

    "Every delivered PRISM Report statement traces to a cited evidence node;
     this is enforced in code, verified by a test that tries to produce an
     uncited statement and confirms it's blocked."

`test_producing_an_uncited_statement_is_blocked` is that test, literally. The
rest of this file covers the ways the guarantee could be true in the happy case
and false in practice: a bypass parameter, a fabricated ref, a caller widening
the accepted set per section, or a new statement kind quietly defaulting into
the exempt bucket.
"""
from __future__ import annotations

import dataclasses
import inspect

import pytest

from app.services.siddhi import citations


def _report(*refs: str) -> citations.Report:
    return citations.Report(known_refs=frozenset(refs or ("e1", "e2")))


# ── THE ACCEPTANCE CRITERION ─────────────────────────────────────────────────


def test_producing_an_uncited_statement_is_blocked() -> None:
    """The criterion, executed: try to produce an uncited statement, confirm it
    is blocked."""
    report = _report()
    report.section("gap", "Gap Analysis & Action Plan").add(
        citations.Statement(
            kind=citations.KIND_FINDING,
            text="They have run a migration end to end.",
            evidence_refs=(),
        )
    )
    with pytest.raises(citations.UncitedStatement):
        report.render()


def test_a_cited_statement_renders() -> None:
    report = _report("e1")
    report.section("gap", "Gap Analysis").add(
        citations.Statement(
            kind=citations.KIND_FINDING,
            text="They have run a migration end to end.",
            evidence_refs=("e1",),
        )
    )
    rendered = report.render()
    assert rendered[0]["statements"][0]["evidence_refs"] == ["e1"]


def test_a_fabricated_citation_is_worse_than_none_and_is_named_separately() -> None:
    """An empty citation list is a generator that forgot. An unknown ref is a
    generator that INVENTED one, which is more dangerous because it reads as
    provenance."""
    report = _report("e1")
    report.section("gap", "Gap Analysis").add(
        citations.Statement(
            kind=citations.KIND_FINDING,
            text="They led a team of forty.",
            evidence_refs=("e-does-not-exist",),
        )
    )
    with pytest.raises(citations.UnknownEvidence):
        report.render()


# ── THERE IS NO BYPASS ───────────────────────────────────────────────────────


def test_render_takes_no_bypass_parameter() -> None:
    """A bypass parameter is a bypass that will be used, and the first use will
    be in a hotfix at the end of a release."""
    signature = inspect.signature(citations.Section.render)
    assert list(signature.parameters) == ["self", "known_refs"]
    for forbidden in ("force", "strict", "allow_uncited", "skip_checks"):
        assert forbidden not in signature.parameters


def test_report_render_takes_no_arguments_at_all() -> None:
    """`known_refs` is fixed at CONSTRUCTION, so a caller cannot widen the
    accepted set per section to get one statement through."""
    signature = inspect.signature(citations.Report.render)
    assert list(signature.parameters) == ["self"]


def test_the_known_ref_set_is_immutable_on_the_report() -> None:
    report = _report("e1")
    assert isinstance(report.known_refs, frozenset)
    with pytest.raises(AttributeError):
        report.known_refs.add("e-smuggled")  # type: ignore[attr-defined]


def test_there_is_no_other_way_to_get_text_out_of_a_section() -> None:
    """`render` is the chokepoint. A second method returning statement text
    would be a second path with no check on it."""
    public = {
        name
        for name in dir(citations.Section)
        if not name.startswith("_") and callable(getattr(citations.Section, name))
    }
    assert public == {"add", "render"}


# ── WHICH KINDS NEED A CITATION ──────────────────────────────────────────────


def test_a_heading_does_not_need_a_citation() -> None:
    """Requiring one would produce either a fake citation or an unreadable
    report."""
    report = _report()
    section = report.section("header", "PRISM Report")
    section.add(citations.Statement(citations.KIND_HEADING, "Gap Analysis & Action Plan"))
    section.add(citations.Statement(citations.KIND_CONNECTIVE, "Across the areas assessed:"))
    assert report.render()


def test_the_validation_section_is_verbatim_and_needs_no_citation() -> None:
    """It is the candidate's own unrated submission, reproduced exactly as
    submitted. It is not a claim about the candidate derived from evidence."""
    report = _report()
    report.section("validation", "Validation").add(
        citations.Statement(
            citations.KIND_VERBATIM, "Why does this role interest you? -- (their answer)"
        )
    )
    assert report.render()


def test_a_gap_statement_needs_a_citation() -> None:
    """"There is no evidence of X" feels uncitable and is not: the citation is
    the evidence that was SEARCHED, which is what distinguishes "we looked at
    their answers and none addressed it" from "we never asked". The second is a
    gap in the assessment being reported as a gap in the candidate."""
    assert citations.KIND_GAP in citations.REQUIRES_CITATION
    report = _report()
    report.section("gap", "Gap Analysis").add(
        citations.Statement(citations.KIND_GAP, "No evidence of on-call ownership.")
    )
    with pytest.raises(citations.UncitedStatement):
        report.render()


def test_a_probe_needs_a_citation() -> None:
    """spec-doc5 requires every gap probe to be grounded in the candidate's
    actual answer rather than generic advice, and a probe with no citation is
    generic advice by definition."""
    assert citations.KIND_PROBE in citations.REQUIRES_CITATION


def test_a_grade_needs_a_citation() -> None:
    assert citations.KIND_GRADE in citations.REQUIRES_CITATION


def test_a_new_statement_kind_cannot_default_into_the_exempt_bucket() -> None:
    """A new kind of thing in a report is a decision about what the report
    asserts. It should cost a reviewed line."""
    with pytest.raises(ValueError, match="Unknown statement kind"):
        citations.Statement("editorial_aside", "They seem nice.")


def test_the_kind_list_is_closed() -> None:
    assert citations.STATEMENT_KINDS == {
        "finding",
        "grade",
        "gap",
        "probe",
        "heading",
        "connective",
        "verbatim",
    }
    assert citations.REQUIRES_CITATION <= citations.STATEMENT_KINDS


# ── FAIL FAST, AND FEED BACK ─────────────────────────────────────────────────


def test_the_whole_report_fails_rather_than_rendering_with_holes() -> None:
    """A report that rendered its clean sections and dropped the rest would be a
    report with holes in it that reads as complete."""
    report = _report("e1")
    report.section("clean", "AI Score").add(
        citations.Statement(citations.KIND_FINDING, "ok", ("e1",))
    )
    report.section("dirty", "Gap Analysis").add(
        citations.Statement(citations.KIND_FINDING, "uncited", ())
    )
    with pytest.raises(citations.UncitedStatement):
        report.render()


def test_violations_reports_without_raising_for_the_loops_reflect_stage() -> None:
    """`agent_loop` feeds a rejection back VERBATIM as an instruction, and "you
    returned three statements with no citation, here they are" is a defect a
    model fixes when told. `render` is the gate; this is the feedback."""
    report = _report("e1")
    section = report.section("gap", "Gap Analysis")
    section.add(citations.Statement(citations.KIND_FINDING, "uncited one", ()))
    section.add(citations.Statement(citations.KIND_GAP, "bad ref", ("e-nope",)))
    section.add(citations.Statement(citations.KIND_FINDING, "fine", ("e1",)))
    section.add(citations.Statement(citations.KIND_HEADING, "exempt"))

    found = report.violations()
    assert len(found) == 2
    assert {v["problem"] for v in found} == {"no_citation", "unknown_evidence"}


def test_the_standalone_check_uses_the_same_rule() -> None:
    """One rule and one error class, rather than two implementations that must
    agree."""
    with pytest.raises(citations.UncitedStatement):
        citations.check(
            [citations.Statement(citations.KIND_FINDING, "uncited", ())], ["e1"]
        )
    citations.check(
        [citations.Statement(citations.KIND_FINDING, "cited", ("e1",))], ["e1"]
    )


def test_a_statement_is_frozen() -> None:
    """A caller must not be able to blank the refs after a check has run."""
    statement = citations.Statement(citations.KIND_FINDING, "x", ("e1",))
    with pytest.raises(dataclasses.FrozenInstanceError):
        statement.evidence_refs = ()  # type: ignore[misc]


# ── END TO END WITH THE AGGREGATOR ───────────────────────────────────────────


def test_the_aggregates_evidence_refs_are_the_known_set() -> None:
    """The two halves have to line up: an evaluation's citable set IS what its
    dimension evaluators actually cited, and nothing else."""
    from app.services.miti import aggregation
    from app.services.miti.dimensions import DimensionResult

    aggregate = aggregation.aggregate(
        [
            DimensionResult("verified_competence", "strong", ("e1", "e2")),
            DimensionResult("track_record_impact", "solid", ("e2", "e3")),
        ]
    )
    # De-duplicated, order preserved: two dimensions citing the same evidence is
    # normal and must not double-count.
    assert aggregate.evidence_refs == ["e1", "e2", "e3"]

    report = citations.Report(known_refs=frozenset(aggregate.evidence_refs))
    report.section("overall", "Overall Assessment").add(
        citations.Statement(citations.KIND_GRADE, "Must-have: Matching", ("e1",))
    )
    assert report.render()

    report.section("bad", "Bad").add(
        citations.Statement(citations.KIND_FINDING, "invented", ("e9",))
    )
    with pytest.raises(citations.UnknownEvidence):
        report.render()
