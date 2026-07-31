"""The 10-stage hiring pipeline's transition rules (spec §3.3).

Each stage carries a promise — `assessment_completed` means a report exists,
`shortlisted` means a person read one. These tests pin the transitions that
keep those promises true, because a wrong edge produces a candidate holding an
offer with no assessment behind it AND an email referencing one they never took.
"""
from __future__ import annotations

import pytest

from app.services import hiring_pipeline as hp


def test_every_stage_in_the_display_order_is_a_known_status() -> None:
    assert set(hp.PIPELINE_ORDER) <= hp.ALL_STATUSES
    assert len(hp.PIPELINE_ORDER) == len(set(hp.PIPELINE_ORDER))


def test_the_spec_lists_ten_pipeline_stages() -> None:
    """The spec's ten, plus `hold` which it describes separately."""
    spec_ten = {
        hp.APPLIED, hp.ASSESSMENT_INVITED, hp.ASSESSMENT_IN_PROGRESS,
        hp.ASSESSMENT_COMPLETED, hp.SHORTLISTED, hp.REJECTED,
        hp.INTERVIEW_SCHEDULED, hp.INTERVIEW_COMPLETED, hp.OFFER_EXTENDED,
        hp.JOINED,
    }
    assert len(spec_ten) == 10
    assert spec_ten <= set(hp.PIPELINE_ORDER)


# ── The happy path ───────────────────────────────────────────────────────────

def test_the_full_forward_path_is_walkable() -> None:
    path = [
        hp.APPLIED, hp.ASSESSMENT_INVITED, hp.ASSESSMENT_IN_PROGRESS,
        hp.ASSESSMENT_COMPLETED, hp.SHORTLISTED, hp.INTERVIEW_SCHEDULED,
        hp.INTERVIEW_COMPLETED, hp.OFFER_EXTENDED, hp.JOINED,
    ]
    for current, target in zip(path, path[1:]):
        assert hp.can_transition(current, target), f"{current} -> {target}"
        assert hp.assert_transition(current, target) == target


# ── The promises each stage carries ──────────────────────────────────────────

def test_an_application_cannot_skip_to_an_offer() -> None:
    """Otherwise a candidate holds an offer with no assessment behind it, and
    the transition email references one they never took."""
    assert hp.can_transition(hp.APPLIED, hp.OFFER_EXTENDED) is False
    assert hp.can_transition(hp.ASSESSMENT_INVITED, hp.OFFER_EXTENDED) is False


def test_an_application_cannot_skip_to_joined() -> None:
    assert hp.can_transition(hp.APPLIED, hp.JOINED) is False
    assert hp.can_transition(hp.SHORTLISTED, hp.JOINED) is False


def test_shortlisting_straight_from_applied_is_allowed() -> None:
    """Deliberate: the permission matrix keeps shortlist available on an
    unassessed application, and a recruiter who knows a referral should not be
    forced through an assessment to advance them."""
    assert hp.can_transition(hp.APPLIED, hp.SHORTLISTED) is True


def test_assessment_stages_cannot_run_backwards() -> None:
    assert hp.can_transition(hp.ASSESSMENT_COMPLETED, hp.ASSESSMENT_INVITED) is False
    assert hp.can_transition(hp.ASSESSMENT_IN_PROGRESS, hp.ASSESSMENT_INVITED) is False


def test_a_further_round_is_reachable_after_an_interview_completes() -> None:
    """Multiple rounds are the norm, so a completed interview can lead to
    another scheduled one rather than only forward to an offer."""
    assert hp.can_transition(hp.INTERVIEW_COMPLETED, hp.INTERVIEW_SCHEDULED) is True


def test_booking_a_second_round_is_a_status_no_op_not_a_self_loop() -> None:
    """Booking round two while round one is still scheduled does not change
    the stage — the application is already `interview_scheduled`. The endpoint
    guards its transition with `can_transition` and skips the no-op, so the
    interview row is still created without writing a duplicate history entry
    for a stage the application never re-entered."""
    assert hp.can_transition(hp.INTERVIEW_SCHEDULED, hp.INTERVIEW_SCHEDULED) is False


# ── Stopping and pausing ─────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "stage",
    [
        hp.APPLIED, hp.ASSESSMENT_INVITED, hp.ASSESSMENT_IN_PROGRESS,
        hp.ASSESSMENT_COMPLETED, hp.SHORTLISTED, hp.INTERVIEW_SCHEDULED,
        hp.INTERVIEW_COMPLETED, hp.OFFER_EXTENDED,
    ],
)
def test_reject_and_hold_are_reachable_from_every_live_stage(stage: str) -> None:
    """A recruiter can always stop or pause, for reasons the system does not
    model."""
    assert hp.can_transition(stage, hp.REJECTED) is True
    assert hp.can_transition(stage, hp.HOLD) is True


@pytest.mark.parametrize("terminal", [hp.REJECTED, hp.JOINED])
def test_terminal_stages_have_no_way_out(terminal: str) -> None:
    assert hp.allowed_transitions(terminal) == frozenset()
    for target in (hp.SHORTLISTED, hp.APPLIED, hp.OFFER_EXTENDED, hp.HOLD):
        assert hp.can_transition(terminal, target) is False


def test_reopening_a_terminal_application_explains_itself() -> None:
    with pytest.raises(hp.InvalidTransition, match="cannot be reopened"):
        hp.assert_transition(hp.JOINED, hp.SHORTLISTED)


# ── Validation messages ──────────────────────────────────────────────────────

def test_an_unknown_status_is_rejected_by_name() -> None:
    with pytest.raises(hp.InvalidTransition, match="unknown status"):
        hp.assert_transition(hp.APPLIED, "promoted_to_ceo")


def test_a_no_op_transition_is_rejected() -> None:
    with pytest.raises(hp.InvalidTransition, match="already"):
        hp.assert_transition(hp.APPLIED, hp.APPLIED)


def test_an_illegal_move_names_what_is_available() -> None:
    """The recruiter should be able to act on the message without reading the
    spec."""
    with pytest.raises(hp.InvalidTransition) as excinfo:
        hp.assert_transition(hp.APPLIED, hp.OFFER_EXTENDED)
    message = str(excinfo.value)
    assert "available" in message
    assert hp.ASSESSMENT_INVITED in message


# ── The legacy synonym ───────────────────────────────────────────────────────

def test_legacy_offered_normalises_to_offer_extended() -> None:
    """Historic rows predate the rename; they must stay readable rather than
    being rewritten."""
    assert hp.normalize(hp.OFFERED) == hp.OFFER_EXTENDED
    assert hp.can_transition(hp.OFFERED, hp.JOINED) is True
    assert hp.assert_transition(hp.OFFERED, hp.JOINED) == hp.JOINED


def test_a_missing_status_reads_as_applied() -> None:
    assert hp.normalize(None) == hp.APPLIED
    assert hp.normalize("") == hp.APPLIED
    assert hp.can_transition(None, hp.ASSESSMENT_INVITED) is True


# ── Labels and emails ────────────────────────────────────────────────────────

def test_every_stage_has_a_human_label_without_underscores() -> None:
    for stage in hp.PIPELINE_ORDER:
        label = hp.STAGE_LABELS[stage]
        assert label and "_" not in label


def test_the_candidate_is_not_emailed_about_their_own_click() -> None:
    """`assessment_in_progress` fires when the candidate opens the assessment.
    Mailing them about something they just did is noise (spec §4.1)."""
    assert hp.TRANSITION_EMAIL[hp.ASSESSMENT_IN_PROGRESS] is None


def test_decision_stages_all_send_an_email() -> None:
    for stage in (hp.SHORTLISTED, hp.REJECTED, hp.HOLD):
        assert hp.TRANSITION_EMAIL[stage] is not None


def test_every_stage_has_an_explicit_email_decision() -> None:
    """Listed as None rather than omitted, so a missing template is visible
    rather than looking like an oversight."""
    for stage in hp.PIPELINE_ORDER:
        assert stage in hp.TRANSITION_EMAIL


def test_every_configured_email_type_exists() -> None:
    from app.models.email_log import EMAIL_TYPES

    for stage, email_type in hp.TRANSITION_EMAIL.items():
        if email_type is not None:
            assert email_type in EMAIL_TYPES, stage


# ── The set returned to the UI ───────────────────────────────────────────────

def test_allowed_transitions_never_includes_the_current_stage() -> None:
    for stage in hp.PIPELINE_ORDER:
        assert stage not in hp.allowed_transitions(stage)


def test_allowed_transitions_only_returns_known_statuses() -> None:
    """The UI renders exactly this set as action buttons, so an unknown value
    here becomes a button that 409s."""
    for stage in hp.PIPELINE_ORDER:
        assert hp.allowed_transitions(stage) <= hp.ALL_STATUSES
