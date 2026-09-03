"""The PRISM Report: its header, its section order, and its three charts.

What each property here is protecting:

  * The HEADER is the document's identity. "Tatva Assessment" is the process
    and "PRISM Report" is the document it produces; printing either name where
    the other belongs teaches a reader they are one thing, which is the single
    confusion the client wrote down twice.
  * The ORDER is fixed, and is written down once per renderer. Two hand-ordered
    renderers drift, and the way it shows up is a recruiter approving a report
    on screen and mailing a PDF that reads differently.
  * THREE charts, not four. The Behavioural section carries a grade and a
    remark, because the spec lists a chart for the other three sections and
    lists none for it.
  * NO NUMBER and NO EM DASH reaches the client, in a PDF exactly as in the UI.
    A PDF is the copy that gets forwarded, so a leak here outlives the screen.
"""
from __future__ import annotations

import io
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pypdf import PdfReader

from app.services import report_pdf


SPEC_ORDER = (
    "ai_score",
    "overall",
    "must_have",
    "nice_to_have",
    "behavioural",
    "gap_analysis",
    "validation",
    # The Proctoring Report is the LAST section (proctoring spec section 7):
    # informational, moves no grade, and sits after everything that does.
    "proctoring",
)

#: The heading each section prints, in the same order. Kept beside the keys so
#: a reordering has to move both halves of the pair.
SECTION_HEADINGS = {
    "ai_score": "AI Score",
    "overall": "Overall Assessment",
    "must_have": "Must-have",
    "nice_to_have": "Nice-to-have",
    "behavioural": "Behavioural Competencies",
    "gap_analysis": "Gap Analysis",
    "validation": "Validation",
    "proctoring": "Proctoring Report",
}

REFERENCE_CODE = "K7QP-2M4X-9TB1"

EM_DASH = chr(8212)


def _dimension(name: str) -> dict:
    return {
        "name": name,
        "grade": "Matching",
        "required_level": "Matching",
        "remark": (
            "Described owning the migration end to end, naming the rollback "
            "they wrote and the on-call week they spent watching it settle."
        ),
    }


def _chart(key: str, title: str) -> dict:
    return {
        "key": key,
        "title": title,
        "axes": [
            {
                "axis": axis,
                "requirement_band": "Matching",
                "requirement_index": 3,
                "candidate_band": "Matching",
                "candidate_index": 3,
            }
            for axis in ("Architecture", "Delivery", "Judgement")
        ],
    }


def _report() -> dict:
    return {
        "reference_code": REFERENCE_CODE,
        "overall_grade": "Matching",
        "overall_summary": "Consistent evidence of ownership across the stack.",
        # Four charts on purpose: a report written before today carries the
        # behavioural chart in its stored payload, and a report is immutable, so
        # the renderer is the only thing that can hold the three-chart rule.
        "radar_charts": [
            _chart("overall", "Overall"),
            _chart("must_have", "Must-have"),
            _chart("nice_to_have", "Nice-to-have"),
            _chart("behavioural", "Behavioural Competencies"),
        ],
        "ai_score": [_dimension("Skills present")],
        "must_have": [_dimension("Distributed Systems")],
        "nice_to_have": [_dimension("Observability")],
        "behavioural": [_dimension("Judgement under pressure")],
        "gap_analysis": {
            "focus_summary": "Spend the interview on incident judgement.",
            "must_have_cap_applied": False,
            "groups": [
                {
                    "category": "must_have",
                    "label": "Must-have gaps",
                    "items": [],
                    "no_gaps_statement": "No gaps in this group.",
                },
                {
                    "category": "behavioural",
                    "label": "Behavioural gaps",
                    "items": [
                        {
                            "name": "Judgement under pressure",
                            "grade": "Moderately Matching",
                            "remark": "Named the outage but not the decision.",
                            "probes": ["Walk through the call you made first."],
                        }
                    ],
                    "no_gaps_statement": None,
                },
            ],
        },
        "validation": {
            "fields": [
                {"label": "Notice period", "value": "Thirty days"},
                {"label": "Role interest", "value": "Platform reliability ownership"},
            ]
        },
    }


def _pdf_text(report: dict | None = None) -> str:
    payload = report_pdf.render_report_pdf(
        report if report is not None else _report(),
        candidate_name="Fixture Candidate",
        job_title="Platform Engineer",
        tenant_name="Fixture Tenant",
        generated_at=datetime(2026, 8, 23, tzinfo=timezone.utc),
    )
    assert payload.startswith(b"%PDF-")
    reader = PdfReader(io.BytesIO(payload))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _image_count(payload: bytes) -> int:
    reader = PdfReader(io.BytesIO(payload))
    total = 0
    for page in reader.pages:
        resources = page.get("/Resources")
        xobjects = resources.get("/XObject", {}) if resources else {}
        for value in xobjects.values():
            if value.get_object().get("/Subtype") == "/Image":
                total += 1
    return total


# ── The header ───────────────────────────────────────────────────────────────

def test_the_header_is_the_documents_name_and_its_expansion_verbatim():
    """A reader who is handed the abbreviation and no expansion cannot tell the
    document from the process that produced it."""
    assert report_pdf.REPORT_TITLE == "PRISM Report"
    assert (
        report_pdf.REPORT_SUBTITLE
        == "Predictive Role Intelligence & Suitability Mapping"
    )
    text = _pdf_text()
    assert "PRISM Report" in text
    assert "Predictive Role Intelligence & Suitability Mapping" in text


def test_the_document_never_calls_itself_the_process():
    """Tatva Assessment is what was run; PRISM Report is what it produced. The
    document naming itself after the framework is the exact conflation the
    client wrote down twice."""
    text = _pdf_text()
    assert "Tatva" not in text
    assert "PPI Assessment Report" not in text


def test_the_reference_code_is_printed_on_the_report():
    """A printed report and a row on a screen are matched by eye. Without the
    code on the page there is nothing to match them by."""
    assert REFERENCE_CODE in _pdf_text()


def test_a_report_without_a_reference_code_still_renders():
    """The code is a display aid. One that could take the whole download with
    it would be worse than not printing it."""
    report = _report()
    report.pop("reference_code")
    assert "PRISM Report" in _pdf_text(report)


# ── The section order ────────────────────────────────────────────────────────

def test_the_pdf_emits_the_spec_section_order():
    assert report_pdf.SECTION_ORDER == SPEC_ORDER


def test_gap_analysis_precedes_validation_on_the_rendered_page():
    """Reverses the earlier order. Validation is the candidate's own unrated
    submission, so the action plan has to sit beside the grades it was drawn
    from rather than after a block of uninterpreted form answers.

    Asserted on the RENDERED text, not on the constant: a constant the renderer
    does not actually walk would pass while the PDF read the other way round.
    """
    text = _pdf_text()
    assert 0 <= text.index("Gap Analysis") < text.index("Validation")


def test_every_section_appears_once_in_the_documented_order():
    text = _pdf_text()
    positions = []
    for key in report_pdf.SECTION_ORDER:
        heading = SECTION_HEADINGS[key]
        assert heading in text, heading
        positions.append(text.index(heading))
    assert positions == sorted(positions), dict(
        zip(report_pdf.SECTION_ORDER, positions)
    )


def test_the_screen_and_the_pdf_agree_on_the_section_order():
    """Both orders are read out of their own source rather than restated here,
    so this cannot pass while the two renderers disagree.

    The failure it prevents is specific: a recruiter approves a report on
    screen, downloads it, and mails a client a document whose sections are in a
    different order from the one they read.
    """
    source = _frontend_report_source()
    literal = re.search(
        r"REPORT_SECTION_ORDER\s*=\s*\[(.*?)\]\s*as const", source, re.S
    )
    assert literal, "REPORT_SECTION_ORDER is no longer a plain array literal"
    on_screen = tuple(re.findall(r'"([a-z_]+)"', literal.group(1)))
    assert on_screen == report_pdf.SECTION_ORDER


def _frontend_report_source() -> str:
    """The on-screen renderer's source, or a skip that says why.

    The backend dev container mounts backend/ alone, so the frontend tree is
    genuinely absent there. CI checks out the whole repo and runs pytest from
    backend/, which is where this comparison has to hold.
    """
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "frontend" / "components" / "functional-skills-report.tsx"
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    pytest.skip("frontend tree not mounted; the comparison runs in CI")


# ── Three charts ─────────────────────────────────────────────────────────────

def test_three_charts_are_rendered_and_behavioural_is_not_one_of_them():
    """The spec lists a chart under Overall Assessment, Must-have and
    Nice-to-have, and lists a grade and a remark under Behavioural. This
    supersedes the earlier exactly-four rule."""
    assert report_pdf.RENDERED_CHART_KEYS == ("overall", "must_have", "nice_to_have")
    assert "behavioural" not in report_pdf.RENDERED_CHART_KEYS


def test_a_report_written_when_four_were_generated_still_shows_three():
    """A report is immutable, so the fourth chart is still in the stored
    payload of every report written before today. Filtering at the generator
    would have left old reports showing four and new ones three."""
    payload = report_pdf.render_report_pdf(
        _report(),
        candidate_name="Fixture Candidate",
        job_title="Platform Engineer",
        tenant_name="Fixture Tenant",
        generated_at=datetime(2026, 8, 23, tzinfo=timezone.utc),
    )
    assert _image_count(payload) == 3


def test_a_chart_stored_before_keys_existed_is_still_found_by_its_title():
    """Identifying charts by key alone would drop every chart from a payload
    written before the key existed. A report is immutable and cannot be
    regenerated, so that loss would be permanent and silent."""
    report = _report()
    for chart in report["radar_charts"]:
        chart.pop("key")
    payload = report_pdf.render_report_pdf(
        report,
        candidate_name="Fixture Candidate",
        job_title="Platform Engineer",
        tenant_name="Fixture Tenant",
        generated_at=datetime(2026, 8, 23, tzinfo=timezone.utc),
    )
    assert _image_count(payload) == 3


# ── Nothing numeric, nothing dashed ──────────────────────────────────────────

def test_no_em_dash_survives_into_the_rendered_pdf():
    """The rule covers data, not only labels, and a PDF is the copy that gets
    forwarded. The footer broke it for the whole life of the file because the
    character was assembled from chr(8212), which a source sweep cannot see."""
    assert EM_DASH not in _pdf_text()
    assert EM_DASH not in report_pdf.FOOTER


def test_no_grade_is_stated_as_a_number_or_a_fraction():
    """Grades are words. A score, a percentage or a "3/4" reaching a client is
    the one leak that cannot be walked back once the PDF is sent."""
    text = _pdf_text()
    assert not re.search(r"\d+\s*%", text)
    assert not re.search(r"\b\d{1,3}\s*/\s*\d{1,3}\b", text)
    assert not re.search(r"\b(?:score|rating)\b\s*[:=]?\s*\d", text, re.I)


def test_no_band_index_is_ever_written_as_text():
    """The band index is the documented exception to the no-numbers rule, and
    its licence is narrow: it is a radius inside a rasterised chart. The moment
    it appears as a character on the page it is a disclosed score."""
    text = _pdf_text()
    for grade in ("Matching", "Moderately Matching", "Not Matching"):
        assert not re.search(rf"{grade}\s*[:(\-]?\s*\d", text)
