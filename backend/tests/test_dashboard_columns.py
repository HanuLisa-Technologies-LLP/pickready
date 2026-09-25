"""The eight columns, their vocabularies, and every documented pending state.

Pure. No database, no HTTP, no model. `dashboard.assemble_row` takes a plain
mapping precisely so every state of the two grade columns (the four words,
not checked, not assessed with each reason, under review) and the row states
can each be asserted directly rather than reached through a seeded fixture.

WHAT THIS FILE IS DEFENDING
---------------------------
Four of the eight columns are filled by agents that were not on a live path
when this surface was built. The failure that invites is not a crash: it is a
plausible-looking default. A dashboard that renders "Moderately Matching" for
a candidate nobody has read is worse than one that renders nothing, because a
recruiter acts on it. Every test below that names a
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
from app.services.miti import aggregation as miti_aggregation
from app.services.yukti import config as yukti_config
from app.services.yukti import ranking as yukti_ranking

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
    ingested, nothing read by AI Matching, nothing assessed. That is the common
    case, so it is what a test has to opt OUT of. The two scores in the mapping
    are what `candidates_page` selects; they must come out as words.
    """
    base = {
        "link_id": LINK,
        "tenant_id": TENANT,
        "job_id": JOB,
        "job_title": "Staff Platform Engineer",
        "candidate_id": CANDIDATE,
        "full_name": FIXTURE_NAME,
        "source_type": "applied",
        "ai_match_status": yukti_config.STATUS_PENDING,
        "ai_match_score": None,
        "ai_match_failure_reason": None,
        "ready_pick_rank": None,
        "has_assessment_grade": False,
        "must_have_failed": False,
        "status": hiring_pipeline.APPLIED,
        "created_at": None,
        "archived_at": None,
        "evaluation_id": None,
        "confidence": None,
        "evaluated_at": None,
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
    protected is the ORDER: Vivekium Grade sitting after Vivekium Note
    would still be eight columns and would break the triage read.
    """
    assert dashboard.COLUMNS == (
        "candidate",
        "source",
        "ai_match",
        "ready_pick_grade",
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


# ── Column 3: AI Match, the resume-only reading ───────────────────────────


def _scored(score, status=yukti_config.STATUS_SCORED):
    return {"ai_match_status": status, "ai_match_score": score}


def test_the_grade_vocabulary_is_the_one_scale():
    """One vocabulary, defined in `rating`, imported here.

    `services/tiers.py` is the reason this is a test: a rendering layer that
    keeps its own copy of a four-value scale is how the product ended up with
    two scales that disagreed for 69.5% of its rows.
    """
    assert dashboard.AI_MATCH_GRADES is rating.GRADES
    assert list(dashboard.GRADE_STATES) == list(rating.GRADES)
    assert len(set(dashboard.GRADE_STATES.values())) == len(rating.GRADES)


@pytest.mark.parametrize(
    "score,expected",
    [
        (100, rating.GRADE_HIGHLY),
        (90, rating.GRADE_HIGHLY),
        (89.9, rating.GRADE_MATCHING),
        (75, rating.GRADE_MATCHING),
        (74.9, rating.GRADE_MODERATELY),
        (60, rating.GRADE_MODERATELY),
        (59.9, rating.GRADE_NOT),
        (0, rating.GRADE_NOT),
    ],
)
def test_a_read_resume_is_one_of_the_four_words(score, expected):
    """Boundaries are the scale's own, inclusive upward (claude.md rule 8),
    asserted from BOTH sides of each cut-point."""
    assembled = dashboard.assemble_row(row(**_scored(score)))
    assert assembled.ai_match_label == expected
    assert assembled.ai_match_state == dashboard.GRADE_STATES[expected]
    assert "resume" in assembled.ai_match_screen_reader_label.lower()


def test_an_unread_resume_is_not_checked_and_never_a_grade():
    """"AI Matching has not read this" and "it read this and it is weak" are
    different sentences. Collapsing them slanders every candidate still in
    the queue."""
    assembled = dashboard.assemble_row(row())
    assert assembled.ai_match_state == dashboard.STATE_NOT_CHECKED
    assert assembled.ai_match_label == dashboard.NOT_CHECKED_LABEL
    assert assembled.ai_match_label not in rating.GRADES


@pytest.mark.parametrize("reason", yukti_config.FAILURE_REASONS)
def test_every_failure_reason_has_a_sentence_and_no_grade(reason):
    """A not-assessed resume says WHY in words, and carries no grade."""
    assembled = dashboard.assemble_row(
        row(
            ai_match_status=yukti_config.STATUS_NOT_ASSESSED,
            ai_match_failure_reason=reason,
        )
    )
    assert assembled.ai_match_state == dashboard.STATE_NOT_ASSESSED
    assert assembled.ai_match_label == dashboard.NOT_ASSESSED_LABEL
    assert assembled.ai_match_note == dashboard.NOT_ASSESSED_NOTES[reason]


def test_the_sentences_cover_exactly_the_reasons_yukti_stores():
    """A reason added to Yukti with no sentence here would reach a row and
    raise; a sentence for a reason Yukti no longer writes is dead copy."""
    assert set(dashboard.NOT_ASSESSED_NOTES) == set(yukti_config.FAILURE_REASONS)


def test_a_legacy_reading_says_it_predates_the_evidence_tags():
    assembled = dashboard.assemble_row(
        row(**_scored(81, status=yukti_config.STATUS_LEGACY))
    )
    assert assembled.ai_match_label == rating.GRADE_MATCHING
    assert assembled.ai_match_note == dashboard.LEGACY_NOTE


@pytest.mark.parametrize(
    "overrides",
    [
        {"ai_match_status": "graded"},
        {"ai_match_status": None},
        {
            "ai_match_status": yukti_config.STATUS_NOT_ASSESSED,
            "ai_match_failure_reason": "because",
        },
        {"ai_match_status": yukti_config.STATUS_SCORED, "ai_match_score": None},
    ],
)
def test_a_diverged_vocabulary_raises_rather_than_rendering(overrides):
    """The CHECK on `yukti_status` and Yukti's own writer refuse all of these,
    so arriving here means the vocabularies have diverged. A dashboard that
    quietly renders one is how that goes unnoticed for a release."""
    with pytest.raises(ValueError):
        dashboard.assemble_row(row(**overrides))


# ── Column 4: the Vivekium Grade ─────────────────────────────────────────────


def test_column_4_is_the_word_for_the_rank_the_ranked_table_sorts_by():
    """ONE rank expression (`yukti.ranking`), so the dashboard and the job page
    can never order one job's candidates two ways. The word is the scale's."""
    assembled = dashboard.assemble_row(
        row(**_scored(80), ready_pick_rank=91.0, has_assessment_grade=True)
    )
    assert assembled.ranking_label == rating.GRADE_HIGHLY
    assert assembled.ranking_state == dashboard.STATE_HIGHLY
    assert dashboard.BASIS_WITH_ASSESSMENT in assembled.ranking_note


def test_a_resume_only_grade_says_so():
    assembled = dashboard.assemble_row(row(**_scored(80), ready_pick_rank=80.0))
    assert assembled.ranking_label == rating.GRADE_MATCHING
    assert assembled.ranking_note.startswith(dashboard.BASIS_RESUME_ONLY)


def test_an_assessment_over_an_unread_resume_says_the_assessment_alone_decided():
    assembled = dashboard.assemble_row(
        row(
            ai_match_status=yukti_config.STATUS_NOT_ASSESSED,
            ai_match_failure_reason=yukti_config.FAILURE_NO_RESUME_TEXT,
            ready_pick_rank=66.0,
            has_assessment_grade=True,
        )
    )
    assert assembled.ranking_label == rating.GRADE_MODERATELY
    assert assembled.ranking_note.startswith(dashboard.BASIS_ASSESSMENT_ONLY)


def test_a_failed_must_have_is_named_beside_the_capped_grade():
    assembled = dashboard.assemble_row(
        row(
            **_scored(100),
            ready_pick_rank=float(yukti_ranking.must_have_cap()),
            has_assessment_grade=True,
            must_have_failed=True,
        )
    )
    assert assembled.ranking_label == rating.GRADE_MODERATELY
    assert dashboard.MUST_HAVE_CAPPED_NOTE in assembled.ranking_note


def test_no_rank_repeats_column_3s_reason_rather_than_inventing_a_grade():
    """With no reading and no graded assessment there is nothing to grade,
    and the cell says which of the two absences it is."""
    unread = dashboard.assemble_row(row())
    assert unread.ranking_state == dashboard.STATE_NOT_CHECKED
    assert unread.ranking_label == dashboard.NOT_CHECKED_LABEL

    failed = dashboard.assemble_row(
        row(
            ai_match_status=yukti_config.STATUS_NOT_ASSESSED,
            ai_match_failure_reason=yukti_config.FAILURE_MODEL_UNAVAILABLE,
        )
    )
    assert failed.ranking_state == dashboard.STATE_NOT_ASSESSED
    assert "retried" in failed.ranking_note


def test_under_review_is_announced_with_its_meaning():
    """The specification names this one explicitly: announced as "Status: Under
    Review, awaiting integrity disposition", not as a visual red."""
    assembled = dashboard.assemble_row(row(under_integrity_review=True))
    assert assembled.ranking_state == dashboard.STATE_UNDER_REVIEW
    assert (
        assembled.ranking_screen_reader_label
        == "Status: Under Review, awaiting integrity disposition"
    )


def test_under_review_withholds_the_grade_even_when_one_exists():
    """A grade printed beside "Under Review" invites a recruiter to act on it,
    which is the one thing the lock exists to prevent."""
    assembled = dashboard.assemble_row(
        row(
            **_scored(95),
            under_integrity_review=True,
            ready_pick_rank=95.0,
            has_assessment_grade=True,
            evaluation_id=EVALUATION,
            confidence=miti_aggregation.CONFIDENCE_HIGH,
        )
    )
    assert assembled.ranking_label == dashboard.UNDER_REVIEW_LABEL
    assert assembled.ranking_label not in rating.GRADES
    assert assembled.confidence_indicator == dashboard.CONFIDENCE_GRAYED


@pytest.mark.parametrize(
    "confidence,expected",
    [
        (miti_aggregation.CONFIDENCE_HIGH, dashboard.CONFIDENCE_FILLED),
        (miti_aggregation.CONFIDENCE_MODERATE, dashboard.CONFIDENCE_FILLED),
        (miti_aggregation.CONFIDENCE_LOW, dashboard.CONFIDENCE_OUTLINE),
        (miti_aggregation.CONFIDENCE_INSUFFICIENT, dashboard.CONFIDENCE_GRAYED),
        (None, dashboard.CONFIDENCE_GRAYED),
    ],
)
def test_the_confidence_dot_follows_the_aggregators_own_word(confidence, expected):
    """`moderate` is the word the aggregator writes (0106 rewrote `medium`
    out of the column). The dot keyed on `medium` rendered every moderate
    assessment as grayed "Insufficient confidence"."""
    assert dashboard.confidence_indicator(confidence) == expected


def test_an_unknown_confidence_word_raises():
    with pytest.raises(ValueError):
        dashboard.confidence_indicator("medium")


def test_no_evaluation_is_not_called_insufficient_confidence():
    """A resume check is not an assessment. Calling its confidence
    "insufficient" would read as a verdict on evidence nobody has gathered."""
    assembled = dashboard.assemble_row(row(**_scored(80), ready_pick_rank=80.0))
    assert assembled.confidence_label == dashboard.CONFIDENCE_NOT_ASSESSED_LABEL


def test_the_confidence_dot_is_always_accompanied_by_words():
    """Colour and shape are never the sole carrier of meaning."""
    for indicator in dashboard.CONFIDENCE_INDICATORS:
        assert dashboard.CONFIDENCE_LABELS[indicator].strip()


# ── The AI Match filter and the scale's cut-points ───────────────────────────


def test_the_filter_ranges_agree_with_the_scale_everywhere():
    """The filter's SQL ranges are read off `rating.grade_for_percent`, never
    retyped. Swept on a fine grid so a cut-point moved off a whole number, or
    a range that overlaps its neighbour, fails here rather than filtering one
    candidate into two grades."""
    for step in range(0, 10001, 5):
        value = step / 100
        inside = []
        for grade in rating.GRADES:
            floor, ceiling = dashboard.grade_range(grade)
            if floor <= value and (ceiling is None or value < ceiling):
                inside.append(grade)
        assert inside == [rating.grade_for_percent(value)], value


def test_the_filter_sql_carries_no_caller_text():
    """Only bounds travel, as bound parameters; the word never reaches SQL."""
    clause, params = dashboard._ai_match_clause([rating.GRADE_MATCHING])
    assert rating.GRADE_MATCHING not in clause
    floor, ceiling = dashboard.grade_range(rating.GRADE_MATCHING)
    assert sorted(params.values()) == [floor, ceiling]


# ── Nothing numeric leaves the row ───────────────────────────────────────────


def test_the_assembled_row_carries_no_number_but_counts():
    """The scores are consumed by `assemble_row`; the row it returns is words.

    Asserted on the VALUES of a fully populated row rather than on field
    names, because a field called `hint` holding 87.0 would pass a name check.
    """
    assembled = dashboard.assemble_row(
        row(
            **_scored(87.4),
            ready_pick_rank=88.2,
            has_assessment_grade=True,
            evaluation_id=EVALUATION,
            confidence=miti_aggregation.CONFIDENCE_HIGH,
            team_review_count=2,
        )
    )
    for name, value in vars(assembled).items():
        if name == "team_review_count":
            continue
        if isinstance(value, bool) or value is None:
            continue
        assert not isinstance(value, (int, float)), name
        assert "87" not in str(value) and "88" not in str(value), name


# ── Column 5: the Vivekium Note ────────────────────────────────────────────


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

    A report payload with a score field on it refuses to construct, and the
    dashboard reaches a client through its own schema rather than by
    borrowing the report's.
    """
    for module in ("app/schemas/dashboard.py", "app/services/dashboard.py",
                   "app/api/dashboard.py"):
        source = (
            pathlib.Path(__file__).resolve().parents[1] / module
        ).read_text(encoding="utf-8")
        assert "schemas.reports" not in source
        assert "schemas import reports" not in source


def test_the_note_is_read_from_the_evaluation_not_from_the_report():
    """spec-doc6 C15: the row's pending state refers to the Vivekium Profile,
    not to the delivered PRISM Report. Sourcing this cell from
    `functional_skills_reports` would make it a statement about the document."""
    assembled = dashboard.assemble_row(
        row(
            evaluation_id=EVALUATION,
            ready_pick_note="Owns a comparable production migration end to end.",
        )
    )
    assert not assembled.note_is_pending
    assert assembled.note.startswith("Owns a comparable")


def test_a_blank_note_is_pending_rather_than_an_empty_cell():
    """An empty string from a degraded run is not a note."""
    assembled = dashboard.assemble_row(
        row(evaluation_id=EVALUATION, ready_pick_note="   ")
    )
    assert assembled.note_is_pending


# ── Column 6: the Vivekium Profile ─────────────────────────────────────────


def test_the_profile_button_is_disabled_with_a_reason_before_a_profile_exists():
    assembled = dashboard.assemble_row(row())
    assert assembled.profile is None
    assert assembled.profile_pending_reason
    # And it says which artefact it is talking about (C15).
    assert "PRISM Report" in assembled.profile_pending_reason


def test_the_profile_points_at_an_evaluation_and_the_report_type_carries_no_score():
    """spec-doc6 C10, enforced by the type rather than by convention.

    Neither reference carries a score any more: D3 took the profile's licence
    for one. Asserted on the FIELD SET rather than by name, so a future field
    called `value` would not slip through.
    """
    profile = dashboard.ReadyPickProfileRef(evaluation_id=EVALUATION)
    report = dashboard.PrismReportRef(report_id=uuid.uuid4())

    assert profile.artifact == "ready_pick_profile"
    assert report.artifact == "prism_report"
    assert set(report.__dataclass_fields__) == {"report_id"}
    assert set(profile.__dataclass_fields__) == {"evaluation_id"}


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
    a cell nobody notices is wrong.

    The property is that the CELL IS NEVER BLANK, and the assertion says that
    directly. It used to say `stage is not None or stage_on_hold`, which was a
    proxy that held only while `hold` was the single status outside the six
    coarse stages. `sourced` is the second (workflow Gate 5) and it is
    deliberately neither a stage nor a pause: it is before the funnel. Under
    the old proxy it would have failed while rendering a perfectly good label.
    """
    for status in hiring_pipeline.ALL_STATUSES:
        assembled = dashboard.assemble_row(row(status=status))
        assert assembled.stage is not None or assembled.stage_label.strip(), status


def test_a_sourced_candidate_is_outside_the_funnel_and_not_paused():
    """Gate 5, at the one surface that counts applicants.

    Mapping `sourced` to Applied would have the Dashboard's funnel report a
    recruiter's own filing cabinet as inbound applications, which is exactly
    the confusion the stage exists to end. Marking it `stage_on_hold` would be
    just as wrong in the other direction: nobody paused this candidate, nobody
    has contacted them at all.
    """
    assembled = dashboard.assemble_row(row(status=hiring_pipeline.SOURCED))
    assert assembled.stage is None
    assert assembled.stage_on_hold is False
    assert assembled.stage_label == hiring_pipeline.STAGE_LABELS[
        hiring_pipeline.SOURCED
    ]
    assert "hold" not in assembled.stage_label.lower()


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


# ── The Vivekium Profile panel ─────────────────────────────────────────────


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
        assert leaked not in flat, f"{leaked} reached the Vivekium Profile panel"
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
