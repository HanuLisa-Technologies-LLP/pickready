"""The sentence library (proctoring-spec-doc.md sections 7.3, 7.4 and 8.3).

TWO AUDIENCES, ONE FILE, BECAUSE THE RULES ARE THE SAME
-------------------------------------------------------
The candidate reads a warning; the recruiter reads a report. Both are plain
language, both name what happened and what the system did, and neither ever
carries an internal identifier, a count in digits, a duration in
milliseconds, a model name or a word that implies the candidate cheated. The
specification lists the forbidden vocabulary (section 7.1) and
`tests/test_proctoring_phrasing.py` sweeps every sentence this module can
produce against it, so a sentence added here without the word "flag" is a
sentence a test has read.

"Implement as a lookup, not string concatenation scattered through code."
Every template lives in a table keyed by catalog event type; the functions
below fill the table entries and spell the numbers. A caller that wants a
sentence asks for it by event type and never assembles one.

COUNTS AND DURATIONS ARE WORDS. "twice", "about half a minute". The only digits
allowed anywhere in a report are clock times in the date line and the activity
log, which `report.py` renders itself. Nothing in this file emits a digit, and
the test asserts that over the whole table at every count and duration.
"""
from __future__ import annotations

from app.models.proctoring import (
    OUTCOME_ABANDONED,
    OUTCOME_COMPLETED,
    OUTCOME_TECHNICAL_FAILURE,
    OUTCOME_TERMINATED_INTEGRITY,
    OUTCOME_TERMINATED_WARNINGS,
    POLICY_TERMINATE,
)
from app.services.proctoring import catalog

__all__ = [
    "count_word",
    "ordinal_word",
    "duration_phrase",
    "finding_sentence",
    "activity_description",
    "system_action",
    "outcome_sentence",
    "warning_message",
    "termination_message",
    "NO_ISSUES",
    "NO_EVENTS_AT_ALL",
    "CLOSING",
    "INFORMATIONAL_NOTE",
    "BLOCKED_ACTIONS_NOTE",
    "AUDIO_UNAVAILABLE",
    "MONITORING_REDUCED_RATE",
    "FORBIDDEN_WORDS",
]

#: Section 7.1's forbidden vocabulary, plus the words that imply a verdict.
#: Read by the phrasing test, and by nothing else: this list is what the
#: sentences below are checked against, not something the code consults.
FORBIDDEN_WORDS: tuple[str, ...] = (
    "strike",
    "tier",
    "violation",
    "flag",
    "anomaly",
    "anomalous",
    "signal",
    "confidence",
    "threshold",
    "severity",
    "cheat",
    "cheated",
    "cheating",
    "fraud",
    "suspicious",
    "dishonest",
    "misconduct",
    "event_type",
    "path a",
    "path b",
    "path c",
    "%",
    "percent",
    "ms",
    "millisecond",
)

NO_ISSUES = "No issues detected."
NO_EVENTS_AT_ALL = "No issues were detected during this assessment."
CLOSING = (
    "This report reflects only what the system detected during the "
    "assessment. It does not affect this candidate's score or ranking; how "
    "much weight to give it is entirely your decision."
)
INFORMATIONAL_NOTE = (
    "Final section of the PRISM Report. Informational only: it does not "
    "affect this candidate's score or ranking."
)
#: Stated only when attempts occurred (section 7.2). Section 4.4 is explicit
#: that browser-level blocking stops an ordinary candidate and not a determined
#: one, so the sentence says what is blocked and never that blocking is
#: unbreakable.
BLOCKED_ACTIONS_NOTE = (
    "Copy-paste, right-click, and developer tools are blocked during the "
    "assessment. The candidate attempted these {times}."
)
AUDIO_UNAVAILABLE = (
    "Audio monitoring was not available for this assessment, so a second "
    "voice could not have been detected."
)
MONITORING_REDUCED_RATE = (
    "The candidate's device could not keep up with the usual monitoring rate, "
    "so camera monitoring ran at a reduced rate for part of the assessment."
)

_ONES = (
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen", "twenty",
)
_ORDINALS = (
    "zeroth", "first", "second", "third", "fourth", "fifth", "sixth",
    "seventh", "eighth", "ninth", "tenth",
)
_MS_PER_SECOND = 1000
_SECONDS_PER_MINUTE = 60
_MINUTES_PER_HOUR = 60


def number_word(n: int) -> str:
    """A small integer in words; beyond the table it is 'more than twenty'."""
    n = max(0, int(n))
    if n < len(_ONES):
        return _ONES[n]
    return f"more than {_ONES[-1]}"


def count_word(n: int) -> str:
    """'once', 'twice', 'three times', ... 'more than twenty times'."""
    n = max(0, int(n))
    if n == 0:
        return "never"
    if n == 1:
        return "once"
    if n == 2:
        return "twice"
    return f"{number_word(n)} times"


def ordinal_word(n: int) -> str:
    n = max(0, int(n))
    if n < len(_ORDINALS):
        return _ORDINALS[n]
    return f"number {number_word(n)}"


def duration_phrase(total_ms: int | None) -> str:
    """An approximate, human duration. Never a figure in milliseconds."""
    if total_ms is None or total_ms <= 0:
        return "a moment"
    seconds = total_ms / _MS_PER_SECOND
    if seconds < 5:
        return "a few seconds"
    if seconds < 15:
        return "about ten seconds"
    if seconds < 25:
        return "about twenty seconds"
    if seconds < 45:
        return "about half a minute"
    if seconds < 90:
        return "about a minute"
    minutes = seconds / _SECONDS_PER_MINUTE
    if minutes < 20:
        return f"about {number_word(round(minutes))} minutes"
    if minutes < 45:
        return "about half an hour"
    if minutes < 90:
        return "about an hour"
    hours = minutes / _MINUTES_PER_HOUR
    return f"about {number_word(round(hours))} hours"


# ── Recruiter phrasing (section 7.4) ─────────────────────────────────────────
#
# `{times}` is the count in words and `{duration}` the total duration phrase.
# A template with neither describes a fact whose count carries no meaning
# (more than one screen is connected or it is not).

_FINDINGS: dict[str, str] = {
    "FULLSCREEN_EXITED": "Left the assessment screen {times}, for about {duration}.",
    "WINDOW_FOCUS_LOST": "Left the assessment screen {times}, for about {duration}.",
    "DEVICE_DETECTED_PHONE": "A phone was visible on camera {times}, for about {duration}.",
    "DEVICE_DETECTED_LAPTOP": "A laptop was visible on camera {times}, for about {duration}.",
    "DEVICE_DETECTED_SCREEN": (
        "A second screen or television was visible on camera {times}, for "
        "about {duration}."
    ),
    "SECOND_PERSON_DETECTED": "Another person appeared on camera {times}, for about {duration}.",
    "SECOND_VOICE_DETECTED": "Another voice was heard {times} during the assessment.",
    "FACE_ABSENT_BRIEF": "The candidate was out of camera view {times}, for about {duration}.",
    "FACE_ABSENT_MODERATE": "The candidate was out of camera view {times}, for about {duration}.",
    "FACE_ABSENT_EXTENDED": (
        "The candidate was out of camera view for about {duration}, and the "
        "assessment was ended."
    ),
    "MULTIPLE_DISPLAYS_DETECTED": (
        "More than one screen was connected to the candidate's computer."
    ),
    "IDENTITY_MISMATCH": (
        "The system could not confirm that the same person remained present "
        "throughout, and the assessment was ended."
    ),
    "IDENTITY_CHECK_MISMATCH": (
        "A routine identity check did not match the candidate's appearance at "
        "the start {times}. A single check can differ because of lighting or "
        "the angle of the camera."
    ),
    "CAMERA_OBSTRUCTED": (
        "The camera was covered for about {duration}, and the assessment was "
        "ended."
    ),
    "CAMERA_PERMISSION_LOST": (
        "Camera access was switched off during the assessment, and the "
        "assessment was ended."
    ),
    "MIC_PERMISSION_LOST": (
        "Microphone access was switched off during the assessment, and the "
        "assessment was ended."
    ),
    "CAMERA_STREAM_FAILED": (
        "The camera stopped working and did not recover within the time "
        "allowed, so the assessment could not continue. This is a technical "
        "problem, not candidate behaviour."
    ),
    "CAMERA_STREAM_INTERRUPTED": (
        "The camera stream was interrupted {times}, for about {duration}, and "
        "recovered."
    ),
    "INTEGRITY_CHECK_FAILED": (
        "The monitoring components stopped responding and did not recover, so "
        "the assessment could not continue."
    ),
    "INTEGRITY_CHECK_WARNING": (
        "The monitoring components stopped responding {times}, for about "
        "{duration}, and recovered."
    ),
    "MONITORING_INTERRUPTED": (
        "Monitoring was interrupted for about {duration} during the assessment."
    ),
    "SESSION_QUALITY_DEGRADED": MONITORING_REDUCED_RATE,
    "LOW_LIGHT": (
        "Lighting was poor for part of the assessment, which reduces what the "
        "camera can see. This is context about the room, not about the candidate."
    ),
    "BLOCKED_ACTION_ATTEMPTED": BLOCKED_ACTIONS_NOTE,
    "FAST_TEXT_ENTRY": (
        "{answers} completed far faster than the candidate's own typing "
        "elsewhere in the assessment makes likely. This can also happen when a "
        "candidate has planned their answer in advance."
    ),
    "UNIFORM_TEXT_ENTRY": (
        "{answers} entered in a single uninterrupted stretch with almost no "
        "corrections, which is unusual. This can also happen when a candidate "
        "has planned their answer in advance."
    ),
    "LOW_TYPED_RATIO": (
        "{answers} contained more text than the keystrokes recorded for it "
        "would produce. This can happen with dictation software or an "
        "assistive input method as well as with text brought in from elsewhere."
    ),
    "MOUSE_BEHAVIOR_DEVIATION": (
        "{answers} showed pointer movement very different from the "
        "candidate's own pattern earlier in the assessment."
    ),
    "AI_TEXT_SIGNAL": (
        "Part of {answers_lower} showed some characteristics of AI-generated "
        "text. This particular check is not reliable enough to draw "
        "conclusions from on its own."
    ),
}


def _answers_phrase(n: int) -> str:
    return "One answer was" if n <= 1 else f"{number_word(n).capitalize()} answers were"


def _answers_lower(n: int) -> str:
    return "one written answer" if n <= 1 else f"{number_word(n)} written answers"


def finding_sentence(event_type: str, *, times: int, duration_ms: int | None) -> str:
    """The recruiter-facing sentence for `times` occurrences of one event type
    totalling `duration_ms`. Raises on an event type with no phrasing rather
    than printing the identifier, because the identifier is the one thing
    section 7.1 says a recruiter must never see."""
    if event_type not in catalog.CATALOG:
        raise KeyError(f"{event_type!r} is not a proctoring event type")
    template = _FINDINGS[event_type]
    return template.format(
        times=count_word(times),
        duration=duration_phrase(duration_ms),
        answers=_answers_phrase(times),
        answers_lower=_answers_lower(times),
    )


# ── Activity log (section 7.2) ───────────────────────────────────────────────

_ACTIVITY: dict[str, str] = {
    "FULLSCREEN_EXITED": "Left fullscreen",
    "WINDOW_FOCUS_LOST": "Left the assessment window",
    "DEVICE_DETECTED_PHONE": "A phone was visible on camera",
    "DEVICE_DETECTED_LAPTOP": "A laptop was visible on camera",
    "DEVICE_DETECTED_SCREEN": "A second screen or television was visible on camera",
    "SECOND_PERSON_DETECTED": "Another person appeared on camera",
    "SECOND_VOICE_DETECTED": "Another voice was heard",
    "FACE_ABSENT_BRIEF": "Briefly out of camera view",
    "FACE_ABSENT_MODERATE": "Out of camera view",
    "FACE_ABSENT_EXTENDED": "Out of camera view for an extended period",
    "MULTIPLE_DISPLAYS_DETECTED": "More than one screen connected",
    "IDENTITY_MISMATCH": "The same person could not be confirmed present",
    "IDENTITY_CHECK_MISMATCH": "An identity check did not match",
    "CAMERA_OBSTRUCTED": "The camera was covered",
    "CAMERA_PERMISSION_LOST": "Camera access was switched off",
    "MIC_PERMISSION_LOST": "Microphone access was switched off",
    "CAMERA_STREAM_FAILED": "The camera stopped working and did not recover",
    "CAMERA_STREAM_INTERRUPTED": "The camera stream was interrupted and recovered",
    "INTEGRITY_CHECK_FAILED": "Monitoring stopped responding and did not recover",
    "INTEGRITY_CHECK_WARNING": "Monitoring stopped responding and recovered",
    "MONITORING_INTERRUPTED": "Monitoring was interrupted",
    "SESSION_QUALITY_DEGRADED": "Monitoring ran at a reduced rate",
    "LOW_LIGHT": "Lighting was poor",
    "BLOCKED_ACTION_ATTEMPTED": "A blocked action was attempted",
    "FAST_TEXT_ENTRY": "An answer was typed much faster than the candidate's usual pace",
    "UNIFORM_TEXT_ENTRY": "An answer was typed in one uninterrupted stretch",
    "LOW_TYPED_RATIO": "An answer contained more text than was typed",
    "MOUSE_BEHAVIOR_DEVIATION": "Pointer movement differed from the candidate's usual pattern",
    "AI_TEXT_SIGNAL": "An answer showed some characteristics of AI-generated text",
}

#: Once per session the audio note is the one degraded-quality event that is
#: about the platform rather than the device.
AUDIO_UNAVAILABLE_NOTE = "audio_analysis_unavailable"
AUDIO_FAILED_NOTE = "audio_analysis_failed"
AUDIO_ACTIVITY = "Audio monitoring was not available"


def activity_description(event_type: str, metadata: dict | None = None) -> str:
    if event_type not in catalog.CATALOG:
        raise KeyError(f"{event_type!r} is not a proctoring event type")
    note = (metadata or {}).get("note")
    if event_type == "SESSION_QUALITY_DEGRADED" and note in (AUDIO_UNAVAILABLE_NOTE, AUDIO_FAILED_NOTE):
        return AUDIO_ACTIVITY
    return _ACTIVITY[event_type]


def system_action(
    *, path: str, warning_issued: bool, warning_number: int | None, terminated: bool
) -> str:
    """The "What the system did" cell."""
    if terminated:
        return "Ended the assessment"
    if warning_issued and warning_number is not None:
        return f"Issued the {ordinal_word(warning_number)} warning"
    if path == catalog.PATH_B:
        return "Noted it, no further warning"
    return "Noted it"


# ── Outcome (section 7.3) ────────────────────────────────────────────────────

_CAMERA_REASONS = frozenset(
    {"CAMERA_OBSTRUCTED", "FACE_ABSENT_EXTENDED", "CAMERA_PERMISSION_LOST", "MIC_PERMISSION_LOST"}
)


def outcome_sentence(outcome: str, *, warnings: int, termination_reason: str | None) -> str:
    if outcome == OUTCOME_COMPLETED:
        if warnings <= 0:
            return "Assessment completed with no issues detected."
        return (
            "Assessment completed. The candidate was warned "
            f"{count_word(warnings)} during the session."
        )
    if outcome == OUTCOME_TERMINATED_WARNINGS:
        return (
            "Assessment ended early. The candidate crossed the warning limit "
            "and, per your setting for this role, the assessment was stopped."
        )
    if outcome == OUTCOME_TERMINATED_INTEGRITY:
        if termination_reason == "IDENTITY_MISMATCH":
            return (
                "Assessment ended early. The system could not confirm that the "
                "same person remained present throughout."
            )
        if termination_reason in _CAMERA_REASONS:
            return (
                "Assessment ended early. The camera was covered or unavailable "
                "for an extended period."
            )
        return (
            "Assessment ended early. Monitoring could not continue, and the "
            "assessment was stopped."
        )
    if outcome == OUTCOME_TECHNICAL_FAILURE:
        return (
            "Assessment could not be completed due to a technical problem, not "
            "candidate behaviour."
        )
    if outcome == OUTCOME_ABANDONED:
        return (
            "Assessment was not completed. The candidate stopped answering and "
            "did not return."
        )
    return "Assessment is still in progress."


# ── Candidate-facing warnings (section 8.3) ──────────────────────────────────
#
# (what happened, what to do). Specific and actionable, never accusatory.

_WARNINGS: dict[str, tuple[str, str]] = {
    "FULLSCREEN_EXITED": (
        "The assessment left fullscreen",
        "return to fullscreen and stay there",
    ),
    "WINDOW_FOCUS_LOST": (
        "You left the assessment window",
        "keep this window in front until you finish",
    ),
    "DEVICE_DETECTED_PHONE": (
        "A phone was detected on camera",
        "move it out of view",
    ),
    "DEVICE_DETECTED_LAPTOP": (
        "A laptop was detected on camera",
        "move it out of view",
    ),
    "DEVICE_DETECTED_SCREEN": (
        "A second screen was detected on camera",
        "move it out of view or switch it off",
    ),
    "SECOND_PERSON_DETECTED": (
        "Another person was detected on camera",
        "make sure you are alone in the room",
    ),
    "SECOND_VOICE_DETECTED": (
        "Another voice was heard",
        "make sure nobody else is speaking near you",
    ),
    "FACE_ABSENT_MODERATE": (
        "Your face was out of camera view",
        "stay in front of the camera",
    ),
    "MULTIPLE_DISPLAYS_DETECTED": (
        "More than one screen is connected to your computer",
        "disconnect the extra screen",
    ),
}


def warning_message(
    event_type: str, *, number: int, max_warnings: int, policy: str
) -> str:
    """The whole warning, composed server-side so the client never words it.

    First and second warnings tell the candidate where they stand; the last
    one says what the recruiter's setting decided.
    """
    if event_type not in _WARNINGS:
        raise KeyError(f"{event_type!r} is not a warning event")
    what, do = _WARNINGS[event_type]
    total = number_word(max_warnings)
    if number >= max_warnings:
        if policy == POLICY_TERMINATE:
            return f"{what}. Your assessment has ended."
        return f"{what}. You may continue, but this has been noted in your report."
    if number == 1:
        return f"{what}. Please {do}. This is your first of {total} warnings."
    remaining = max_warnings - number
    if remaining == 1:
        tail = "one more and your assessment may end"
    else:
        tail = f"{number_word(remaining)} more and your assessment may end"
    return (
        f"{what} again. Please {do}. This is your {ordinal_word(number)} of "
        f"{total} warnings, {tail}."
    )


# ── Candidate-facing termination (section 4.1) ───────────────────────────────

_TERMINATIONS: dict[str, str] = {
    "IDENTITY_MISMATCH": (
        "Your assessment has ended because the system could not confirm that "
        "the same person stayed in front of the camera."
    ),
    "CAMERA_OBSTRUCTED": (
        "Your assessment has ended because the camera was covered for too long."
    ),
    "FACE_ABSENT_EXTENDED": (
        "Your assessment has ended because you were out of camera view for too "
        "long."
    ),
    "CAMERA_PERMISSION_LOST": (
        "Your assessment has ended because camera access was switched off."
    ),
    "MIC_PERMISSION_LOST": (
        "Your assessment has ended because microphone access was switched off."
    ),
    "CAMERA_STREAM_FAILED": (
        "Your assessment has ended because the camera stopped working and could "
        "not be restarted."
    ),
    "INTEGRITY_CHECK_FAILED": (
        "Your assessment has ended because monitoring stopped working and could "
        "not be restored."
    ),
}
_TERMINATION_WARNINGS = (
    "Your assessment has ended because the warning limit for this role was "
    "reached."
)
_TERMINATION_TAIL = (
    " Your answers up to this point have been saved, and the hiring team has "
    "been told why the assessment ended."
)


def termination_message(reason_code: str, *, outcome: str) -> str:
    if outcome == OUTCOME_TERMINATED_WARNINGS:
        return _TERMINATION_WARNINGS + _TERMINATION_TAIL
    if reason_code not in _TERMINATIONS:
        raise KeyError(f"{reason_code!r} is not a termination reason")
    return _TERMINATIONS[reason_code] + _TERMINATION_TAIL
