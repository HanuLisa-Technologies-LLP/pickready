"""The two 0107 sections, across every place a section has to be declared.

A PRISM section exists in four places and they have to agree or a recruiter
approves a document on screen and mails a different one:

    report_pdf.SECTION_ORDER           the PDF's order AND its builder table
    REPORT_SECTION_ORDER (.tsx)        the screen's order AND its component map
    siddhi.synthesis.SECTION_TITLES    the heading the chokepoint prints
    the response model                 what actually crosses the boundary

`test_prism_report.py` already pins the first two against each other. This file
pins the other two to them, and then asserts the property a section added to an
IMMUTABLE document has to have: every report written before today still
renders, without the new sections and without raising.
"""
from __future__ import annotations

import asyncio
import io
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pypdf import PdfReader

from app.schemas.assessments import (
    ClaimEvidenceOut,
    DimensionOut,
    ValidationPointsOut,
)
from app.services import report_pdf
from app.services.siddhi import citations, claim_evidence, synthesis, validation_points
from app.services.siddhi import report as siddhi_report

NEW_SECTIONS = (claim_evidence.SECTION_KEY, validation_points.SECTION_KEY)


def _frontend_source() -> str:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "frontend" / "components" / "functional-skills-report.tsx"
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    pytest.skip("frontend tree not mounted; the comparison runs in CI")


# -- The declarations ---------------------------------------------------------

def test_both_new_sections_are_in_the_pdf_order():
    for key in NEW_SECTIONS:
        assert key in report_pdf.SECTION_ORDER


def test_both_new_sections_are_in_the_screens_order():
    source = _frontend_source()
    literal = re.search(
        r"REPORT_SECTION_ORDER\s*=\s*\[(.*?)\]\s*as const", source, re.S
    )
    assert literal, "REPORT_SECTION_ORDER is no longer a plain array literal"
    on_screen = tuple(re.findall(r'"([a-z_]+)"', literal.group(1)))
    assert on_screen == report_pdf.SECTION_ORDER
    for key in NEW_SECTIONS:
        assert key in on_screen


def test_the_screen_has_a_component_for_every_section_it_orders():
    """The order array drives the render, so a key with no entry in the record
    would render nothing at all. TypeScript catches it at build; this catches
    it here, where the order is already being read."""
    source = _frontend_source()
    for key in report_pdf.SECTION_ORDER:
        assert re.search(rf"^\s*{key}: ", source, re.M), key


def test_every_ordered_section_has_a_title_the_chokepoint_can_print():
    for key in report_pdf.SECTION_ORDER:
        if key == "proctoring":
            # The Proctoring Report is composed by its own module and never
            # passes through `citations`, so it has no entry and needs none.
            continue
        assert key in synthesis.SECTION_TITLES, key


def test_the_two_new_headings_are_identical_in_both_renderers():
    """Verbatim, or a reader gets one name on screen and another in the PDF."""
    assert (
        synthesis.SECTION_TITLES[claim_evidence.SECTION_KEY]
        == report_pdf.CLAIM_EVIDENCE_TITLE
        == claim_evidence.SECTION_TITLE
    )
    assert (
        synthesis.SECTION_TITLES[validation_points.SECTION_KEY]
        == report_pdf.VALIDATION_POINTS_TITLE
        == validation_points.SECTION_TITLE
    )
    source = _frontend_source()
    assert f'"{report_pdf.CLAIM_EVIDENCE_TITLE}"' in source
    assert f'"{report_pdf.VALIDATION_POINTS_TITLE}"' in source


def test_gap_analysis_still_precedes_validation_in_the_declared_order():
    """The 2026-08-23 reversal. Inserting two sections must not undo it."""
    order = report_pdf.SECTION_ORDER
    assert order.index("gap_analysis") < order.index("validation")


# -- A report written before 0107 --------------------------------------------

def _legacy_report() -> dict:
    """A payload exactly as a report written before 0107 carries it.

    No `evidence_confidence` on any rated line, no `claim_evidence` key and no
    `validation_points` key. A report is immutable, so this is not a
    hypothetical: it is what every stored report in the product looks like.
    """
    dimension = {
        "name": "Distributed Systems",
        "grade": "Matching",
        "required_level": "Matching",
        "remark": "Described owning the migration end to end and the rollback.",
    }
    return {
        "reference_code": "K7QP-2M4X-9TB1",
        "overall_grade": "Matching",
        "overall_summary": "Consistent evidence of ownership across the stack.",
        "radar_charts": [],
        "ai_score": [dict(dimension, name="Skills present")],
        "must_have": [dimension],
        "nice_to_have": [dict(dimension, name="Observability")],
        "behavioural": [dict(dimension, name="Judgement under pressure")],
        "gap_analysis": {
            "focus_summary": "Spend the interview on incident judgement.",
            "must_have_cap_applied": False,
            "groups": [
                {
                    "category": "must_have",
                    "label": "Must-have gaps",
                    "items": [],
                    "no_gaps_statement": "No gaps in this group.",
                }
            ],
        },
        "validation": {"fields": [{"label": "Notice period", "value": "Thirty days"}]},
    }


def _pdf_text(report: dict) -> str:
    payload = report_pdf.render_report_pdf(
        report,
        candidate_name="Fixture Candidate",
        job_title="Platform Engineer",
        tenant_name="Fixture Tenant",
        generated_at=datetime(2026, 8, 23, tzinfo=timezone.utc),
    )
    assert payload.startswith(b"%PDF-")
    reader = PdfReader(io.BytesIO(payload))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def test_a_report_written_before_0107_still_renders():
    """It must not raise, and it must not print an empty section either.

    A heading with nothing under it reads as a section whose content failed to
    load. The honest rendering of a section that did not exist when the
    document was written is no section at all, which is the same rule `_chart`
    follows by not going looking for a fourth chart.
    """
    text = _pdf_text(_legacy_report())
    assert "PRISM Report" in text
    assert report_pdf.CLAIM_EVIDENCE_TITLE not in text
    assert report_pdf.VALIDATION_POINTS_TITLE not in text


def test_a_report_written_before_0107_prints_no_confidence_line():
    """Nothing is substituted for a word that was never computed. The evidence
    set an older report was written from is not reconstructable, so an invented
    word would be the only uncheckable statement on the line."""
    text = _pdf_text(_legacy_report())
    assert "Evidence confidence" not in text
    assert "Sources:" not in text


def test_the_response_model_defaults_both_sections_to_empty_not_null():
    """A client walking the section order must find an empty section it can
    skip rather than a missing key it has to guard."""
    claims = ClaimEvidenceOut.model_validate({})
    points = ValidationPointsOut.model_validate({})
    assert claims.entries == []
    assert points.points == []


def test_a_dimension_without_the_new_fields_still_validates():
    row = DimensionOut(
        name="Distributed Systems",
        description=None,
        grade="Matching",
        remark="x",
    )
    assert row.evidence_confidence is None
    assert row.evidence_sources == []


# -- The chokepoint still binds ----------------------------------------------

def _composed(**kwargs):
    return asyncio.run(_compose(**kwargs))


async def _compose(**kwargs):
    return await siddhi_report.compose_prism(
        embed=None,
        dimensions=[
            {
                "name": "Distributed Systems",
                "category": "must_have",
                "grade": "Matching",
                "remark": "Owned the migration end to end.",
                "evidence_confidence": "moderate",
            }
        ],
        evidence_by_item={
            "Distributed Systems": [
                {"question": "Walk me through it.", "answer": "We moved the ingest."}
            ]
        },
        **kwargs,
    )


def test_the_confidence_word_is_a_cited_statement_like_the_grade():
    """It is a verdict about the record, so it goes through the chokepoint. A
    word outside it could not be asked what it rests on."""
    composed = _composed()
    statements = [
        statement
        for section in composed.sections
        for statement in section["statements"]
        if "evidence confidence" in statement["text"]
    ]
    assert statements
    for statement in statements:
        assert statement["kind"] == citations.KIND_GRADE
        assert statement["evidence_refs"]


def test_an_uncitable_claim_entry_is_withheld_rather_than_rendered():
    """No bypass was added for the new sections. An entry whose refs are empty
    is not rendered, is named (section, not prose) as withheld, and sends the
    report to review. Before the Vivekium release it failed the whole report."""
    composed = _composed(
            claim_evidence={
                "note": "",
                "entries": [
                    {
                        "area": "Distributed Systems",
                        "claim": "Led the migration.",
                        "evidence": "Identified in Resume.",
                        "confidence": "Low",
                        "evidence_refs": [],
                    }
                ],
                "no_claims_statement": None,
            }
        )
    section = next(
        item for item in composed.sections if item["key"] == claim_evidence.SECTION_KEY
    )
    assert not [
        statement for statement in section["statements"] if statement["kind"] != citations.KIND_HEADING
    ]
    assert {held.problem for held in composed.withheld} == {citations.PROBLEM_NO_CITATION}
    assert {held.section for held in composed.withheld} == {claim_evidence.SECTION_KEY}
    assert composed.needs_human_review is True


def test_a_fabricated_citation_in_a_new_section_is_withheld_and_named_apart():
    """A different problem code and finding, because it means something worse:
    an invented ref reads as provenance."""
    composed = _composed(
        claim_evidence={
            "note": "",
            "entries": [
                {
                    "area": "Distributed Systems",
                    "claim": "Led the migration.",
                    "evidence": "Identified in Resume.",
                    "confidence": "Low",
                    "evidence_refs": ["employer:nobody"],
                }
            ],
            "no_claims_statement": None,
        }
    )
    assert {held.problem for held in composed.withheld} == {
        citations.PROBLEM_UNKNOWN_EVIDENCE
    }
    assert "fabricated_citation" in {
        finding["issue"] for finding in composed.review_findings()
    }
    trail = composed.trail()
    for statement in trail["statements"]:
        assert "employer:nobody" not in statement["evidence_refs"]


def test_an_unevidenced_claim_is_composed_as_a_gap_not_as_a_finding():
    """The kind carries the meaning. A GAP cites what was SEARCHED, which is
    what separates "we looked and nothing addressed it" from a verdict."""
    composed = _composed(
        claim_evidence={
            "note": "",
            "entries": [
                {
                    "area": "Distributed Systems",
                    "claim": "Led the migration.",
                    "evidence": claim_evidence.ABSENT_EVIDENCE,
                    "confidence": "Insufficient evidence",
                    "evidence_refs": ["searched:distributed-systems"],
                }
            ],
            "no_claims_statement": None,
        }
    )
    section = next(
        item for item in composed.sections if item["key"] == claim_evidence.SECTION_KEY
    )
    kinds = {
        statement["kind"]
        for statement in section["statements"]
        if statement["text"] == claim_evidence.ABSENT_EVIDENCE
    }
    assert kinds == {citations.KIND_GAP}


def test_the_employer_nodes_join_the_index_the_trail_is_written_from():
    """A ref that is citable but absent from the index is a ref the persisted
    trail cannot explain, and a trail listing fewer nodes than the statements
    cite is worse than none: it reads as complete."""
    employments = [
        {
            "employer_name": "Acme Logistics",
            "designation": "Staff Engineer",
            "status": "verified",
        }
    ]
    records = claim_evidence.employment_claims(employments)
    composed = _composed(
        claim_evidence=claim_evidence.build(records)
        | {
            "entries": [
                entry | {"evidence_refs": list(records[0].evidence_refs)}
                for entry in claim_evidence.build(records)["entries"]
            ]
        },
        extra_nodes=claim_evidence.employment_nodes(employments),
    )
    trail_refs = {node["ref"] for node in composed.trail()["evidence_nodes"]}
    for statement in composed.trail()["statements"]:
        assert set(statement["evidence_refs"]) <= trail_refs


def test_a_composed_report_with_neither_new_section_omits_both():
    """The sections are optional at the chokepoint too. Passing nothing must
    compose nothing rather than an empty section with a heading."""
    composed = _composed()
    keys = {section["key"] for section in composed.sections}
    assert claim_evidence.SECTION_KEY not in keys
    assert validation_points.SECTION_KEY not in keys


def test_the_delivered_payload_with_both_sections_passes_the_number_ban():
    """The serialiser-level ban runs over the WHOLE model, not a list of known
    fields, so two new sections are two new places a number could arrive. This
    constructs the real response model with both populated and lets
    `NumberFreeDelivery` refuse it if anything countable got in."""
    import uuid
    from datetime import datetime, timezone

    from app.schemas.assessments import FunctionalReportOut

    payload = FunctionalReportOut(
        id=uuid.uuid4(),
        job_candidate_link_id=uuid.uuid4(),
        grade="non_managerial",
        ai_score=[],
        overall_grade="Matching",
        overall_summary="Consistent evidence of ownership.",
        must_have=[
            DimensionOut(
                name="Distributed Systems",
                description=None,
                grade="Matching",
                required_level="Matching",
                remark="Owned the migration end to end.",
                evidence_confidence="Moderate",
                evidence_sources=["Assessment responses", "Resume"],
            )
        ],
        nice_to_have=[],
        behavioural=[],
        validation={"fields": []},
        claim_evidence=ClaimEvidenceOut.model_validate(
            {
                "note": "What this person asserted, and what the record holds.",
                "entries": [
                    {
                        "area": "Distributed Systems",
                        "claim": "Led the migration of the ingest path onto Kafka.",
                        "evidence": claim_evidence.ABSENT_EVIDENCE,
                        "confidence": "Insufficient evidence",
                    }
                ],
                "no_claims_statement": None,
            }
        ),
        validation_points=ValidationPointsOut.model_validate(
            {
                "note": validation_points.SECTION_NOTE,
                "points": [
                    {
                        "area": "Judgement under pressure",
                        "driver": validation_points.DRIVER_CONFIDENCE,
                        "confidence": "Low",
                        "reason": "The evidence is this person's own account.",
                        "probe": "Ask for a specific instance and who else saw it.",
                    }
                ],
                "no_points_statement": None,
            }
        ),
        synthesized_at=datetime(2026, 9, 22, tzinfo=timezone.utc),
    )
    assert payload.claim_evidence.entries[0].confidence == "Insufficient evidence"
    assert payload.validation_points.points[0].area == "Judgement under pressure"
