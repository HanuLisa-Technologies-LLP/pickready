"""The PRISM Report's Leadership Alignment section (spec 22.9, 0135).

What it pins, through the ONE composer (`siddhi.report.compose_prism`):

* the section is composed from the frozen context's lines and Miti's grade
  WORDS for leadership-sourced skills, every statement cited: an expectation
  cites its `leadership` node, evidence cites the candidate's answers, a gap
  cites the answers or what was searched, a follow-up reuses the Gap
  Analysis probe;
* a statement whose citation cannot be made is WITHHELD, never rendered, and
  therefore never stored (`stored_section` reads what rendered);
* no leadership context, no section: an older report and a job with no
  leadership input read exactly as they did;
* the delivered payload carrying the section passes the number ban, and both
  renderers place it immediately after Behavioural.
"""
from __future__ import annotations

import asyncio
import io
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pypdf import PdfReader

from app.schemas.assessments import FunctionalReportOut, LeadershipAlignmentOut
from app.services import report_pdf
from app.services.siddhi import citations, leadership_alignment
from app.services.siddhi import report as siddhi_report
from app.services.siddhi import synthesis

CONTEXT_ID = str(uuid.UUID(int=41))

LINES = [
    {"ref": "l1", "source": "leadership_ceo", "scope": "company",
     "text": "We need people who have led the response to a production incident."},
    {"ref": "l2", "source": "leadership_functional_head", "scope": "department",
     "text": "Engineers joining us have shipped a streaming pipeline to production."},
]
LABELS = {"leadership_ceo": "CEO", "leadership_md": "MD", "leadership_functional_head": "Functional Head"}

DIMENSIONS = [
    {"name": "Stream processing", "category": "must_have", "grade": "Matching",
     "remark": "Built the Kafka ingest path and ran it in production."},
    {"name": "Incident ownership", "category": "behavioural", "grade": "Not Matching",
     "remark": "Described an outage without saying what they decided."},
]
EVIDENCE = {
    "Stream processing": [{"question": "Tell me about the pipeline.", "answer": "I built the ingest on Kafka."}],
    "Incident ownership": [{"question": "Tell me about an outage.", "answer": "There was an outage once."}],
}
PROBE = "Ask which decision they made first when the outage began, and why."


def _payload(skills=None, probes=None) -> dict:
    return leadership_alignment.build_payload(
        lines=LINES,
        source_labels=LABELS,
        leadership_skills=skills if skills is not None else [
            {"name": "Stream processing", "source": "leadership_functional_head", "grade": "Matching"},
            {"name": "Incident ownership", "source": "leadership_ceo", "grade": "Not Matching"},
        ],
        probes_by_item=probes if probes is not None else {"Incident ownership": [PROBE]},
    )


def _compose(payload, nodes=None):
    return asyncio.run(
        siddhi_report.compose_prism(
            embed=None,
            dimensions=DIMENSIONS,
            evidence_by_item=EVIDENCE,
            extra_nodes=tuple(
                nodes if nodes is not None else leadership_alignment.nodes(CONTEXT_ID, LINES)
            ),
            leadership=payload,
        )
    )


def _section(composed) -> dict:
    return next(s for s in composed.sections if s["key"] == leadership_alignment.SECTION_KEY)


def test_every_statement_is_cited_and_says_whose_it_is() -> None:
    composed = _compose(_payload())
    section = _section(composed)
    by_kind: dict[str, list[dict]] = {}
    for statement in section["statements"]:
        by_kind.setdefault(statement["kind"], []).append(statement)

    expectations = [s for s in by_kind["finding"]]
    assert [s["text"] for s in expectations] == [
        f"CEO: {LINES[0]['text']}",
        f"Functional Head: {LINES[1]['text']}",
    ]
    assert expectations[0]["evidence_refs"] == ["leadership:l1"]
    (shown,) = by_kind["grade"]
    assert shown["text"] == "Stream processing, from the Functional Head's expectations: Matching"
    assert all(ref.startswith("answer:") or ref.startswith("searched:") for ref in shown["evidence_refs"])
    (gap,) = by_kind["gap"]
    assert gap["text"] == "Incident ownership, from the CEO's expectations: Not Matching"
    (probe,) = by_kind["probe"]
    assert probe["text"] == PROBE
    assert not composed.withheld

    stored = leadership_alignment.stored_section(
        composed.sections, context_id=CONTEXT_ID, context_version=2, digest="d" * 64
    )
    assert stored["context_id"] == CONTEXT_ID and stored["context_version"] == 2
    assert [group["title"] for group in stored["groups"]] == [
        "Company-wide expectations",
        "Department expectations",
        "Evidence demonstrated",
        "Evidence not demonstrated",
        "Follow-up questions",
    ]
    assert stored["note"] == leadership_alignment.NOTE


def test_a_statement_that_cannot_be_cited_is_withheld_and_never_stored() -> None:
    """The leadership nodes are missing from the index: every expectation is
    withheld by the chokepoint, reported without its prose, and the stored
    section carries none of them."""
    composed = _compose(_payload(), nodes=())
    held = [item for item in composed.withheld if item.section == leadership_alignment.SECTION_KEY]
    assert len(held) == 2
    assert {item.problem for item in held} == {citations.PROBLEM_UNKNOWN_EVIDENCE}
    assert composed.needs_human_review is True
    stored = leadership_alignment.stored_section(
        composed.sections, context_id=CONTEXT_ID, context_version=1, digest="d" * 64
    )
    texts = [line["text"] for group in stored["groups"] for line in group["lines"]]
    assert not [text for text in texts if LINES[0]["text"] in text]
    assert "Company-wide expectations" not in [group["title"] for group in stored["groups"]]


def test_no_leadership_skill_says_so_in_words() -> None:
    composed = _compose(_payload(skills=[]))
    texts = [s["text"] for s in _section(composed)["statements"]]
    assert leadership_alignment.NO_LEADERSHIP_SKILLS in texts


def test_no_context_means_no_section() -> None:
    composed = _compose(None)
    assert leadership_alignment.SECTION_KEY not in {s["key"] for s in composed.sections}
    assert leadership_alignment.stored_section(
        composed.sections, context_id=CONTEXT_ID, context_version=1, digest="d" * 64
    ) is None


def test_the_title_is_one_string_in_every_place_it_is_printed() -> None:
    assert synthesis.SECTION_TITLES[leadership_alignment.SECTION_KEY] == leadership_alignment.TITLE
    assert report_pdf.LEADERSHIP_ALIGNMENT_TITLE == leadership_alignment.TITLE
    source = (
        Path(__file__).resolve().parents[2] / "frontend" / "components" / "functional-skills-report.tsx"
    ).read_text(encoding="utf-8")
    assert f'LEADERSHIP_ALIGNMENT_TITLE = "{leadership_alignment.TITLE}"' in source


def test_both_renderers_place_it_directly_after_behavioural() -> None:
    order = report_pdf.SECTION_ORDER
    assert order.index("leadership_alignment") == order.index("behavioural") + 1
    source = (
        Path(__file__).resolve().parents[2] / "frontend" / "components" / "functional-skills-report.tsx"
    ).read_text(encoding="utf-8")
    literal = re.search(r"REPORT_SECTION_ORDER\s*=\s*\[(.*?)\]\s*as const", source, re.S)
    on_screen = tuple(re.findall(r'"([a-z_]+)"', literal.group(1)))
    assert on_screen.index("leadership_alignment") == on_screen.index("behavioural") + 1


def _delivered(alignment: LeadershipAlignmentOut | None) -> FunctionalReportOut:
    return FunctionalReportOut(
        id=uuid.uuid4(),
        job_candidate_link_id=uuid.uuid4(),
        grade="managerial",
        ai_score=[],
        overall_grade="Matching",
        overall_summary="Consistent evidence.",
        must_have=[],
        nice_to_have=[],
        behavioural=[],
        validation={"fields": []},
        leadership_alignment=alignment,
        synthesized_at=datetime(2026, 9, 29, tzinfo=timezone.utc),
    )


def test_the_delivered_section_passes_the_number_ban_and_prints() -> None:
    composed = _compose(_payload())
    stored = leadership_alignment.stored_section(
        composed.sections, context_id=CONTEXT_ID, context_version=3, digest="d" * 64
    )
    out = _delivered(LeadershipAlignmentOut.model_validate(
        {"note": stored["note"], "groups": stored["groups"]}
    ))
    dumped = out.model_dump(mode="json")
    # The stored provenance (id, version, digest) never crosses the boundary.
    assert set(dumped["leadership_alignment"]) == {"note", "groups"}

    pdf = report_pdf.render_report_pdf(
        dumped,
        candidate_name="Fixture Candidate",
        job_title="Platform Engineer",
        tenant_name="Fixture Tenant",
        generated_at=datetime(2026, 9, 29, tzinfo=timezone.utc),
    )
    text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf)).pages)
    assert "Leadership Alignment" in text
    assert "Evidence not demonstrated" in text


def test_a_report_without_the_section_prints_no_heading() -> None:
    dumped = _delivered(None).model_dump(mode="json")
    assert dumped["leadership_alignment"] is None
    pdf = report_pdf.render_report_pdf(
        dumped,
        candidate_name="Fixture Candidate",
        job_title="Platform Engineer",
        tenant_name="Fixture Tenant",
        generated_at=datetime(2026, 9, 29, tzinfo=timezone.utc),
    )
    text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf)).pages)
    assert "Leadership Alignment" not in text
