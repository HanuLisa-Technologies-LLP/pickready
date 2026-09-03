"""The rules, without a database (spec sections 3.3, 3.6, 4.1, 4.2, 4.5, 9).

Four groups, each one of section 11's required unit tests:

  * server-side classification, which is the only thing standing between a
    browser's claim and a termination;
  * the warning state machine, every path from zero to three and both policy
    branches;
  * the candidate's own typing baseline;
  * face descriptor distance.

All four are pure functions on purpose. The pipeline that calls them needs a
database and a Redis and is tested in `test_proctoring_pipeline.py`; the rules
themselves are arithmetic, and arithmetic tested through an integration
harness is arithmetic nobody can read the failure of.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models.proctoring import (
    FACE_DESCRIPTOR_WIDTH,
    OUTCOME_TECHNICAL_FAILURE,
    OUTCOME_TERMINATED_INTEGRITY,
    POLICY_CONTINUE_AND_NOTE,
    POLICY_TERMINATE,
    QUALITY_DEGRADED,
    QUALITY_GOOD,
    QUALITY_POOR,
)
from app.schemas.assessments import AnswerBehaviourIn
from app.schemas.proctoring import EventIn
from app.services.proctoring import behaviour, catalog, identity, ingestion
from app.services.proctoring.config import get_config

CONFIG = get_config()
NOW = datetime(2026, 9, 2, 10, 30, tzinfo=timezone.utc)
MS = 1000


def _event(event_type: str, duration_ms: int | None = None, **metadata) -> EventIn:
    return EventIn(
        event_type=event_type,
        occurred_at=NOW,
        duration_ms=duration_ms,
        metadata=metadata,
    )


# ══════════════════════════════════════════════════════════════════════════
# 1. SERVER-SIDE CLASSIFICATION
# ══════════════════════════════════════════════════════════════════════════


def test_an_event_the_browser_timed_correctly_keeps_its_path() -> None:
    """The healthy path first: a check that downgraded everything would pass
    every test below and terminate nobody."""
    result = ingestion.classify(
        _event("FACE_ABSENT_EXTENDED", CONFIG.face_absent_extended_seconds * MS), CONFIG
    )
    assert result.event_type == "FACE_ABSENT_EXTENDED"
    assert result.path == catalog.PATH_A


def test_a_focus_loss_under_the_ignore_window_is_logged_not_warned() -> None:
    """Section 4.2: "Ignore losses under 2 seconds (accidental)". A candidate
    who clicked their own taskbar has not left the assessment."""
    result = ingestion.classify(
        _event("WINDOW_FOCUS_LOST", int(CONFIG.focus_loss_ignore_under_seconds * MS) - 1),
        CONFIG,
    )
    assert result.path == catalog.PATH_C
    assert result.note[ingestion.NOTE_KEY] == ingestion.NOTE_UNDER_IGNORE_WINDOW


def test_a_focus_loss_at_the_ignore_boundary_still_warns() -> None:
    """Boundaries are inclusive upward (claude.md rule 8): exactly two seconds
    is a real departure, not an accident."""
    result = ingestion.classify(
        _event("WINDOW_FOCUS_LOST", int(CONFIG.focus_loss_ignore_under_seconds * MS)), CONFIG
    )
    assert result.path == catalog.PATH_B


def test_a_recovered_integrity_failure_is_a_note_rather_than_a_termination() -> None:
    """Section 9: failure is an event, and only after sixty seconds is it
    Path A. Terminating on the first missed self-check would end an
    assessment over a garbage-collection pause."""
    result = ingestion.classify(
        _event("INTEGRITY_CHECK_FAILED", CONFIG.integrity_failure_termination_seconds * MS - 1),
        CONFIG,
    )
    assert result.event_type == "INTEGRITY_CHECK_WARNING"
    assert result.path == catalog.PATH_C
    assert result.note[ingestion.NOTE_DOWNGRADED_FROM] == "INTEGRITY_CHECK_FAILED"


def test_an_unrecovered_integrity_failure_terminates() -> None:
    result = ingestion.classify(
        _event("INTEGRITY_CHECK_FAILED", CONFIG.integrity_failure_termination_seconds * MS),
        CONFIG,
    )
    assert result.event_type == "INTEGRITY_CHECK_FAILED"
    assert result.path == catalog.PATH_A


def test_a_camera_stream_that_recovered_in_time_is_an_interruption() -> None:
    result = ingestion.classify(
        _event("CAMERA_STREAM_FAILED", CONFIG.camera_recovery_seconds * MS - 1), CONFIG
    )
    assert result.event_type == "CAMERA_STREAM_INTERRUPTED"
    assert result.path == catalog.PATH_C


def test_a_camera_stream_that_never_recovered_terminates() -> None:
    result = ingestion.classify(
        _event("CAMERA_STREAM_FAILED", CONFIG.camera_recovery_seconds * MS), CONFIG
    )
    assert result.path == catalog.PATH_A


def test_an_obstruction_shorter_than_the_rule_is_not_an_obstruction() -> None:
    """Section 4.6: obstruction escalates to Path A far faster than absence
    BECAUSE it is deliberate. A lens covered for two seconds is a hand
    passing the camera."""
    result = ingestion.classify(
        _event("CAMERA_OBSTRUCTED", CONFIG.obstruction_seconds * MS - 1), CONFIG
    )
    assert result.event_type == "FACE_ABSENT_BRIEF"
    assert result.path == catalog.PATH_C


def test_absence_is_graded_by_how_long_it_lasted() -> None:
    """The three absence bands are one rule at three durations, and the
    server holds the browser to each of them."""
    brief = ingestion.classify(
        _event("FACE_ABSENT_EXTENDED", CONFIG.face_absent_moderate_seconds * MS - 1), CONFIG
    )
    assert brief.event_type == "FACE_ABSENT_BRIEF"
    assert brief.path == catalog.PATH_C

    moderate = ingestion.classify(
        _event("FACE_ABSENT_EXTENDED", CONFIG.face_absent_moderate_seconds * MS), CONFIG
    )
    assert moderate.event_type == "FACE_ABSENT_MODERATE"
    assert moderate.path == catalog.PATH_B

    extended = ingestion.classify(
        _event("FACE_ABSENT_EXTENDED", CONFIG.face_absent_extended_seconds * MS), CONFIG
    )
    assert extended.event_type == "FACE_ABSENT_EXTENDED"
    assert extended.path == catalog.PATH_A


def test_a_moderate_absence_below_its_own_rule_becomes_brief() -> None:
    result = ingestion.classify(
        _event("FACE_ABSENT_MODERATE", CONFIG.face_absent_moderate_seconds * MS - 1), CONFIG
    )
    assert result.event_type == "FACE_ABSENT_BRIEF"


def test_an_untimed_event_is_trusted_as_the_browser_sent_it() -> None:
    """The browser is the only party that saw the camera. Refusing every
    event that omits a duration would make the rules depend on a field the
    specification does not require, and the failure would be silent."""
    for event_type in ("CAMERA_OBSTRUCTED", "FACE_ABSENT_EXTENDED", "CAMERA_STREAM_FAILED"):
        assert ingestion.classify(_event(event_type), CONFIG).path == catalog.PATH_A


def test_a_browser_cannot_escalate_by_claiming_a_longer_duration() -> None:
    """Classification only ever moves an event DOWN. A Path C identifier with
    an enormous duration is still Path C."""
    result = ingestion.classify(_event("LOW_LIGHT", 10 * 60 * 60 * MS), CONFIG)
    assert result.path == catalog.PATH_C


# ══════════════════════════════════════════════════════════════════════════
# 2. THE WARNING STATE MACHINE, BOTH POLICY BRANCHES
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("policy", [POLICY_TERMINATE, POLICY_CONTINUE_AND_NOTE])
@pytest.mark.parametrize("number", [1, 2])
def test_the_first_two_warnings_never_terminate_under_either_policy(
    policy: str, number: int
) -> None:
    """The policy is consulted at the THIRD warning and nowhere else."""
    decision = ingestion.decide_warning(number, CONFIG.max_warnings, policy)
    assert decision.number == number
    assert not decision.final
    assert not decision.terminate


def test_the_third_warning_terminates_only_under_the_terminate_policy() -> None:
    stopped = ingestion.decide_warning(3, CONFIG.max_warnings, POLICY_TERMINATE)
    noted = ingestion.decide_warning(3, CONFIG.max_warnings, POLICY_CONTINUE_AND_NOTE)
    assert stopped.final and stopped.terminate
    assert noted.final and not noted.terminate


def test_the_default_policy_is_the_one_that_does_not_terminate() -> None:
    """Section 6: "Never terminate by default without an explicit choice."
    Asserted against the column default, not against a literal here."""
    from app.models.proctoring import DEFAULT_WARNING_POLICY

    assert DEFAULT_WARNING_POLICY == POLICY_CONTINUE_AND_NOTE
    assert not ingestion.decide_warning(
        CONFIG.max_warnings, CONFIG.max_warnings, DEFAULT_WARNING_POLICY
    ).terminate


def test_the_counter_is_shared_across_every_path_b_type() -> None:
    """Section 4.0: "A candidate who switches tabs once, is seen with a phone
    once, and has a second person appear once has used all three warnings.
    This is deliberate: separate counters would allow twelve incidents before
    any consequence."

    Asserted as a property of the DECISION function: the third warning is
    final whatever produced it, because the number is the only input.
    """
    for number, event_type in enumerate(
        ("WINDOW_FOCUS_LOST", "DEVICE_DETECTED_PHONE", "SECOND_PERSON_DETECTED"), start=1
    ):
        decision = ingestion.decide_warning(number, CONFIG.max_warnings, POLICY_TERMINATE)
        assert decision.terminate == (number == CONFIG.max_warnings), event_type


def test_a_warning_past_the_limit_is_still_final_rather_than_wrapping() -> None:
    """A fourth warning cannot exist (the row's CHECK forbids it), and the
    decision must not read as a fresh first warning if one is ever asked for."""
    decision = ingestion.decide_warning(CONFIG.max_warnings + 1, CONFIG.max_warnings, POLICY_TERMINATE)
    assert decision.final and decision.terminate


# ── Which outcome a termination produces ─────────────────────────────────────


def test_a_dead_camera_is_a_technical_failure_and_never_an_integrity_one() -> None:
    """Section 7.3: "A candidate whose laptop camera died must never be
    presented as suspicious." Independent of the warning count: hardware does
    not become misconduct because something else happened earlier."""
    for warnings in (0, 1, 3):
        assert ingestion.outcome_for_termination("CAMERA_STREAM_FAILED", warnings) == (
            OUTCOME_TECHNICAL_FAILURE
        )


def test_an_integrity_failure_on_a_clean_session_reads_as_technical() -> None:
    """Nothing else in the session suggested the candidate was doing anything,
    and the honest reading of an unexplained failure on a clean session is
    that something broke."""
    assert ingestion.outcome_for_termination("INTEGRITY_CHECK_FAILED", 0) == (
        OUTCOME_TECHNICAL_FAILURE
    )


def test_an_integrity_failure_after_warnings_is_not_written_off_as_technical() -> None:
    assert ingestion.outcome_for_termination("INTEGRITY_CHECK_FAILED", 1) == (
        OUTCOME_TERMINATED_INTEGRITY
    )


@pytest.mark.parametrize(
    "reason", ["IDENTITY_MISMATCH", "CAMERA_OBSTRUCTED", "FACE_ABSENT_EXTENDED",
               "CAMERA_PERMISSION_LOST", "MIC_PERMISSION_LOST"]
)
def test_a_candidate_side_termination_is_an_integrity_outcome(reason: str) -> None:
    for warnings in (0, 2):
        assert ingestion.outcome_for_termination(reason, warnings) == OUTCOME_TERMINATED_INTEGRITY


def test_only_a_path_a_reason_can_terminate_a_session() -> None:
    """`terminate` is the one function that ends a session on an event, and a
    Path B identifier reaching it would be a bug that ended assessments."""
    import asyncio

    with pytest.raises(ValueError):
        asyncio.run(ingestion.terminate(None, None, "WINDOW_FOCUS_LOST", NOW))


# ── Session quality (section 3.6) ────────────────────────────────────────────


def test_a_slow_device_is_recorded_rather_than_refused() -> None:
    """"Do not block the candidate from taking the assessment because their
    laptop is slow." Every band is a recorded quality, never a refusal."""
    assert ingestion.session_quality_for(CONFIG.sampling_fps_normal, CONFIG) == QUALITY_GOOD
    assert ingestion.session_quality_for(CONFIG.sampling_fps_degraded, CONFIG) == QUALITY_DEGRADED
    assert ingestion.session_quality_for(CONFIG.sampling_fps_degraded - 0.5, CONFIG) == QUALITY_POOR
    assert ingestion.session_quality_for(None, CONFIG) == QUALITY_GOOD


# ══════════════════════════════════════════════════════════════════════════
# 3. THE CANDIDATE'S OWN TYPING BASELINE (section 4.5)
# ══════════════════════════════════════════════════════════════════════════


def _typed(count: int, *, start: int = 0, gap_ms: int = 200, backspaces: int = 0) -> AnswerBehaviourIn:
    """An answer typed at a steady `gap_ms` per key."""
    offsets = [start + i * gap_ms for i in range(count)]
    return AnswerBehaviourIn(
        keydown_offsets_ms=offsets,
        backspace_offsets_ms=offsets[:backspaces],
        focus_ms=(offsets[-1] - offsets[0]) if count > 1 else 0,
    )


def _baseline_profile(gap_ms: int = 200) -> dict:
    """A profile built from the configured number of ordinary answers."""
    profile: dict = {}
    for _ in range(CONFIG.baseline_answers):
        summary = behaviour.summarise(_typed(200, gap_ms=gap_ms), 200, CONFIG)
        profile = behaviour.updated_profile(profile, summary, CONFIG)
    return profile


def test_the_baseline_needs_the_configured_number_of_answers() -> None:
    profile: dict = {}
    for index in range(CONFIG.baseline_answers):
        assert not behaviour.baseline_established(profile, CONFIG), index
        summary = behaviour.summarise(_typed(100), 100, CONFIG)
        profile = behaviour.updated_profile(profile, summary, CONFIG)
    assert behaviour.baseline_established(profile, CONFIG)


#: The two rules that compare a candidate against their OWN baseline. The
#: other two describe the answer alone and fire without one, deliberately.
RELATIVE_RULES = ("FAST_TEXT_ENTRY", "MOUSE_BEHAVIOR_DEVIATION")


def test_no_relative_rule_fires_before_the_baseline_exists() -> None:
    """The first answers of every assessment have no baseline, and that is the
    ordinary state rather than an error. A relative rule that fired against an
    empty profile would judge every candidate on their first answer, when
    there is by construction nothing to compare them with."""
    blazing = behaviour.summarise(_typed(1200, gap_ms=10), 1200, CONFIG)
    for profile in (None, {}, {"answers": 0, "rates": []}):
        found = dict(behaviour.evaluate(blazing, profile, CONFIG))
        for rule in RELATIVE_RULES:
            assert rule not in found, (profile, rule)


def test_a_fast_typist_is_not_judged_for_being_fast() -> None:
    """Section 4.5: "A naturally fast typist should not be flagged for being
    fast; they should only be noted if they are dramatically faster than
    themselves." The baseline here is fast and the answer matches it."""
    fast_profile = _baseline_profile(gap_ms=40)
    same_speed = behaviour.summarise(_typed(400, gap_ms=40), 400, CONFIG)
    found = dict(behaviour.evaluate(same_speed, fast_profile, CONFIG))
    assert "FAST_TEXT_ENTRY" not in found


def test_a_candidate_dramatically_faster_than_themselves_is_noted() -> None:
    """The span has to outlast `fast_entry_sustained_seconds`, so the fixture
    types for longer than that window at a rate far above its own baseline."""
    slow_profile = _baseline_profile(gap_ms=400)
    keys = (CONFIG.fast_entry_sustained_seconds * MS // 10) + 200
    burst = behaviour.summarise(_typed(keys, gap_ms=10), keys, CONFIG)
    found = dict(behaviour.evaluate(burst, slow_profile, CONFIG))
    assert "FAST_TEXT_ENTRY" in found
    assert found["FAST_TEXT_ENTRY"]["rate_ratio"] > CONFIG.fast_entry_multiplier


def test_the_fast_rule_needs_the_rate_to_be_sustained() -> None:
    """"Sustained entry rate ... for 10+ seconds". A one-second flurry is
    somebody holding a key down, not an answer arriving from elsewhere."""
    slow_profile = _baseline_profile(gap_ms=400)
    flurry = behaviour.summarise(_typed(30, gap_ms=10), 30, CONFIG)
    assert "FAST_TEXT_ENTRY" not in dict(behaviour.evaluate(flurry, slow_profile, CONFIG))


def test_a_long_uninterrupted_span_with_no_corrections_is_noted() -> None:
    """An absolute rule: it describes the answer alone and needs no baseline,
    which is why it fires here with an empty profile."""
    uniform = behaviour.summarise(
        _typed(CONFIG.uniform_span_chars + 50, gap_ms=100), CONFIG.uniform_span_chars + 50, CONFIG
    )
    assert "UNIFORM_TEXT_ENTRY" in dict(behaviour.evaluate(uniform, None, CONFIG))


def test_ordinary_typing_with_pauses_and_corrections_is_not_noted() -> None:
    """The false-positive direction, which is the one that costs a candidate.
    Real writing pauses to think and deletes things."""
    offsets: list[int] = []
    moment = 0
    for block in range(6):
        for _ in range(40):
            moment += 150
            offsets.append(moment)
        moment += int(CONFIG.uniform_max_pause_seconds * MS) + 500
    answer = AnswerBehaviourIn(
        keydown_offsets_ms=offsets,
        backspace_offsets_ms=offsets[::20][: CONFIG.uniform_max_corrections + 2],
        focus_ms=moment,
    )
    summary = behaviour.summarise(answer, len(offsets), CONFIG)
    assert "UNIFORM_TEXT_ENTRY" not in dict(behaviour.evaluate(summary, None, CONFIG))


def test_more_text_than_keystrokes_is_noted_as_a_ratio() -> None:
    """Section 4.5's third rule. The answer is longer than the typing that
    produced it, which is what a paste looks like once pasting is blocked."""
    length = CONFIG.low_ratio_min_length * 4
    summary = behaviour.summarise(_typed(20, gap_ms=150), length, CONFIG)
    found = dict(behaviour.evaluate(summary, None, CONFIG))
    assert "LOW_TYPED_RATIO" in found
    assert found["LOW_TYPED_RATIO"]["typed_ratio"] < CONFIG.low_ratio_threshold


def test_a_short_answer_is_never_judged_on_its_ratio() -> None:
    """"Final answer length > 150 chars". Below that the ratio is noise: a
    ten-character answer with two corrections is nothing."""
    summary = behaviour.summarise(_typed(5, gap_ms=150), CONFIG.low_ratio_min_length, CONFIG)
    assert "LOW_TYPED_RATIO" not in dict(behaviour.evaluate(summary, None, CONFIG))


def test_typing_the_whole_answer_yourself_produces_no_ratio_finding() -> None:
    length = CONFIG.low_ratio_min_length * 2
    summary = behaviour.summarise(_typed(length, gap_ms=150), length, CONFIG)
    assert "LOW_TYPED_RATIO" not in dict(behaviour.evaluate(summary, None, CONFIG))


def test_corrections_are_not_counted_as_characters_typed() -> None:
    """A backspace is a keystroke that removes a character. Counting it as one
    typed would make a heavily edited answer look pasted."""
    summary = behaviour.summarise(_typed(100, backspaces=30), 100, CONFIG)
    assert summary.keystrokes == 100
    assert summary.corrections == 30
    assert summary.typed_chars == 70


def test_the_baseline_stops_growing_once_it_is_established() -> None:
    """Otherwise a candidate could drift their own baseline upward one answer
    at a time and the relative rules would never fire again."""
    profile = _baseline_profile()
    before = list(profile["rates"])
    faster = behaviour.summarise(_typed(400, gap_ms=20), 400, CONFIG)
    after = behaviour.updated_profile(profile, faster, CONFIG)
    assert after["rates"] == before
    assert after["answers"] == CONFIG.baseline_answers


def test_an_answer_with_no_keystrokes_does_not_enter_the_baseline() -> None:
    """An MCQ click is not typing. Averaging it in would give every candidate
    a baseline of zero and make every later answer look infinitely faster."""
    empty = behaviour.summarise(AnswerBehaviourIn(), 0, CONFIG)
    assert behaviour.updated_profile(None, empty, CONFIG)["answers"] == 0
    assert behaviour.evaluate(empty, _baseline_profile(), CONFIG) == []


def test_pauses_are_counted_against_the_configured_gap() -> None:
    gap = int(CONFIG.pause_gap_seconds * MS) + 500
    offsets = [0, 100, 200, 200 + gap, 200 + gap + 100]
    summary = behaviour.summarise(
        AnswerBehaviourIn(keydown_offsets_ms=offsets, focus_ms=offsets[-1]), 5, CONFIG
    )
    assert summary.pauses == 1
    assert summary.pause_ms_total >= gap


def test_every_behaviour_finding_is_path_c() -> None:
    """Section 4.3. Nothing about how somebody typed may cost them a warning,
    let alone their assessment."""
    for event_type in ("FAST_TEXT_ENTRY", "UNIFORM_TEXT_ENTRY", "LOW_TYPED_RATIO",
                       "MOUSE_BEHAVIOR_DEVIATION"):
        assert catalog.spec_for(event_type).path == catalog.PATH_C


def test_the_aggregates_carry_no_raw_offsets() -> None:
    """"Store keystroke timings, not keystroke content" is the specification's
    rule; this is the stronger one the product keeps: the offsets themselves
    are reduced and dropped, so nothing persisted can rebuild a rhythm."""
    slow_profile = _baseline_profile(gap_ms=400)
    keys = (CONFIG.fast_entry_sustained_seconds * MS // 10) + 200
    burst = behaviour.summarise(_typed(keys, gap_ms=10), keys, CONFIG)
    for _event_type, aggregates in behaviour.evaluate(burst, slow_profile, CONFIG):
        for value in aggregates.values():
            assert not isinstance(value, (list, tuple, dict)), aggregates


def test_the_stored_profile_holds_rates_and_never_offsets() -> None:
    profile = _baseline_profile()
    assert set(profile) <= {"version", "answers", "rates", "mouse_rates"}
    assert len(profile["rates"]) == CONFIG.baseline_answers
    for rate in profile["rates"]:
        assert isinstance(rate, float)


# ══════════════════════════════════════════════════════════════════════════
# 4. FACE DESCRIPTOR DISTANCE (section 3.3)
# ══════════════════════════════════════════════════════════════════════════


def _descriptor(value: float = 0.0) -> list[float]:
    return [value] * FACE_DESCRIPTOR_WIDTH


def test_a_descriptor_compared_with_itself_is_at_zero_distance() -> None:
    assert identity.descriptor_distance(_descriptor(0.1), _descriptor(0.1)) == 0.0


def test_distance_is_euclidean() -> None:
    a = _descriptor(0.0)
    b = _descriptor(0.0)
    b[0] = 3.0
    b[1] = 4.0
    assert identity.descriptor_distance(a, b) == pytest.approx(5.0)


def test_distance_is_symmetric() -> None:
    a, b = _descriptor(0.2), _descriptor(0.9)
    assert identity.descriptor_distance(a, b) == identity.descriptor_distance(b, a)


def test_a_descriptor_of_the_wrong_width_is_refused_rather_than_truncated() -> None:
    """A vector from a different network compared against a face-api.js
    baseline gives a number that means nothing, and a meaningless number below
    the threshold reads as a match."""
    with pytest.raises(ValueError) as caught:
        identity.descriptor_distance([0.0] * 64, _descriptor())
    assert str(FACE_DESCRIPTOR_WIDTH) in str(caught.value)


def test_a_distance_inside_the_threshold_is_not_a_mismatch() -> None:
    assert not identity.is_mismatch(CONFIG.face_distance_threshold, CONFIG)
    assert not identity.is_mismatch(0.0, CONFIG)


def test_a_distance_beyond_the_threshold_is_a_mismatch() -> None:
    assert identity.is_mismatch(CONFIG.face_distance_threshold + 0.01, CONFIG)


def test_a_check_with_no_reported_distance_is_trusted_as_sent() -> None:
    """The browser is the only party that saw the face. Refusing an event that
    omits a diagnostic would make the rule depend on a field the specification
    does not require."""
    assert identity.is_mismatch(None, CONFIG)


def test_one_mismatch_is_never_enough_to_end_an_assessment() -> None:
    """Section 3.3: "Require two consecutive mismatches, never one, a single
    bad reading from lighting or angle must not end someone's assessment."
    The consecutive count is the configured one, and it is above one."""
    assert CONFIG.identity_consecutive_mismatches > 1
