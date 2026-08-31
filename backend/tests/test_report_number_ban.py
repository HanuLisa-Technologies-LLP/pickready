"""The serialiser-level number ban, walked over every export format (D8).

    spec-doc6 §4.5: "A test walks every field of a rendered PRISM payload, in
    every export format, and asserts no numeric score, band value, percentage,
    or dimension figure is present. This is the enforcement of D8."

    spec-doc6 D8: the Ready Pick Score "renders in the candidate list and
    nowhere else. It must be technically impossible for it to enter a delivered
    report."

FOUR FORMATS, AND EACH ONE IS A SEPARATE PIECE OF CODE THAT COULD LEAK
------------------------------------------------------------------------
  json        `FunctionalReportOut`, the API response the screen reads
  pdf         `report_pdf.render_report_pdf`, the copy that gets forwarded
  email_body  the notification, which travels further than any other surface
  attachment  the PDF again, under a client-visible filename

They are tested separately rather than through one wrapper because that is how
they are CALLED: the download route reaches the renderer directly, so a check
that only lived in a delivery wrapper would be a check the live route skips.

WHAT THIS FILE ASSERTS THAT A GREP COULD NOT
-----------------------------------------------
That the walker visited the field. A "no violations" assertion cannot tell a
clean payload from a walker that stopped at the first level, so
`test_the_walk_reaches_every_leaf_of_the_payload` pins the reached-path set and
`test_a_number_hidden_at_every_depth_is_found` plants one at each depth.
"""
from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from pypdf import PdfReader

from app.schemas.assessments import FunctionalReportOut
from app.schemas.reports import ReadyPickNoteOut
from app.services import report_pdf
from app.services.siddhi import delivery, numbers

GENERATED_AT = datetime(2026, 8, 29, tzinfo=timezone.utc)
EM_DASH = chr(8212)


def _dimension(name: str) -> dict:
    return {
        "name": name,
        "description": "What the job asks of this criterion.",
        "grade": "Matching",
        "required_level": "Matching",
        "remark": (
            "Described owning the migration end to end, naming the rollback "
            "they wrote and the on-call week they spent watching it settle."
        ),
    }


def _axes() -> list[dict]:
    return [
        {
            "axis": axis,
            "requirement_band": "Matching",
            "requirement_index": 3,
            "candidate_band": "Matching",
            "candidate_index": 3,
        }
        for axis in ("Architecture", "Delivery", "Judgement")
    ]


def _payload() -> dict:
    return {
        "id": uuid.uuid4(),
        "job_candidate_link_id": uuid.uuid4(),
        "reference_code": "K7QP-2M4X-9TB1",
        "grade": "non_managerial",
        "ai_score": [_dimension("Skills present")],
        "overall_grade": "Matching",
        "overall_summary": "Consistent evidence of ownership across the stack.",
        "must_have": [_dimension("Distributed Systems")],
        "nice_to_have": [_dimension("Observability")],
        "behavioural": [_dimension("Judgement under pressure")],
        "technical": [],
        "validation": {
            "captured": True,
            "fields": [
                # A CTC and a notice period are numbers the candidate typed.
                # They are the one thing the report reproduces untouched.
                {"label": "Current CTC", "value": "1800000", "group": "Application"},
                {"label": "Notice period", "value": "60 days", "group": "Application"},
                {
                    "label": "Why does this role interest you?",
                    "value": "I want to own reliability for a platform at scale.",
                    "group": "Application",
                },
            ],
        },
        "gap_analysis": {
            "focus_summary": "Spend the interview on incident judgement.",
            "must_have_cap_applied": False,
            "groups": [
                {
                    "category": "must_have",
                    "label": "Must-have",
                    "items": [],
                    "no_gaps_statement": "No Must-have gaps identified.",
                },
                {
                    "category": "behavioural",
                    "label": "Behavioural Competencies",
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
        "suggested_interview_questions": [],
        "radar_charts": [
            {"key": "overall", "title": "Overall", "axes": _axes()},
            {"key": "must_have", "title": "Must-have", "axes": _axes()},
            {"key": "nice_to_have", "title": "Nice-to-have", "axes": _axes()},
        ],
        "radar_bands": ["Highly Matching", "Matching", "Moderately Matching", "Not Matching"],
        "radar_series": ["Job requirement", "Candidate"],
        "synthesized_at": GENERATED_AT,
        "immutable": True,
    }


def _report_out() -> FunctionalReportOut:
    return FunctionalReportOut(**_payload())


def _pdf_text(payload: bytes) -> str:
    reader = PdfReader(io.BytesIO(payload))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


# ── The clean report survives every format ───────────────────────────────────


def test_a_clean_report_serialises_in_every_export_format() -> None:
    """The ban must not be so blunt that a real report cannot be delivered.

    Asserted first, because a check that refuses everything passes every leak
    test and ships nothing.
    """
    report = _report_out()
    assert delivery.prism_json(report)["overall_grade"] == "Matching"
    pdf = delivery.prism_pdf(
        report,
        candidate_name="Fixture Candidate",
        job_title="Platform Engineer",
        tenant_name="Fixture Tenant",
        generated_at=GENERATED_AT,
    )
    assert pdf.startswith(b"%PDF-")
    body = delivery.prism_email_body(
        report, candidate_name="Fixture Candidate", job_title="Platform Engineer"
    )
    assert "PRISM Report" in body
    filename, attached, media = delivery.prism_attachment(
        report,
        candidate_name="Fixture Candidate",
        job_title="Platform Engineer",
        tenant_name="Fixture Tenant",
        generated_at=GENERATED_AT,
    )
    assert filename == "PRISM-Report-Fixture-Candidate.pdf"
    assert media == "application/pdf"
    assert attached.startswith(b"%PDF-")


@pytest.mark.asyncio
async def test_delivery_runs_the_gate_first_then_every_format() -> None:
    """One call, in the order the rules have to happen in: G4, then the four
    exports, each checked as it is produced."""

    class _Result:
        def scalars(self):
            return self

        def first(self):
            return None

    class _Session:
        async def execute(self, statement):  # noqa: ANN001 - a stub, not an engine
            return _Result()

    class _Report:
        id = "report-1"
        job_candidate_link_id = "link-1"
        needs_human_review = False
        synthesized_at = GENERATED_AT

    delivered = await delivery.deliver(
        _Session(),
        _Report(),
        _report_out(),
        candidate_name="Fixture Candidate",
        job_title="Platform Engineer",
        tenant_name="Fixture Tenant",
    )
    assert delivered["clearance"]["gate"] == "G4_human_review"
    assert delivered["json"]["overall_grade"] == "Matching"
    assert delivered["pdf"].startswith(b"%PDF-")
    assert "PRISM Report" in delivered["email_body"]
    assert delivered["attachment"]["media_type"] == "application/pdf"


@pytest.mark.asyncio
async def test_a_report_with_no_synthesis_timestamp_is_not_dated_by_the_delivery() -> None:
    """Dating the document `now` would put a date on a client's permanent record
    that no stage of the pipeline ever wrote."""

    class _Result:
        def scalars(self):
            return self

        def first(self):
            return None

    class _Session:
        async def execute(self, statement):  # noqa: ANN001 - a stub, not an engine
            return _Result()

    class _Report:
        id = "report-1"
        job_candidate_link_id = "link-1"
        needs_human_review = False
        synthesized_at = None

    payload = _payload()
    payload.pop("synthesized_at")
    with pytest.raises(delivery.DeliveryBlocked) as caught:
        await delivery.deliver(
            _Session(),
            _Report(),
            payload,
            candidate_name="Fixture Candidate",
            job_title="Platform Engineer",
            tenant_name="Fixture Tenant",
        )
    assert "no synthesis timestamp" in str(caught.value)


# ── The walk actually walks ──────────────────────────────────────────────────


def test_the_walk_reaches_every_leaf_of_the_payload() -> None:
    """A "no violations" result proves nothing if the walker stopped early.

    Every field of the response model has to be reached, including the ones
    nested three containers deep, which is where a score would actually be
    hidden.
    """
    paths = set(numbers.known_paths(_report_out()))
    for expected in (
        "payload.overall_summary",
        "payload.ai_score[0].remark",
        "payload.must_have[0].grade",
        "payload.radar_charts[0].axes[0].candidate_index",
        "payload.gap_analysis.groups[1].items[0].probes[0]",
        "payload.validation.fields[0].value",
        "payload.radar_bands[0]",
        "payload.immutable",
    ):
        assert expected in paths, expected


@pytest.mark.parametrize(
    "mutate,where",
    [
        (lambda p: p.__setitem__("ready_pick_score", 82), "top level"),
        (lambda p: p["must_have"][0].__setitem__("score", 74), "a dimension row"),
        (
            lambda p: p["gap_analysis"]["groups"][1]["items"][0].__setitem__(
                "score", 61
            ),
            "a gap item",
        ),
        (
            lambda p: p["gap_analysis"].__setitem__("composite", 68.5),
            "the gap section",
        ),
        (lambda p: p["radar_charts"][0].__setitem__("score", 74), "a chart"),
    ],
)
def test_a_number_hidden_at_every_depth_is_found(mutate, where) -> None:
    """One planted number per depth. Depth is exactly what a leak relies on."""
    payload = _payload()
    mutate(payload)
    violations = numbers.scan(payload)
    assert violations, where
    assert any(v.rule == numbers.RULE_NUMERIC_FIELD for v in violations)


# ── The Ready Pick Score, specifically ───────────────────────────────────────


def test_an_undeclared_score_never_enters_the_json_response_at_all() -> None:
    """The first of two independent stops. A response model is a closed shape,
    so a score handed to it from a route is dropped before serialisation."""
    payload = _payload()
    payload["ready_pick_score"] = 82
    assert "ready_pick_score" not in FunctionalReportOut(**payload).model_dump()


def test_a_declared_score_field_makes_the_response_model_refuse_to_construct() -> None:
    """The second stop, and the one D8 is actually about.

    The realistic leak is not a stray dict key; it is somebody adding
    `ready_pick_score: int` to the response model because the dashboard needs
    it, in a release where the report and the candidate list are being worked on
    together. That change now fails at construction, on every report, rather
    than shipping the number to a client.
    """

    class ReportWithAScore(FunctionalReportOut):
        ready_pick_score: int = 82

    # Pydantic wraps a validator's ValueError, so the type on the way out is
    # ValidationError and the ban's own message travels inside it. Asserted on
    # the message rather than the class for exactly that reason: what has to
    # survive is the field name, which is what tells the next reader which line
    # to revert.
    with pytest.raises(ValidationError) as caught:
        ReportWithAScore(**_payload())
    assert "ready_pick_score" in str(caught.value)
    assert "numeric_field" in str(caught.value)


def test_the_ready_pick_score_cannot_enter_the_pdf_or_the_attachment() -> None:
    payload = _payload()
    payload["ready_pick_score"] = 82
    for call in (report_pdf.render_report_pdf, delivery.prism_pdf):
        with pytest.raises(numbers.NumberInDeliveredReport):
            call(
                payload,
                candidate_name="Fixture Candidate",
                job_title="Platform Engineer",
                tenant_name="Fixture Tenant",
                generated_at=GENERATED_AT,
            )


def test_a_band_value_and_a_confidence_number_are_both_refused() -> None:
    payload = _payload()
    payload["band_value"] = 3
    payload["confidence_score"] = 0.82
    violations = numbers.scan(payload)
    assert {v.path for v in violations} == {"payload.band_value", "payload.confidence_score"}


# ── Prose, not only fields ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "remark",
    [
        "They scored 82 on distributed systems and should be advanced.",
        "Rated 7/10 against the required level for this competency.",
        "This candidate sits in the top 12% of the applicant pool.",
        "Matching (74) on the evidence recorded in the conversation.",
        "Cut checkout latency by 30% over the migration window.",
        "A 68 percent match against the role as defined.",
    ],
)
def test_score_shaped_prose_is_refused_wherever_it_appears(remark: str) -> None:
    """A number does not have to be a field. The remark is written by a model
    that has just been asked to assess somebody, which is precisely where
    "demonstrates strong 8/10 capability" comes from."""
    payload = _payload()
    payload["must_have"][0]["remark"] = remark
    violations = numbers.scan(payload)
    assert violations, remark
    assert all(v.rule == numbers.RULE_SCORE_PROSE for v in violations)


def test_ordinary_technical_language_in_a_remark_is_not_refused() -> None:
    """The hard part is the distinction, not the detection. A remark naming the
    candidate's own work is the report doing its job."""
    payload = _payload()
    payload["must_have"][0]["remark"] = (
        "Brought p99 latency under 200ms on the checkout path by moving the "
        "session lookup behind a cache, and named the rollback they wrote."
    )
    assert numbers.scan(payload) == []


def test_a_score_in_an_email_body_is_refused() -> None:
    with pytest.raises(numbers.NumberInDeliveredReport):
        numbers.assert_text_clean(
            "The candidate scored 82 and is Highly Matching.",
            where="prism.email_body",
        )


def test_the_email_body_states_that_a_report_exists_and_nothing_it_contains() -> None:
    """An email is forwarded further than any other surface in this product, and
    a grade quoted in one outlives every access control on the document."""
    body = delivery.prism_email_body(
        _report_out(), candidate_name="Fixture Candidate", job_title="Platform Engineer"
    )
    for grade in ("Highly Matching", "Moderately Matching", "Not Matching"):
        assert grade not in body
    assert "Matching" not in body
    assert EM_DASH not in body


# ── The two exemptions, both narrow ──────────────────────────────────────────


def test_the_radar_band_index_is_allowed_only_inside_a_chart() -> None:
    """A radar has no geometry without a radius, and the four grades ARE the
    axis. The licence is that narrow: the same field name anywhere else in the
    payload is a disclosed score."""
    assert numbers.scan(_payload()) == []
    payload = _payload()
    payload["candidate_index"] = 3
    violations = numbers.scan(payload)
    assert [v.path for v in violations] == ["payload.candidate_index"]


def test_the_band_index_never_appears_as_a_character_on_the_page() -> None:
    """The exemption is for a rendering coordinate, not for a printed number.
    The moment it appears as text it is a disclosed score."""
    text = _pdf_text(
        delivery.prism_pdf(
            _report_out(),
            candidate_name="Fixture Candidate",
            job_title="Platform Engineer",
            tenant_name="Fixture Tenant",
            generated_at=GENERATED_AT,
        )
    )
    for grade in ("Highly Matching", "Moderately Matching", "Not Matching", "Matching"):
        assert f"{grade} 3" not in text
        assert f"{grade}: 3" not in text


def test_the_candidates_own_submission_may_carry_numbers() -> None:
    """A current CTC is an amount and a notice period is a count of days. The
    Validation section is the candidate's own unrated submission reproduced
    exactly as submitted, and a product that withheld or reworded it would have
    falsified an application field in a document a client decides from."""
    payload = _payload()
    payload["validation"]["fields"].append(
        {"label": "Expected CTC", "value": 2400000, "group": "Application"}
    )
    assert numbers.scan(payload) == []


def test_a_score_smuggled_into_the_validation_section_is_still_refused() -> None:
    """The verbatim exemption relaxes the numeric-field rule and hands over to
    the score-shaped-key rule. A Ready Pick Score does not become acceptable by
    being put somewhere the candidate's own answers live."""
    payload = _payload()
    payload["validation"]["ready_pick_score"] = 82
    violations = numbers.scan(payload)
    assert [v.rule for v in violations] == [numbers.RULE_SCORE_KEY]


def test_a_candidates_own_words_are_never_scanned_for_prose_violations() -> None:
    """Refusing to deliver a report because an applicant wrote "I scored 82 in
    my entrance exam" would withhold the document over the one section the
    product promises to reproduce untouched."""
    payload = _payload()
    payload["validation"]["fields"][2]["value"] = (
        "I scored 82 in the entrance exam and placed in the top 5% of my batch."
    )
    assert numbers.scan(payload) == []


# ── Booleans, dates and identifiers are not numbers ──────────────────────────


def test_a_boolean_flag_is_not_a_score() -> None:
    """`isinstance(True, int)` is True, which would otherwise make every
    `immutable: true` in the product a violation."""
    assert numbers.scan({"immutable": True, "must_have_cap_applied": False}) == []


def test_a_timestamp_and_a_uuid_are_not_scores() -> None:
    assert numbers.scan(
        {"synthesized_at": GENERATED_AT, "id": uuid.uuid4()}
    ) == []


# ── The note the dashboard renders ───────────────────────────────────────────


def test_the_ready_pick_note_response_carries_the_sentence_and_no_refs() -> None:
    """A ref is an internal audit locator. It identifies a row and authorises
    nothing, and a locator shipped to a browser is one somebody will eventually
    read back as permission."""
    note = ReadyPickNoteOut(sentence="Strongest on Distributed Systems, graded Matching.")
    assert note.model_dump() == {
        "sentence": "Strongest on Distributed Systems, graded Matching."
    }
    assert "evidence_refs" not in note.model_dump()
