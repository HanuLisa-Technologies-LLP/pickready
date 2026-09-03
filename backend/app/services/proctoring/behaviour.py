"""Typing and pointer behaviour, evaluated at submission (section 4.5).

WHAT ARRIVES, AND WHAT IS KEPT
------------------------------
The browser sends TIMINGS: the millisecond offset of every keydown and of
every backspace, plus a handful of pointer aggregates. It never sends a
character; the answer is stored by the assessment module. This module reduces
the offsets to a few aggregates (a rate, a pause count, a correction count, a
typed-to-length ratio), writes the aggregates into `proctoring_events` and
`proctoring_sessions.behaviour_profile_json`, and drops the offsets. The raw
list is never persisted, so there is nothing to reconstruct a typing rhythm
from later.

THE CANDIDATE IS COMPARED WITH THEMSELVES
-----------------------------------------
"A naturally fast typist should not be flagged for being fast; they should
only be noted if they are dramatically faster than themselves." The baseline
is built from the candidate's first `baseline_answers` typed answers and
stored on the session. The two relative rules (fast entry, pointer deviation)
do not fire until it exists; the two absolute rules (a long uninterrupted
span, a typed-to-length ratio below the floor) describe the answer alone and
need no baseline. An answer with no keystrokes at all (an MCQ click) adds
nothing to the baseline and is never judged against it.

EVERYTHING HERE IS PATH C. A behaviour observation is recorded for the report
and never warns, never terminates, and, like everything in this package,
never reaches a score. `MISSING_BASELINE` is not an error: it is the ordinary
state of the first two answers, and `record_answer_behaviour` never raises
for it.

THE KEYSTROKE LIST INCLUDES THE BACKSPACES. `keydown_offsets_ms` is every
key the field saw and `backspace_offsets_ms` is the subset that deleted, so
"characters typed" is the difference. The client capture module is written to
that contract.
"""
from __future__ import annotations

import logging
import statistics
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assessment import AssessmentConversation
from app.models.proctoring import ProctoringEvent, ProctoringSession
from app.schemas.assessments import AnswerBehaviourIn
from app.services.proctoring import catalog
from app.services.proctoring.config import ProctoringConfig, get_config

logger = logging.getLogger(__name__)

__all__ = [
    "AnswerSummary",
    "summarise",
    "evaluate",
    "updated_profile",
    "baseline_established",
    "record_answer_behaviour",
    "PROFILE_VERSION",
]

_MS_PER_SECOND = 1000
PROFILE_VERSION = 1
#: Two points make a span. Arithmetic, not a threshold: with one keystroke
#: there is no interval to measure and no rate to compute, at any setting.
_MIN_KEYSTROKES_FOR_A_SPAN = 2


@dataclass(frozen=True)
class AnswerSummary:
    """The aggregates one answer reduces to. Numbers, internal, never shown."""

    keystrokes: int
    corrections: int
    typed_chars: int
    final_length: int
    #: Characters typed divided by the final answer length; None when there is
    #: nothing to divide.
    typed_ratio: float | None
    #: First keydown to last keydown.
    active_ms: int
    #: Keystrokes per second over the active span.
    overall_rate: float | None
    #: The highest keystrokes-per-second held over any span at least
    #: `fast_entry_sustained_seconds` long.
    sustained_rate: float | None
    pauses: int
    pause_ms_total: int
    #: Most keystrokes in any `burst_window_seconds` window.
    peak_burst: int
    #: Longest run of keystrokes with no gap over `uniform_max_pause_seconds`.
    longest_uniform_span: int
    #: Backspaces inside that run.
    uniform_span_corrections: int
    #: Pointer path length per second of field focus.
    mouse_rate: float | None
    mouse_idle_share: float | None


def _windows_max_rate(offsets: list[int], span_ms: int) -> float | None:
    """The highest rate held over any window at least `span_ms` long."""
    if len(offsets) < _MIN_KEYSTROKES_FOR_A_SPAN or offsets[-1] - offsets[0] < span_ms:
        return None
    best: float | None = None
    j = 0
    for i in range(len(offsets)):
        while j < len(offsets) and offsets[j] - offsets[i] < span_ms:
            j += 1
        if j >= len(offsets):
            break
        span = offsets[j] - offsets[i]
        rate = (j - i + 1) / (span / _MS_PER_SECOND)
        if best is None or rate > best:
            best = rate
    return best


def _peak_burst(offsets: list[int], window_ms: int) -> int:
    best = 0
    j = 0
    for i in range(len(offsets)):
        while j < len(offsets) and offsets[j] - offsets[i] <= window_ms:
            j += 1
        best = max(best, j - i)
    return best


def _longest_uniform_span(
    offsets: list[int], backspaces: list[int], max_gap_ms: int
) -> tuple[int, int]:
    """(keystrokes, corrections) of the longest run with no gap over the cap."""
    if not offsets:
        return 0, 0
    best_len, best_corrections = 0, 0
    start = 0
    for index in range(1, len(offsets) + 1):
        closed = index == len(offsets) or offsets[index] - offsets[index - 1] > max_gap_ms
        if not closed:
            continue
        length = index - start
        if length > best_len:
            lo, hi = offsets[start], offsets[index - 1]
            best_len = length
            best_corrections = sum(1 for b in backspaces if lo <= b <= hi)
        start = index
    return best_len, best_corrections


def summarise(
    behaviour: AnswerBehaviourIn, final_length: int, config: ProctoringConfig
) -> AnswerSummary:
    """Reduce one answer's timings to aggregates. Pure."""
    offsets = sorted(int(v) for v in behaviour.keydown_offsets_ms)
    backspaces = sorted(int(v) for v in behaviour.backspace_offsets_ms)
    keystrokes = len(offsets)
    corrections = len(backspaces)
    typed_chars = max(0, keystrokes - corrections)
    final_length = max(0, int(final_length))
    typed_ratio = (typed_chars / final_length) if final_length > 0 else None
    active_ms = (
        (offsets[-1] - offsets[0]) if keystrokes >= _MIN_KEYSTROKES_FOR_A_SPAN else 0
    )
    overall_rate = (keystrokes / (active_ms / _MS_PER_SECOND)) if active_ms > 0 else None

    pause_gap_ms = int(config.pause_gap_seconds * _MS_PER_SECOND)
    gaps = [b - a for a, b in zip(offsets, offsets[1:])]
    long_gaps = [gap for gap in gaps if gap > pause_gap_ms]

    span_len, span_corrections = _longest_uniform_span(
        offsets, backspaces, int(config.uniform_max_pause_seconds * _MS_PER_SECOND)
    )
    focus_seconds = behaviour.focus_ms / _MS_PER_SECOND if behaviour.focus_ms > 0 else None
    mouse_rate = (behaviour.mouse_path_px / focus_seconds) if focus_seconds else None
    idle_share = (
        min(1.0, behaviour.mouse_idle_ms / behaviour.focus_ms) if behaviour.focus_ms > 0 else None
    )
    return AnswerSummary(
        keystrokes=keystrokes,
        corrections=corrections,
        typed_chars=typed_chars,
        final_length=final_length,
        typed_ratio=typed_ratio,
        active_ms=active_ms,
        overall_rate=overall_rate,
        sustained_rate=_windows_max_rate(
            offsets, int(config.fast_entry_sustained_seconds * _MS_PER_SECOND)
        ),
        pauses=len(long_gaps),
        pause_ms_total=sum(long_gaps),
        peak_burst=_peak_burst(offsets, int(config.burst_window_seconds * _MS_PER_SECOND)),
        longest_uniform_span=span_len,
        uniform_span_corrections=span_corrections,
        mouse_rate=mouse_rate,
        mouse_idle_share=idle_share,
    )


def _empty_profile() -> dict[str, Any]:
    return {"version": PROFILE_VERSION, "answers": 0, "rates": [], "mouse_rates": []}


def baseline_established(profile: dict[str, Any] | None, config: ProctoringConfig) -> bool:
    return bool(profile) and int(profile.get("answers", 0)) >= config.baseline_answers


def _baseline_rate(profile: dict[str, Any]) -> float | None:
    rates = [float(r) for r in profile.get("rates", []) if r]
    return statistics.median(rates) if rates else None


def _baseline_mouse_rate(profile: dict[str, Any]) -> float | None:
    rates = [float(r) for r in profile.get("mouse_rates", []) if r]
    return statistics.median(rates) if rates else None


def evaluate(
    summary: AnswerSummary, profile: dict[str, Any] | None, config: ProctoringConfig
) -> list[tuple[str, dict[str, Any]]]:
    """The four section 4.5 rules over one answer. Pure. Returns
    (event_type, aggregates) pairs; the aggregates become `metadata_json`."""
    found: list[tuple[str, dict[str, Any]]] = []
    established = baseline_established(profile, config)
    baseline = _baseline_rate(profile or {}) if established else None

    if (
        baseline
        and summary.sustained_rate is not None
        and summary.sustained_rate > config.fast_entry_multiplier * baseline
    ):
        found.append(
            (
                "FAST_TEXT_ENTRY",
                {
                    "rate_ratio": round(summary.sustained_rate / baseline, 2),
                    "sustained_seconds": config.fast_entry_sustained_seconds,
                    "peak_burst": summary.peak_burst,
                },
            )
        )
    if (
        summary.longest_uniform_span >= config.uniform_span_chars
        and summary.uniform_span_corrections < config.uniform_max_corrections
    ):
        found.append(
            (
                "UNIFORM_TEXT_ENTRY",
                {
                    "span_chars": summary.longest_uniform_span,
                    "corrections": summary.uniform_span_corrections,
                    "pauses": summary.pauses,
                },
            )
        )
    if (
        summary.final_length > config.low_ratio_min_length
        and summary.typed_ratio is not None
        and summary.typed_ratio < config.low_ratio_threshold
    ):
        found.append(
            (
                "LOW_TYPED_RATIO",
                {
                    "typed_ratio": round(summary.typed_ratio, 3),
                    "typed_chars": summary.typed_chars,
                    "final_length": summary.final_length,
                },
            )
        )
    baseline_mouse = _baseline_mouse_rate(profile or {}) if established else None
    if baseline_mouse and summary.mouse_rate is not None:
        # The configuration carries ONE deviation multiplier. The pointer
        # comparison reuses it deliberately, in both directions, rather than
        # inventing a second number the specification does not state.
        factor = config.fast_entry_multiplier
        if summary.mouse_rate > factor * baseline_mouse or summary.mouse_rate * factor < baseline_mouse:
            found.append(
                (
                    "MOUSE_BEHAVIOR_DEVIATION",
                    {
                        "rate_ratio": round(summary.mouse_rate / baseline_mouse, 2),
                        "idle_share": round(summary.mouse_idle_share or 0.0, 2),
                    },
                )
            )
    return found


def updated_profile(
    profile: dict[str, Any] | None, summary: AnswerSummary, config: ProctoringConfig
) -> dict[str, Any]:
    """The baseline after this answer. Only the first `baseline_answers` typed
    answers contribute; later ones are judged, not averaged in, so a candidate
    cannot drift their own baseline upward one answer at a time."""
    current = dict(profile or _empty_profile())
    current.setdefault("rates", [])
    current.setdefault("mouse_rates", [])
    if summary.overall_rate is None or summary.keystrokes == 0:
        return current
    if int(current.get("answers", 0)) >= config.baseline_answers:
        return current
    current["answers"] = int(current.get("answers", 0)) + 1
    current["rates"] = [*current["rates"], round(summary.overall_rate, 3)]
    if summary.mouse_rate is not None:
        current["mouse_rates"] = [*current["mouse_rates"], round(summary.mouse_rate, 3)]
    current["version"] = PROFILE_VERSION
    return current


async def record_answer_behaviour(
    session: AsyncSession,
    proctoring_session: ProctoringSession,
    conversation: AssessmentConversation,
    question_id: uuid.UUID | None,
    behaviour: AnswerBehaviourIn,
    final_length: int,
) -> list[str]:
    """Evaluate one submitted answer's timings and record what was notable.

    Never raises for a missing baseline. Returns the event types written, for
    the caller's log line; the aggregates are on the rows.
    """
    config = get_config()
    now = datetime.now(timezone.utc)
    summary = summarise(behaviour, final_length, config)
    profile = proctoring_session.behaviour_profile_json
    written: list[str] = []
    for event_type, aggregates in evaluate(summary, profile, config):
        session.add(
            ProctoringEvent(
                tenant_id=proctoring_session.tenant_id,
                proctoring_session_id=proctoring_session.id,
                event_type=event_type,
                occurred_at=now,
                duration_ms=summary.active_ms or None,
                path=catalog.PATH_C,
                warning_issued=False,
                warning_number=None,
                confidence=None,
                question_id=question_id,
                metadata_json={**aggregates, "conversation_id": str(conversation.id)},
            )
        )
        written.append(event_type)
    proctoring_session.behaviour_profile_json = updated_profile(profile, summary, config)
    proctoring_session.updated_at = now
    await session.flush()
    if written:
        logger.info(
            "proctoring.behaviour session_id=%s question_id=%s events=%s",
            proctoring_session.id, question_id, ",".join(written),
        )
    return written
