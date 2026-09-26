"""Change 6: differentiated narratives, and PDF export.

The deterministic `report_skill_evidence` extraction (`services/report_evidence`)
and its two tests were DELETED with the grading pipeline split (PLAN-p5 WP5-D):
it wrote a second, keyword-regex reading of the transcript beside Miti's ledger
that nothing read. The table stays as history (S4).
"""
from __future__ import annotations

import io
from datetime import datetime, timezone
from difflib import SequenceMatcher
from types import SimpleNamespace

import pytest
from pypdf import PdfReader

from app.services import agent_loop
from app.services.assessment_pipeline.types import ProvenanceRecorder
from app.services.report_pdf import FOOTER, render_report_pdf
from app.services.siddhi import remarks


FIXTURE_CASES = [
    (
        "Kafka",
        "The candidate traced Kafka partition skew to one overloaded consumer, rebalanced ownership, and verified recovery through lag metrics during a checkout incident. That concrete diagnosis demonstrates strong distributed-systems judgement. A follow-up should test whether the same reasoning holds during cross-region failure and partial broker availability.",
    ),
    (
        "PostgreSQL",
        "While describing PostgreSQL lock contention, the candidate identified a long transaction, changed the write sequence, and confirmed duplicate billing stopped under peak traffic. The account supports dependable systems reasoning. Interviewers should probe how deadlock risk was measured and which rollback path the candidate personally designed.",
    ),
    (
        "Redis",
        "The Redis example named eviction pressure and a cache-key redesign, but it did not explain how stale reads were detected or which consistency trade-off was accepted at scale. This is partial evidence. A focused discussion should examine invalidation ownership, failure behaviour, and verification under concurrent updates.",
    ),
    (
        "OpenTelemetry",
        "The conversation mentioned OpenTelemetry traces but supplied no owned incident, diagnostic sequence, or resulting production change during a release. Capability therefore remains unresolved. A direct role-specific probe should ask the candidate to isolate one latency regression and distinguish missing observability knowledge from an example they omitted.",
    ),
    (
        "Cloud Run",
        "In the Cloud Run account, the candidate linked concurrency changes to traffic spikes, described testing under load, and explained why minimum capacity was retained. The evidence is relevant and specific. Interviewers should verify cost trade-offs, cold-start measurement, and how the candidate handled a failed rollout.",
    ),
]


@pytest.mark.asyncio
async def test_five_fixture_candidates_have_distinct_banned_phrase_free_narratives(
    monkeypatch,
) -> None:
    responses = iter(text for _, text in FIXTURE_CASES)

    async def _chat(*args, **kwargs):
        return next(responses)

    monkeypatch.setattr(remarks.llm_router, "chat_completion", _chat)
    narratives: list[str] = []
    ratings = [
        "Highly Matching",
        "Matching",
        "Moderately Matching",
        "Not Matching",
        "Matching",
    ]
    for (anchor, _), rating in zip(FIXTURE_CASES, ratings, strict=True):
        remark = await remarks.bounded_remark(
            None,
            "Distributed Systems",
            f"{anchor} production evidence",
            45,
            50,
            rating=rating,
            provenance=ProvenanceRecorder(),
        )
        assert remark.source == remarks.SOURCE_MODEL
        narratives.append(remark.text)

    assert all(45 <= remarks.word_count(value) <= 50 for value in narratives)
    assert all(
        agent_loop.banned_phrase_gate(value, remarks.REPORT_BANNED_PHRASES).ok
        for value in narratives
    )
    similarities = [
        SequenceMatcher(None, left.casefold(), right.casefold()).ratio()
        for index, left in enumerate(narratives)
        for right in narratives[index + 1 :]
    ]
    assert max(similarities) < 0.72


def _report_fixture():
    dimension = {
        "name": "Distributed Systems",
        "grade": "Matching",
        "required_level": "Matching",
        "remark": FIXTURE_CASES[0][1],
    }
    return SimpleNamespace(
        overall_grade="Matching",
        overall_summary=FIXTURE_CASES[4][1],
        radar_charts=[
            {
                "title": title,
                "axes": [
                    {
                        "axis": name,
                        "requirement_index": 3,
                        "candidate_index": 1 + ((chart_index + axis_index) % 4),
                    }
                    for axis_index, name in enumerate(
                        ("Architecture", "Delivery", "Judgement")
                    )
                ],
            }
            for chart_index, title in enumerate(
                (
                    "Overall",
                    "Must-have",
                    "Nice-to-have",
                    "Behavioural Competencies",
                )
            )
        ],
        ai_score=[dimension],
        must_have=[dimension],
        nice_to_have=[dimension],
        behavioural=[dimension],
        suggested_interview_questions=[
            "For Distributed Systems, explain the hardest recovery trade-off you owned."
        ],
        validation={
            "fields": [
                {"label": "Notice period", "value": "Thirty days"},
                {"label": "Role interest", "value": "Platform reliability ownership"},
            ]
        },
    )


def test_pdf_contains_branding_all_sections_charts_and_confidential_footer() -> None:
    payload = render_report_pdf(
        _report_fixture(),
        candidate_name="Change Six Fixture Candidate",
        job_title="Platform Engineer",
        tenant_name="Fixture Tenant — Test Data",
        generated_at=datetime(2026, 8, 7, tzinfo=timezone.utc),
    )
    assert payload.startswith(b"%PDF-")
    reader = PdfReader(io.BytesIO(payload))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert "Vivekium" in text
    assert "Change Six Fixture Candidate" in text
    assert "Platform Engineer" in text
    assert "Must-have" in text
    assert "Nice-to-have" in text
    # Draft v4 removed the retired section completely, including legacy payloads.
    assert "Suggested interview questions" not in text
    assert FOOTER in text

    image_objects = 0
    for page in reader.pages:
        resources = page.get("/Resources")
        xobjects = resources.get("/XObject", {}) if resources else {}
        for value in xobjects.values():
            resolved = value.get_object()
            if resolved.get("/Subtype") == "/Image":
                image_objects += 1
    # THREE charts since 2026-08-23: spec doc 4 lists a radar under Overall,
    # Must-have and Nice-to-have, and lists only a grade and a remark under
    # Behavioural. Asserted EXACTLY rather than as a floor. A floor is what let
    # this sit at ">= 4" while the real question was "which charts", and it
    # would let the fourth come back unnoticed -- which is the whole failure the
    # fixed chart set exists to prevent.
    assert image_objects == 3
