"""Per-turn observability for the conversational assessment, as structured logs.

WHY THIS EXISTS, AND WHY TRACING WAS NOT ENOUGH
-----------------------------------------------
`services/tracing.py` puts a LangSmith run around every LLM call at the one
chokepoint, `llm_router.invoke_llm`. That answers "was the model called, did it
answer, how long did it take". It cannot answer anything about the INTERVIEW,
because a conversation is not an LLM call: it is a sequence of turns, some of
which adapt, some of which fall back to stored text, and one of which may be
the reason a candidate says the interview was bad.

On 2026-08-05 a candidate reported exactly that, and there was no way to
reconstruct it afterwards. How many turns actually adapted? How many answers
were classified as non-answers? Did any turn degrade to the scripted path
because a provider was down? A per-call trace shows none of that, and no amount
of extra tracing at the router would, because the router does not know what a
turn is.

WHY A LOG LINE AND NOT A METRICS CLIENT
---------------------------------------
The backend runs on ECS Fargate, whose awslogs driver ships stdout straight to
CloudWatch Logs. A
structured `key=value` line IS the metric: it is queryable, it is already
retained, and it costs no new dependency, no client to initialise, no endpoint
to be unreachable. A metrics client would be a second network dependency on the
live request path of a candidate mid-assessment, which is the one place this
module must not add risk. The `key=value` shape matches what `llm_router` and
`functional_assessment` already emit, so one query pattern covers all of them.

THE SUMMARY DICT IS OPERATOR DATA AND MUST NEVER REACH A CLIENT
---------------------------------------------------------------
Read this twice. `conversation_summary` returns counts, rates and latency
percentiles: numbers, deliberately. CLAUDE.md's hardest and oldest rule is that
NO NUMBER REACHES A CLIENT -- not a score, not a percentage, not a rank, in the
UI, in an API response, or in an email. This dict is exactly the thing someone
would be tempted to render on an ops page that a hiring manager can also open,
or to return from a debug route that later gets exposed.

It is for logs and for a human reading logs. It must never be returned from an
API route, serialised into a response schema, or rendered anywhere a client
can see. If a client-facing surface ever needs to say something about interview
quality, it says it in WORDS, through `services/rating.py`, from a fresh
function -- not by dressing up this dict.

WHAT IS LOGGED, AND WHAT IS NEVER LOGGED
-----------------------------------------
Logged: conversation id, turn index, question KEY, domain, the answer's
classification LABEL, the action taken, two booleans and a duration.

NEVER logged, under any setting: answer text, question text, candidate name,
candidate email. `tracing.py` already establishes that prompt and completion
TEXT does not leave the process without an explicit opt-in, because a prompt
carries a real candidate's answers and a real JD. This module holds the same
line and holds it harder: an ordinary application log is read by far more people
than a LangSmith project, and there is no flag here to loosen it because there
is no operational question that needs the text to answer it.

IT MUST NEVER RAISE
-------------------
Every public function swallows its own failures. Telemetry that can break the
request it observes is strictly worse than no telemetry, and the request being
observed here is a candidate part-way through an assessment they cannot easily
restart. A bad field, a None where a dataclass was expected, a logging handler
that itself throws: all of it degrades to a silent no-op.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Iterable

logger = logging.getLogger(__name__)

__all__ = [
    "TurnEvent",
    "record_turn",
    "conversation_summary",
    "emit_summary",
]

#: Longest a single logged value may be. A label, a key and a domain are all
#: short by construction, so anything longer is a caller mistake -- most
#: plausibly answer text arriving where a label was expected. Truncating bounds
#: the blast radius of that mistake to 64 characters instead of leaking a
#: paragraph of a candidate's answer into Cloud Logging.
_MAX_VALUE_CHARS = 64

#: Actions that mean the interviewer DEPARTED from the scripted next question.
#: The adaptivity rate is the share of turns that did one of these, and it is
#: the number that answers "was this an interview or a form".
_ADAPTIVE_ACTIONS = frozenset({"followed_up", "rechallenged"})


@dataclass(frozen=True)
class TurnEvent:
    """One turn of one conversation, as observed. Carries no candidate text.

    Frozen because a recorded observation is a fact about something that has
    already happened; a caller mutating one after the fact would be recording a
    different turn than the one that occurred.
    """

    conversation_id: str
    turn_index: int
    question_key: str
    domain: str          # "technical" | "ppi"
    answer_label: str    # from answer_classification.LABELS
    action: str          # "advanced" | "followed_up" | "rechallenged"
    generated: bool      # True when written this turn, False when stored text
    degraded: bool       # True when an LLM step failed and a fallback was used
    latency_ms: int


def _scrub(value: Any) -> str:
    """One log-safe token: no whitespace, bounded length, never None.

    Whitespace is collapsed because the line is `key=value` and a value with a
    space in it silently breaks every query written against it -- the field
    after it reads as part of this one.
    """
    text = "unknown" if value is None else str(value)
    text = "_".join(text.split()) or "unknown"
    if len(text) > _MAX_VALUE_CHARS:
        text = text[:_MAX_VALUE_CHARS]
    return text


def record_turn(event: TurnEvent) -> None:
    """Write ONE structured line for one turn, at INFO.

    INFO and not DEBUG: production runs at INFO, and a telemetry line that the
    deployed log level drops is not telemetry. That is the same reasoning that
    moved `llm_router.attempt`'s failure line up to WARNING -- a signal nobody
    can see is indistinguishable from no signal.
    """
    try:
        logger.info(
            "interview_telemetry.turn conversation_id=%s turn_index=%s "
            "question_key=%s domain=%s answer_label=%s action=%s "
            "generated=%s degraded=%s latency_ms=%s",
            _scrub(getattr(event, "conversation_id", None)),
            _scrub(getattr(event, "turn_index", None)),
            _scrub(getattr(event, "question_key", None)),
            _scrub(getattr(event, "domain", None)),
            _scrub(getattr(event, "answer_label", None)),
            _scrub(getattr(event, "action", None)),
            _scrub(getattr(event, "generated", None)),
            _scrub(getattr(event, "degraded", None)),
            _scrub(getattr(event, "latency_ms", None)),
        )
    except Exception:  # noqa: BLE001
        # A candidate is mid-assessment on a live request. Losing one telemetry
        # line is free; raising out of an observer is not.
        pass


def _empty_summary() -> dict[str, Any]:
    """The shape returned when there is nothing to summarise.

    Rates are None rather than 0.0 on purpose. 0.0 is a claim -- "no turn
    adapted" -- and a conversation with no turns has made no such claim. A
    reader who sees 0.0 across a dashboard cannot tell a scripted interview from
    an interview that never started.
    """
    return {
        "total_turns": 0,
        "answer_labels": {},
        "actions": {},
        "adaptivity_rate": None,
        "generation_rate": None,
        "degradation_rate": None,
        "latency_p50_ms": None,
        "latency_p95_ms": None,
    }


def _percentile(sorted_values: list[int], fraction: float) -> int | None:
    """Nearest-rank percentile over an already sorted list.

    Nearest-rank, not interpolation: every value here is an observed latency of
    a turn that really happened, and an interpolated p95 is a number no turn
    took. When the operator question is "how slow was the slow tail", the honest
    answer is a measurement, not an average of two of them.
    """
    if not sorted_values:
        return None
    rank = math.ceil(fraction * len(sorted_values))
    index = min(max(rank, 1), len(sorted_values)) - 1
    return sorted_values[index]


def conversation_summary(events: list[TurnEvent]) -> dict:
    """Aggregate a conversation's turns into operator counters.

    OPERATOR DATA ONLY. The returned dict contains counts, rates and latency
    percentiles. It must NEVER be returned from an API route, placed in a
    response schema, or rendered to a client in any portal -- see the module
    docstring. CLAUDE.md: no number reaches a client, ever.

    Tolerant of a malformed list by design: a single unusable entry is skipped
    rather than discarding the whole conversation's counters, because the reason
    someone is reading this at all is usually that something went wrong.
    """
    try:
        return _summarise(events)
    except Exception:  # noqa: BLE001
        # Never raise: a caller reaching for a summary is by definition already
        # in the reporting path, not the product path, and a broken summary must
        # not become a broken request.
        return _empty_summary()


def _summarise(events: Iterable[Any]) -> dict[str, Any]:
    total = 0
    answer_labels: dict[str, int] = {}
    actions: dict[str, int] = {}
    adaptive = 0
    generated = 0
    degraded = 0
    latencies: list[int] = []

    for event in events or []:
        label = getattr(event, "answer_label", None)
        action = getattr(event, "action", None)
        if label is None and action is None:
            # Not a turn observation at all (a None, a string, a stray dict).
            # Counting it would corrupt every rate below it.
            continue
        total += 1

        label_key = _scrub(label)
        answer_labels[label_key] = answer_labels.get(label_key, 0) + 1
        action_key = _scrub(action)
        actions[action_key] = actions.get(action_key, 0) + 1

        if action_key in _ADAPTIVE_ACTIONS:
            adaptive += 1
        if bool(getattr(event, "generated", False)):
            generated += 1
        if bool(getattr(event, "degraded", False)):
            degraded += 1

        latency = getattr(event, "latency_ms", None)
        try:
            latencies.append(int(latency))
        except (TypeError, ValueError):
            # A turn with an unreadable duration still counts as a turn. It just
            # contributes nothing to the percentiles, which is better than
            # dropping the turn from every other counter to save one field.
            pass

    if not total:
        return _empty_summary()

    latencies.sort()
    return {
        "total_turns": total,
        "answer_labels": answer_labels,
        "actions": actions,
        "adaptivity_rate": round(adaptive / total, 3),
        "generation_rate": round(generated / total, 3),
        "degradation_rate": round(degraded / total, 3),
        "latency_p50_ms": _percentile(latencies, 0.50),
        "latency_p95_ms": _percentile(latencies, 0.95),
    }


def _render_counts(counts: dict[str, int]) -> str:
    """Fold a count map into one whitespace-free token, e.g. `a:2|b:1`.

    Kept inside the single log line rather than emitted as one line per label:
    the summary is one event, and splitting it across lines means a reader has
    to reassemble it by conversation id in a log viewer that may have
    interleaved another conversation between them.
    """
    if not counts:
        return "none"
    return "|".join(f"{key}:{value}" for key, value in sorted(counts.items()))


def emit_summary(conversation_id: str, events: list[TurnEvent]) -> None:
    """Log a conversation's summary once, when the conversation ends.

    Same rule as `conversation_summary`: this goes to the LOG. It is not a
    payload, and nothing in this function's output may be forwarded to a client.
    """
    try:
        summary = conversation_summary(events)
        logger.info(
            "interview_telemetry.conversation conversation_id=%s total_turns=%s "
            "answer_labels=%s actions=%s adaptivity_rate=%s generation_rate=%s "
            "degradation_rate=%s latency_p50_ms=%s latency_p95_ms=%s",
            _scrub(conversation_id),
            summary["total_turns"],
            _render_counts(summary["answer_labels"]),
            _render_counts(summary["actions"]),
            summary["adaptivity_rate"],
            summary["generation_rate"],
            summary["degradation_rate"],
            summary["latency_p50_ms"],
            summary["latency_p95_ms"],
        )
    except Exception:  # noqa: BLE001
        # Emitted at the END of a conversation, which is also where completion
        # and billing are settled. Nothing here may interrupt that.
        pass
