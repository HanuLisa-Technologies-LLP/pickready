"""The eight columns, their vocabularies, and every documented pending state.

Pure. No database, no HTTP, no model. `dashboard.assemble_row` takes a plain
mapping precisely so the specification's two state tables (column 4's six
states, and the four row states) can each be asserted directly rather than
reached through a seeded fixture.

WHAT THIS FILE IS DEFENDING
---------------------------
Four of the eight columns are filled by agents that were not on a live path
when this surface was built. The failure that invites is not a crash: it is a
plausible-looking default. A dashboard that renders `50 . Consider with
Reservations` for a candidate nobody has assessed is worse than one that
renders nothing, because a recruiter acts on it. Every test below that names a
"pending" state is defending exactly that.

NO REAL NAMES ANYWHERE (spec-doc6 C14). The Dashboard Specification's sample
row uses a real person's name; the fixtures here are obviously synthetic and
the suite asserts that the name from the document appears nowhere in this
package.
"""
from __future__ import annotations

import pathlib
import uuid

import pytest

from app.services import dashboard, hiring_pipeline, rating
from app.services.hiring import gates as hiring_gates
from app.services.hiring import prescreen

# Obviously synthetic. A reader must never wonder whether this is somebody.
FIXTURE_NAME = "Test Candidate Zero"
TENANT = uuid.UUID("0a0a0a0a-0000-4000-8000-00000000000a")
JOB = uuid.UUID("0b0b0b0b-0000-4000-8000-00000000000b")
CANDIDATE = uuid.UUID("0c0c0c0c-0000-4000-8000-00000000000c")
LINK = uuid.UUID("0d0d0d0d-0000-4000-8000-00000000000d")
EVALUATION = uuid.UUID("0e0e0e0e-0000-4000-8000-00000000000e")


def row(**overrides):
    """One queried row, with every column at its emptiest honest value.

    The DEFAULT is the state a candidate is in the moment they apply: a resume
    ingested, nothing graded, nothing assessed. That is the common case for the
    whole of this phase, so it is what a test has to opt OUT of.
    """
    base = {
        "link_id": LINK,
        "tenant_id": TENANT,
        "job_id": JOB,
        "job_title": "Staff Platform Engineer",
        "candidate_id": CANDIDATE,
        "full_name": FIXTURE_NAME,
        "source_type": "applied",
        "pre_screen_grade": None,
        "status": hiring_pipeline.APPLIED,
        "created_at": None,
        "archived_at": None,
        "evaluation_id": None,
        "confidence": None,
        "evaluated_at": None,
        "ready_pick_score": None,
        "ready_pick_note": None,
        "under_integrity_review": False,
        "team_review_count": 0,
        "own_verdict": None,
        "own_verdict_at": None,
    }
    base.update(overrides)
    return base


# ── The order of the eight columns ───────────────────────────────────────────


def test_the_eight_columns_are_in_the_specified_scanning_order():
    """Scanning order follows decision logic, not backend computation order.

    Pinned as a literal list rather than as a length, because the value being
    protected is the ORDER: Ready Pick Score sitting after Ready Pick Note
    would still be eight columns and would break the triage read.
    """
    assert dashboard.COLUMNS == (
        "candidate",
        "source",
        "pre_screen_grade",
        "ready_pick_score",
        "ready_pick_note",
        "ready_pick_profile",
        "team_review",
        "stage",
    )


def test_every_column_has_a_spoken_label():
    """Colour is never the sole carrier of meaning, and neither is position."""
    assert set(dashboard.COLUMN_SCREEN_READER_LABELS) == set(dashboard.COLUMNS)
    # Column 1 is the one the specification singles out.
    assert (
        dashboard.COLUMN_SCREEN_READER_LABELS[dashboard.COLUMN_CANDIDATE]
        == "Candidate Code Name"
    )


# ── Column 3: the Pre-Screen Grade ───────────────────────────────────────────


def test_the_pre_screen_vocabulary_is_the_graders_own():
    """One vocabulary, defined where it is written, imported where it is read.

    `services/tiers.py` is the reason this is a test: a rendering layer that
    keeps its own copy of a four-value scale is how the product ended up with
    two scales that disagreed for 69.5% of its rows.
    """
    assert dashboard.PRE_SCREEN_GRADES is prescreen.GRADES
    assert set(dashboard.PRE_SCREEN_LABELS) == set(prescreen.GRADES)


def test_an_ungraded_application_is_not_rendered_as_hold():
    """NULL means "not pre-screened". `Hold` means "graded, a person should
    look". Rendering them the same way tells a recruiter an untriaged backlog
    has been triaged."""
    ungraded = dashboard.assemble_row(row())
    held = dashboard.assemble_row(row(pre_screen_grade=prescreen.GRADE_HOLD))

    assert ungraded.pre_screen_grade is None
    assert held.pre_screen_grade == prescreen.GRADE_HOLD
    assert ungraded.pre_screen_label != held.pre_screen_label
    assert "not been graded" in ungraded.pre_screen_label


def test_an_unknown_pre_screen_grade_raises_rather_than_rendering():
    """The database CHECK already refuses one, so arriving here means the
    vocabularies have diverged. A dashboard that quietly renders the unknown
    value is how that goes unnoticed for a release."""
    with pytest.raises(ValueError):
        dashboard.pre_screen_label("A+")


def test_the_pre_screen_vocabulary_contains_no_rejecting_value():
    """No pre-screen has ever rejected anybody, and the weakest value says so.

    Asserted on the words rather than on the count: a fifth value called
    `Reject` would keep every other test in this file green.
    """
    assert not {"reject", "rejected", "fail", "failed"} & {
        grade.lower() for grade in dashboard.PRE_SCREEN_GRADES
    }


# ── Column 4: the Ready Pick Score ───────────────────────────────────────────


@pytest.mark.parametrize(
    "score,expected",
    [
        (100, dashboard.BAND_STRONG),
        (85, dashboard.BAND_STRONG),
        (84, dashboard.BAND_READY),
        (72, dashboard.BAND_READY),
        (71, dashboard.BAND_RESERVATIONS),
        (60, dashboard.BAND_RESERVATIONS),
        (59, dashboard.BAND_NOT_RECOMMENDED),
        (0, dashboard.BAND_NOT_RECOMMENDED),
    ],
)
def test_band_boundaries_are_inclusive_upward(score, expected):
    """claude.md rule 8, and the same direction `rating.grade_for_percent` uses.

    Each boundary is asserted from BOTH sides. A cut-point test that only
    checks the value at the boundary passes for an off-by-one in either
    direction.
    """
    assert dashboard.band_for_score(score) == expected


def test_a_better_score_never_earns_a_worse_band():
    """The `tiers.py` guard, applied to the new vocabulary.

    Swept across the whole range rather than sampled: the defect that made this
    test necessary was two adjacent bands SWAPPED, which every plausible sample
    of three points misses.
    """
    ranks = {band: index for index, band in enumerate(dashboard.BAND_ORDER)}
    previous = None
    for score in range(0, 101):
        rank = ranks[dashboard.band_for_score(score)]
        if previous is not None:
            assert rank <= previous, f"band got worse as the score rose, at {score}"
        previous = rank


def test_the_band_never_inverts_against_the_assessment_grade():
    """Two vocabularies over one number line must agree on DIRECTION.

    They are allowed to disagree on where the lines fall -- the dashboard cuts
    at 85/72/60 and `rating` at 90/75/60, and D8 makes them different artifacts
    -- but a score that grades better must never band worse. That is exactly
    the property `tiers.py` violated.
    """
    grade_rank = {grade: index for index, grade in enumerate(rating.GRADES)}
    band_rank = {band: index for index, band in enumerate(dashboard.BAND_ORDER)}
    pairs = [
        (grade_rank[rating.grade_for_percent(score)],
         band_rank[dashboard.band_for_score(score)])
        for score in range(0, 101)
    ]
    for (grade_a, band_a), (grade_b, band_b) in zip(pairs, pairs[1:]):
        if grade_b < grade_a:  # the grade improved
            assert band_b <= band_a, "the grade improved while the band worsened"


def test_the_two_vocabularies_share_no_word():
    """A recruiter reading "Matching" must never have to ask which scale it is.

    The dashboard band and the assessment grade are different artifacts (D8),
    and the cheapest way to keep them distinguishable on screen is for them to
    have no word in common.
    """
    assert dashboard.vocabularies_are_disjoint()


def test_an_unassessed_candidate_is_pending_and_never_zero():
    """The entire reason column 4 has a pending state.

    "We have not assessed this person" and "we assessed this person and they
    scored badly" are different sentences, and collapsing them slanders every
    candidate still in the queue.
    """
    assembled = dashboard.assemble_row(row())
    assert assembled.ready_pick_score is None
    assert assembled.band == dashboard.BAND_PENDING
    assert assembled.band_label == "Pending Ready Pick Profile"
    assert assembled.band != dashboard.BAND_NOT_RECOMMENDED


def test_the_pending_state_is_announced_with_its_meaning():
    """A grey pill reading two words tells a screen-reader user nothing."""
    assembled = dashboard.assemble_row(row())
    assert "assessment in progress" in assembled.band_screen_reader_label.lower()


def test_under_review_is_announced_with_its_meaning():
    """The specification names this one explicitly: announced as "Status: Under
    Review, awaiting integrity disposition", not as a visual red."""
    assembled = dashboard.assemble_row(row(under_integrity_review=True))
    assert assembled.band == dashboard.BAND_UNDER_REVIEW
    assert (
        assembled.band_screen_reader_label
        == "Status: Under Review, awaiting integrity disposition"
    )


def test_under_review_withholds_the_number_even_when_one_exists():
    """A score printed beside "Under Review" invites a recruiter to act on it,
    which is the one thing the lock exists to prevent."""
    assembled = dashboard.assemble_row(
        row(
            under_integrity_review=True,
            ready_pick_score=88,
            evaluation_id=EVALUATION,
            confidence="high",
        )
    )
    assert assembled.ready_pick_score is None
    assert assembled.band == dashboard.BAND_UNDER_REVIEW


@pytest.mark.parametrize(
    "confidence,expected",
    [
        ("high", dashboard.CONFIDENCE_FILLED),
        ("medium", dashboard.CONFIDENCE_FILLED),
        ("low", dashboard.CONFIDENCE_OUTLINE),
        (None, dashboard.CONFIDENCE_GRAYED),
    ],
)
def test_the_confidence_dot_follows_the_aggregators_own_word(confidence, expected):
    assert dashboard.confidence_indicator(confidence) == expected


def test_the_confidence_dot_is_always_accompanied_by_words():
    """Colour and shape are never the sole carrier of meaning."""
    for indicator in dashboard.CONFIDENCE_INDICATORS:
        assert dashboard.CONFIDENCE_LABELS[indicator].strip()


def test_no_score_range_is_invented():
    """The specification asks for `82 [76 to 88]` and nothing in the engine
    publishes an interval.

    A bracket computed from the confidence word would be a number with no
    provenance printed beside one that has some. The row carries a null range
    and a sentence saying why.
    """
    assembled = dashboard.assemble_row(
        row(evaluation_id=EVALUATION, ready_pick_score=82, confidence="high")
    )
    assert assembled.ready_pick_score == 82
    assert assembled.score_range is None
    assert "no uncertainty interval" in assembled.score_range_note.lower()


# ── Column 5: the Ready Pick Note ────────────────────────────────────────────


def test_the_note_is_pending_until_siddhi_writes_one():
    assembled = dashboard.assemble_row(row())
    assert assembled.note_is_pending
    assert assembled.note == dashboard.NOTE_PENDING


def test_the_note_key_is_the_one_siddhi_writes():
    """One producer, two consumers, and the key stated in both modules.

    `siddhi/synthesis` computes the sentence and writes it; this module reads
    it. The constant is restated rather than imported so neither service
    acquires an import edge to the other, which means the agreement needs a
    test on BOTH sides: deleting either one must not silently unpin it.
    """
    from app.services.siddhi import synthesis

    assert dashboard.READY_PICK_NOTE_KEY == synthesis.READY_PICK_NOTE_KEY


def test_the_dashboard_never_imports_the_report_schemas():
    """The two artefacts stay apart at the module level too (spec-doc6 C10).

    A report payload with a score field on it now refuses to construct, and the
    dashboard's one permitted number must reach a client through the
    dashboard's own schema rather than by borrowing the report's.
    """
    for module in ("app/schemas/dashboard.py", "app/services/dashboard.py",
                   "app/api/dashboard.py"):
        source = (
            pathlib.Path(__file__).resolve().parents[1] / module
        ).read_text(encoding="utf-8")
        assert "schemas.reports" not in source
        assert "schemas import reports" not in source


def test_the_note_is_read_from_the_evaluation_not_from_the_report():
    """spec-doc6 C15: the row's pending state refers to the Ready Pick Profile,
    not to the delivered PRISM Report. Sourcing this cell from
    `functional_skills_reports` would make it a statement about the document."""
    assembled = dashboard.assemble_row(
        row(
            evaluation_id=EVALUATION,
            ready_pick_score=78,
            ready_pick_note="Owns a comparable production migration end to end.",
        )
    )
    assert not assembled.note_is_pending
    assert assembled.note.startswith("Owns a comparable")


def test_a_blank_note_is_pending_rather_than_an_empty_cell():
    """An empty string from a degraded run is not a note."""
    assembled = dashboard.assemble_row(
        row(evaluation_id=EVALUATION, ready_pick_score=78, ready_pick_note="   ")
    )
    assert assembled.note_is_pending


# ── Column 6: the Ready Pick Profile ─────────────────────────────────────────


def test_the_profile_button_is_disabled_with_a_reason_before_a_profile_exists():
    assembled = dashboard.assemble_row(row())
    assert assembled.profile is None
    assert assembled.profile_pending_reason
    # And it says which artefact it is talking about (C15).
    assert "PRISM Report" in assembled.profile_pending_reason


def test_the_profile_points_at_an_evaluation_and_the_report_type_carries_no_score():
    """spec-doc6 C10, enforced by the type rather than by convention.

    `PrismReportRef` has no score field. Asserted on the FIELD SET rather than
    by name, so a future field called `value` would not slip through.
    """
    profile = dashboard.ReadyPickProfileRef(evaluation_id=EVALUATION, score=81)
    report = dashboard.PrismReportRef(report_id=uuid.uuid4())

    assert profile.artifact == "ready_pick_profile"
    assert report.artifact == "prism_report"
    assert set(report.__dataclass_fields__) == {"report_id"}
    assert "score" not in report.__dataclass_fields__


# ── Column 8: Stage ──────────────────────────────────────────────────────────


def test_the_row_renders_the_coarse_candidate_stage_never_a_job_lifecycle_state():
    """spec-doc6 C11. Two enums on two entities, never interchanged."""
    lifecycle = {state.value for state in hiring_pipeline.JobLifecycleState}
    for status in hiring_pipeline.ALL_STATUSES:
        assembled = dashboard.assemble_row(row(status=status))
        assert assembled.stage not in lifecycle


def test_hold_is_rendered_as_a_modifier_and_not_as_a_stage():
    """`hold` is an ACTION, not a stage. It has no home in the six coarse
    stages and the row says the candidate is paused, not that they moved."""
    assembled = dashboard.assemble_row(row(status=hiring_pipeline.HOLD))
    assert assembled.stage is None
    assert assembled.stage_on_hold
    assert "hold" in assembled.stage_label.lower()


def test_every_stored_status_reaches_a_stage_or_is_a_named_modifier():
    """A status added to the FSM with no dashboard home renders blank, which is
    a cell nobody notices is wrong."""
    for status in hiring_pipeline.ALL_STATUSES:
        assembled = dashboard.assemble_row(row(status=status))
        assert assembled.stage is not None or assembled.stage_on_hold


# ── Row states ───────────────────────────────────────────────────────────────


def test_an_archived_row_is_marked_rather_than_removed():
    """The specification: archived / rejected is not a separate state, it is a
    closed stage rendered at reduced opacity."""
    assembled = dashboard.assemble_row(
        row(archived_at="2026-08-01T00:00:00Z", status=hiring_pipeline.REJECTED)
    )
    assert assembled.archived
    assert assembled.stage == hiring_pipeline.CandidatePipelineStage.CLOSED.value


# ── Column 2: Source ─────────────────────────────────────────────────────────


def test_all_three_source_values_render(monkeypatch):
    """spec-doc6 C40. The document lists two and this repository has three; a
    two-value filter silently hides every `sourced` candidate."""
    assert dashboard.SOURCE_TYPES == ("applied", "sourced", "databank")
    for value in dashboard.SOURCE_TYPES:
        assembled = dashboard.assemble_row(row(source_type=value))
        assert assembled.source_label == dashboard.SOURCE_LABELS[value]


# ── The Ready Pick Profile panel ─────────────────────────────────────────────


def test_the_panel_shows_named_ratings_and_no_raw_dimension_number():
    """spec-doc6 D8 / C2. The panel shows per-dimension NAMED ratings; raw
    D1-D5 numbers live only in the audited calibration view."""
    panel = dashboard.profile_panel(
        evaluation={
            "id": EVALUATION,
            "dimension_scores": {
                "verified_competence": {"band": "strong", "evidence_refs": ["e1"]},
                "authenticity_consistency": {"band": "partial", "evidence_refs": []},
            },
            "aggregate_json": {
                "overall_grade": rating.GRADE_MATCHING,
                "category_grades": {"must_have": rating.GRADE_MATCHING},
                # Present in the source and deliberately NOT projected.
                "raw_composite": 78.4,
                "adjusted_composite": 74.1,
                "category_scores": {"must_have": 78.0},
            },
            "gate_results_json": [],
            "triangulation_json": {},
            "confidence": "medium",
        },
        candidate_name=FIXTURE_NAME,
        system_id="AAAA-BBBB-CCCC",
        under_integrity_review=False,
    )
    flat = repr(panel)
    for leaked in ("raw_composite", "adjusted_composite", "category_scores", "78.4"):
        assert leaked not in flat, f"{leaked} reached the Ready Pick Profile panel"
    ratings = {d["dimension"]: d["rating"] for d in panel["dimensions"]}
    assert ratings["verified_competence"] == "strong"
    # A dimension the evaluators never reached is UNRATED, not `absent`.
    assert ratings["track_record_impact"] is None
    assert any(d["rated"] is False for d in panel["dimensions"])


def test_the_panel_reports_an_open_integrity_flag_without_deciding_anything():
    """G3 fails loudly and blocks nothing about the person. The panel names the
    finding; it carries no reject field, no status and no decision."""
    panel = dashboard.profile_panel(
        evaluation={
            "id": EVALUATION,
            "dimension_scores": {},
            "aggregate_json": {},
            "gate_results_json": [
                {
                    "gate": hiring_gates.G3,
                    "passed": False,
                    "blocking": False,
                    "reasons": ["The account's internal consistency graded partial."],
                }
            ],
            "triangulation_json": {},
        },
        candidate_name=FIXTURE_NAME,
        system_id="AAAA-BBBB-CCCC",
        under_integrity_review=True,
    )
    assert panel["open_flags"][0]["gate"] == hiring_gates.G3
    assert panel["under_integrity_review"] is True
    for forbidden in ("reject", "decision", "status"):
        assert forbidden not in panel


# ── spec-doc6 C14: no real names ─────────────────────────────────────────────


def test_the_specification_sample_name_appears_nowhere_in_the_backend():
    """The Dashboard Specification signs off with a real person's name and uses
    it as sample data. It must not survive into code, fixtures or seed data.

    Searched over the whole backend package rather than over this file, because
    the risk is somebody copying the document's example into a fixture.
    """
    root = pathlib.Path(__file__).resolve().parents[1]
    # The name as the document writes it, built from parts so this file does
    # not itself contain the string it is banning.
    banned = "Manju" + " H"
    offenders = []
    for path in list(root.glob("app/**/*.py")) + list(root.glob("tests/**/*.py")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if banned in text:
            offenders.append(str(path.relative_to(root)))
    assert not offenders, f"the specification's sample personal name is in {offenders}"
