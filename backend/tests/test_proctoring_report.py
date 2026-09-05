"""The Proctoring Report's content and its ordering (spec section 7).

The composer is a pure function over a session row and a list of events, so
every property section 7 states can be asserted directly rather than through
a database. What is checked here:

  * the number ban, run with `siddhi.numbers.scan`, the same walker the
    delivered PRISM payload goes through. The report travels INSIDE that
    payload, so a number here would refuse the whole document;
  * ordering, which section 7.1 says carries the weight;
  * that gaps are stated rather than hidden, which is section 9's rule and
    the difference between an honest report and a false clean one;
  * that a clean session reads plainly and positively.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.proctoring import (
    OUTCOME_ABANDONED,
    OUTCOME_COMPLETED,
    OUTCOME_TECHNICAL_FAILURE,
    OUTCOME_TERMINATED_INTEGRITY,
    OUTCOME_TERMINATED_WARNINGS,
    QUALITY_DEGRADED,
    QUALITY_GOOD,
)
from app.schemas.proctoring import ProctoringReportOut
from app.services.proctoring import catalog, ingestion, phrasing
from app.services.proctoring import report as proctoring_report
from app.services.siddhi import numbers

START = datetime(2026, 9, 2, 10, 0, tzinfo=timezone.utc)
EM_DASH = chr(8212)


class _Session:
    """The fields `compose` reads. A stand-in rather than an ORM row, because
    the composer is pure and a database here would only slow the failure."""

    def __init__(
        self,
        *,
        outcome: str = OUTCOME_COMPLETED,
        warnings_used: int = 0,
        termination_reason: str | None = None,
        session_quality: str = QUALITY_GOOD,
        ended: bool = True,
    ) -> None:
        self.id = uuid.uuid4()
        self.consented_at = START
        self.started_at = START
        self.ended_at = START + timedelta(minutes=42) if ended else None
        self.outcome = outcome
        self.warnings_used = warnings_used
        self.termination_reason = termination_reason
        self.session_quality = session_quality


def _event(
    event_type: str,
    *,
    minute: int = 1,
    duration_ms: int | None = None,
    warning_number: int | None = None,
    metadata: dict | None = None,
) -> proctoring_report.EventView:
    spec = catalog.spec_for(event_type)
    return proctoring_report.EventView(
        event_type=event_type,
        occurred_at=START + timedelta(minutes=minute),
        duration_ms=duration_ms,
        path=spec.path,
        warning_issued=warning_number is not None,
        warning_number=warning_number,
        metadata=metadata or {},
    )


def _compose(session: _Session, events: list, *, audio: bool = True) -> dict:
    return proctoring_report.compose(
        candidate_name="Priya Raman",
        assessment_name="Tatva Assessment for Platform Engineer",
        ps=session,
        events=events,
        audio_available=audio,
    )


def _out(content: dict) -> ProctoringReportOut:
    return ProctoringReportOut(**content, generated_at=START)


# ══════════════════════════════════════════════════════════════════════════
# THE NUMBER BAN
# ══════════════════════════════════════════════════════════════════════════


def _busy_events() -> list:
    """A session with something from every findings group, so the ban is run
    over a report that actually has content in it."""
    return [
        _event("WINDOW_FOCUS_LOST", minute=2, duration_ms=8_000, warning_number=1),
        _event("DEVICE_DETECTED_PHONE", minute=5, duration_ms=30_000, warning_number=2),
        _event("DEVICE_DETECTED_PHONE", minute=6, duration_ms=12_000,
               metadata={ingestion.NOTE_KEY: ingestion.NOTE_WITHIN_COOLDOWN}),
        _event("SECOND_PERSON_DETECTED", minute=9, duration_ms=20_000, warning_number=3),
        _event("SECOND_VOICE_DETECTED", minute=11, duration_ms=30_000),
        _event("FACE_ABSENT_BRIEF", minute=14, duration_ms=6_000),
        _event("LOW_LIGHT", minute=15, duration_ms=600_000),
        _event("BLOCKED_ACTION_ATTEMPTED", minute=17, metadata={"action": "paste"}),
        _event("BLOCKED_ACTION_ATTEMPTED", minute=18, metadata={"action": "copy"}),
        _event("FAST_TEXT_ENTRY", minute=21, duration_ms=40_000,
               metadata={"rate_ratio": 4.1, "peak_burst": 88}),
        _event("UNIFORM_TEXT_ENTRY", minute=24, duration_ms=55_000,
               metadata={"span_chars": 420, "corrections": 1}),
        _event("AI_TEXT_SIGNAL", minute=26, metadata={"probability": 0.94}),
        _event("MONITORING_INTERRUPTED", minute=30, duration_ms=45_000),
        _event("MULTIPLE_DISPLAYS_DETECTED", minute=33),
    ]


def test_a_busy_report_carries_no_number_at_any_path() -> None:
    """The same walker the delivered PRISM payload runs. A number anywhere in
    this subtree would refuse the whole document at the serialiser."""
    content = _compose(_Session(warnings_used=3), _busy_events())
    violations = numbers.scan(_out(content), path="proctoring")
    assert not violations, [str(v) for v in violations]


def test_the_report_model_itself_refuses_to_carry_a_number() -> None:
    """`ProctoringReportOut` is embedded in `FunctionalReportOut`, which is a
    `NumberFreeDelivery`. Checked here so the failure names this model rather
    than surfacing as a refused PRISM Report."""
    from app.schemas.assessments import FunctionalReportOut

    assert "proctoring" in FunctionalReportOut.model_fields
    content = _compose(_Session(warnings_used=2), _busy_events())
    assert not numbers.scan(_out(content), path="proctoring")


@pytest.mark.parametrize(
    "outcome,warnings,reason",
    [
        (OUTCOME_COMPLETED, 0, None),
        (OUTCOME_COMPLETED, 2, None),
        (OUTCOME_TERMINATED_WARNINGS, 3, "DEVICE_DETECTED_PHONE"),
        (OUTCOME_TERMINATED_INTEGRITY, 0, "IDENTITY_MISMATCH"),
        (OUTCOME_TERMINATED_INTEGRITY, 1, "CAMERA_OBSTRUCTED"),
        (OUTCOME_TECHNICAL_FAILURE, 0, "CAMERA_STREAM_FAILED"),
        (OUTCOME_ABANDONED, 0, None),
    ],
)
def test_every_outcome_composes_a_number_free_report(
    outcome: str, warnings: int, reason: str | None
) -> None:
    session = _Session(outcome=outcome, warnings_used=warnings, termination_reason=reason)
    content = _compose(session, _busy_events())
    assert not numbers.scan(_out(content), path="proctoring")


def test_the_only_digits_in_the_report_are_clock_times() -> None:
    """Counts and durations are words. A clock time is the one exception, and
    it appears in exactly two places: the date line and the log's time column."""
    content = _compose(_Session(warnings_used=3), _busy_events())
    allowed = {content["date_line"]} | {row["time"] for row in content["activity_log"]}
    for key in ("outcome", "summary", "closing", "candidate", "assessment"):
        assert not any(c.isdigit() for c in content[key]), (key, content[key])
    for group in content["findings"].values():
        for sentence in group:
            assert not any(c.isdigit() for c in sentence), sentence
    for row in content["activity_log"]:
        for cell_key in ("what_happened", "how_long", "what_the_system_did"):
            assert not any(c.isdigit() for c in row[cell_key]), row
        assert re.fullmatch(r"\d{2}:\d{2}", row["time"]), row["time"]
    assert allowed


def test_no_em_dash_reaches_the_report() -> None:
    content = _compose(_Session(warnings_used=1), _busy_events())
    assert EM_DASH not in repr(content)


def test_no_internal_identifier_reaches_the_report() -> None:
    content = _compose(_Session(warnings_used=3), _busy_events())
    rendered = repr(content)
    for identifier in catalog.CATALOG:
        assert identifier not in rendered, identifier


@pytest.mark.parametrize("word", phrasing.FORBIDDEN_WORDS)
def test_no_forbidden_word_reaches_a_composed_report(word: str) -> None:
    content = _compose(_Session(warnings_used=3), _busy_events())
    del content["date_line"]  # a month name, not a finding
    haystack = " ".join(
        [content["outcome"], content["summary"], content["closing"]]
        + [s for group in content["findings"].values() for s in group]
        + [
            f"{row['what_happened']} {row['how_long']} {row['what_the_system_did']}"
            for row in content["activity_log"]
        ]
    )
    assert not re.search(rf"(?<![a-z]){re.escape(word)}", haystack, re.IGNORECASE), word


# ══════════════════════════════════════════════════════════════════════════
# ORDERING AND CONTENT
# ══════════════════════════════════════════════════════════════════════════


def test_a_clean_session_says_so_plainly_and_positively() -> None:
    """Section 7.2: "If nothing happened, say so plainly and positively."""
    content = _compose(_Session(), [])
    assert content["outcome"] == "Assessment completed with no issues detected."
    assert phrasing.NO_EVENTS_AT_ALL in content["summary"]
    assert "not warned at any point" in content["summary"]
    for group in content["findings"].values():
        assert group == [phrasing.NO_ISSUES]
    assert content["activity_log"] == []
    assert content["monitoring_was_incomplete"] is False


def test_the_most_significant_finding_leads_the_summary() -> None:
    """Section 7.1: "Ordering carries the weight. Most significant finding
    first". A termination outranks a warning, which outranks a note, whatever
    order they happened in."""
    events = [
        _event("LOW_LIGHT", minute=1, duration_ms=60_000),
        _event("BLOCKED_ACTION_ATTEMPTED", minute=2),
        _event("WINDOW_FOCUS_LOST", minute=3, duration_ms=9_000, warning_number=1),
        _event("CAMERA_OBSTRUCTED", minute=40, duration_ms=70_000),
    ]
    content = _compose(
        _Session(
            outcome=OUTCOME_TERMINATED_INTEGRITY,
            warnings_used=1,
            termination_reason="CAMERA_OBSTRUCTED",
        ),
        events,
    )
    assert "camera was covered" in content["summary"].lower()
    camera = content["findings"]["camera"]
    assert "covered" in camera[0].lower(), camera


def test_a_warned_finding_precedes_an_unwarned_one_in_its_group() -> None:
    events = [
        _event("FULLSCREEN_EXITED", minute=1, duration_ms=5_000),
        _event("WINDOW_FOCUS_LOST", minute=9, duration_ms=30_000, warning_number=1),
        _event("MULTIPLE_DISPLAYS_DETECTED", minute=2, warning_number=2),
    ]
    content = _compose(_Session(warnings_used=2), events)
    screen = content["findings"]["screen_browser"]
    assert len(screen) >= 2
    assert "Left the assessment screen" in screen[0]


def test_repeats_inside_a_cooldown_count_once_but_add_their_time() -> None:
    """It is the same phone, still on the desk. Counting each detection would
    tell a recruiter it happened eleven times; ignoring the duration would say
    it was there for four seconds."""
    events = [
        _event("DEVICE_DETECTED_PHONE", minute=3, duration_ms=10_000, warning_number=1),
        _event("DEVICE_DETECTED_PHONE", minute=3, duration_ms=10_000,
               metadata={ingestion.NOTE_KEY: ingestion.NOTE_WITHIN_COOLDOWN}),
        _event("DEVICE_DETECTED_PHONE", minute=4, duration_ms=10_000,
               metadata={ingestion.NOTE_KEY: ingestion.NOTE_WITHIN_COOLDOWN}),
    ]
    content = _compose(_Session(warnings_used=1), events)
    camera = " ".join(content["findings"]["camera"])
    assert "once" in camera, camera
    assert "about half a minute" in camera, camera


def test_two_identifiers_that_read_the_same_fold_into_one_sentence() -> None:
    """A fullscreen exit and a focus loss are both "left the assessment
    screen" to a recruiter. Printing both would say the same thing twice."""
    events = [
        _event("FULLSCREEN_EXITED", minute=1, duration_ms=5_000, warning_number=1),
        _event("WINDOW_FOCUS_LOST", minute=2, duration_ms=5_000, warning_number=2),
    ]
    content = _compose(_Session(warnings_used=2), events)
    screen = [s for s in content["findings"]["screen_browser"] if "Left the assessment" in s]
    assert len(screen) == 1, content["findings"]["screen_browser"]
    assert "twice" in screen[0]


def test_the_blocked_action_sentence_appears_only_when_attempts_occurred() -> None:
    """Section 7.2: "If not: omit the sentence entirely."""
    without = _compose(_Session(), [_event("LOW_LIGHT", duration_ms=30_000)])
    assert not any(
        "blocked" in s.lower() for s in without["findings"]["screen_browser"]
    ), without["findings"]["screen_browser"]

    with_attempts = _compose(_Session(), [_event("BLOCKED_ACTION_ATTEMPTED", minute=4)])
    joined = " ".join(with_attempts["findings"]["screen_browser"])
    assert "blocked during the assessment" in joined
    assert "attempted these once" in joined


def test_a_monitoring_gap_is_stated_rather_than_hidden() -> None:
    """Section 9: "Silent monitoring gaps must be visible ... This is more
    honest and more useful than a false clean report."""
    content = _compose(_Session(), [_event("MONITORING_INTERRUPTED", minute=20, duration_ms=45_000)])
    assert "Monitoring was interrupted" in content["summary"]
    assert content["monitoring_was_incomplete"] is True


def test_a_degraded_device_is_recorded_as_context_not_as_misconduct() -> None:
    content = _compose(_Session(session_quality=QUALITY_DEGRADED), [])
    assert "reduced rate" in content["summary"]
    assert content["monitoring_was_incomplete"] is True


def test_unavailable_audio_says_so_rather_than_saying_nothing_was_heard() -> None:
    """A deployment with no diarization must not report a clean audio
    section: "no issues detected" would be a claim it never checked."""
    content = _compose(_Session(), [], audio=False)
    assert content["findings"]["audio"] == [phrasing.AUDIO_UNAVAILABLE]
    assert content["monitoring_was_incomplete"] is True


def test_available_audio_with_nothing_heard_reads_as_no_issues() -> None:
    content = _compose(_Session(), [], audio=True)
    assert content["findings"]["audio"] == [phrasing.NO_ISSUES]


def test_an_audio_service_failure_is_distinguished_from_never_having_one() -> None:
    events = [
        _event("SESSION_QUALITY_DEGRADED", minute=5,
               metadata={"note": phrasing.AUDIO_FAILED_NOTE})
    ]
    content = _compose(_Session(), events, audio=True)
    joined = " ".join(content["findings"]["audio"])
    assert "stopped working" in joined
    assert content["monitoring_was_incomplete"] is True


def test_the_activity_log_is_chronological_and_says_what_the_system_did() -> None:
    content = _compose(_Session(warnings_used=3), _busy_events())
    times = [row["time"] for row in content["activity_log"]]
    assert times == sorted(times)
    actions = {row["what_the_system_did"] for row in content["activity_log"]}
    assert "Issued the first warning" in actions
    assert "Issued the third warning" in actions
    assert "Noted it" in actions


def test_the_terminating_event_says_the_assessment_was_ended() -> None:
    events = [_event("IDENTITY_MISMATCH", minute=30)]
    content = _compose(
        _Session(
            outcome=OUTCOME_TERMINATED_INTEGRITY, termination_reason="IDENTITY_MISMATCH"
        ),
        events,
    )
    assert content["activity_log"][0]["what_the_system_did"] == "Ended the assessment"


def test_the_third_warning_under_a_terminate_policy_shows_as_the_ending() -> None:
    events = [
        _event("WINDOW_FOCUS_LOST", minute=2, duration_ms=9_000, warning_number=1),
        _event("DEVICE_DETECTED_PHONE", minute=5, duration_ms=9_000, warning_number=2),
        _event("SECOND_PERSON_DETECTED", minute=8, duration_ms=9_000, warning_number=3),
    ]
    content = _compose(
        _Session(
            outcome=OUTCOME_TERMINATED_WARNINGS,
            warnings_used=3,
            termination_reason="SECOND_PERSON_DETECTED",
        ),
        events,
    )
    assert content["activity_log"][-1]["what_the_system_did"] == "Ended the assessment"
    assert "your setting for this role" in content["outcome"]


def test_the_date_line_carries_the_start_and_end_times() -> None:
    content = _compose(_Session(), [])
    assert re.search(r"\d{2}:\d{2} to \d{2}:\d{2}", content["date_line"]), content["date_line"]
    assert "September 2026" in content["date_line"]


def test_a_session_with_no_end_time_still_renders_a_date_line() -> None:
    content = _compose(_Session(outcome=OUTCOME_ABANDONED, ended=False), [])
    assert "started at" in content["date_line"]


def test_the_report_timezone_matches_the_platforms_own() -> None:
    """A recruiter reading "10:32" reads their own wall clock. If the sweep
    schedule and the report disagreed, an operator correlating a log line
    with a report would be five and a half hours out."""
    from app.core import config

    assert proctoring_report.REPORT_TIMEZONE == config.PLATFORM_TIMEZONE


def test_the_closing_line_is_present_and_disclaims_the_score() -> None:
    content = _compose(_Session(), [])
    assert content["closing"] == phrasing.CLOSING
    assert "does not affect this candidate's score or ranking" in content["closing"]


def test_the_candidate_and_assessment_are_named() -> None:
    content = _compose(_Session(), [])
    assert content["candidate"] == "Priya Raman"
    assert "Platform Engineer" in content["assessment"]


def test_the_composed_shape_validates_as_the_delivered_model() -> None:
    """The composer writes the dict the schema reads. A key that drifted would
    fail here rather than at the moment a recruiter opened the report."""
    content = _compose(_Session(warnings_used=2), _busy_events())
    out = _out(content)
    assert out.findings.screen_browser
    assert out.activity_log
    assert set(content) == set(ProctoringReportOut.model_fields) - {"generated_at"}
