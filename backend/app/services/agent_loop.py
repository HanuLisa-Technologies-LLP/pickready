"""The bounded agent loop every generative task in the product runs inside.

WHAT THIS REPLACES
------------------
One-shot prompting. Ask a model once, parse the reply, and if it is unusable
throw it away and fall back to a deterministic template. That shape is why the
product's AI output was bimodal: either the first attempt happened to satisfy
every constraint, or the candidate/recruiter got a canned string. Nothing ever
told the model WHAT it got wrong and asked again, even when the defect was one
the prompt could obviously have fixed ("you returned 4 items, I asked for 5").

THE LOOP
--------
    plan -> execute -> evaluate -> (reflect -> improve)* -> verify

  plan      Build the request. Pure, no I/O, no model call. Runs once.
  execute   The model call. May raise; a raised attempt is a failed attempt,
            not a failed loop.
  evaluate  DETERMINISTIC success criteria over the parsed output. Returns a
            `Critique` -- ok, plus the specific reasons it is not ok. This is
            the part that makes the loop worth having: the criteria are code,
            so they are testable offline and cannot be argued with by a model.
  reflect   Turn those reasons into an instruction the next attempt can act on.
            No model call: reflection here is mechanical, because a model asked
            to critique its own output is one more thing that can fail.
  improve   Re-execute WITH the reflection appended. Bounded by `max_attempts`.
  verify    The final gate, applied to whatever survived. Identical criteria to
            `evaluate` by default; a separate `verify` is for the stricter
            check you only want to pay for once.

WHY EVALUATION IS DETERMINISTIC AND NOT AN LLM JUDGE
----------------------------------------------------
Same reason `answer_classification` settles empty and gibberish without a model
call: the moment you need the guard most is the moment the provider is down. An
LLM-judge evaluator turns one flaky dependency into two, and it makes the
success criteria unfalsifiable -- you can no longer write a test that says "this
output is rejected", only one that says "the judge usually rejects it".

EVERY LOOP DEGRADES, NONE OF THEM RAISE
---------------------------------------
`run_loop` never propagates an exception. When no attempt satisfies the
criteria it returns `fallback` with `degraded=True` and the reasons attached.
Callers are on live request paths with a candidate or a recruiter waiting, and
the correct answer to a provider outage is the product's previous behaviour, not
a 500. `LoopResult.degraded` is the honest record that it happened, and it is
what telemetry counts -- a degradation nobody counts is a degradation nobody
notices, which is the failure mode this codebase has been bitten by repeatedly.

BOUNDS ARE STRUCTURAL, NOT ADVISORY
-----------------------------------
Two independent ceilings, and both are needed. `max_attempts` bounds the number
of model calls; `deadline_seconds` bounds the wall clock, because N attempts at
the per-task timeout is a multiple of what the user experiences. An interactive
loop (a candidate watching a text box) gets 2 attempts and a short deadline; a
background loop (a Celery task nobody is watching) can afford more. The defaults
below are the interactive ones, because that is the dangerous direction: a
background task that retries too little is slow, an interactive one that retries
too much is a timeout.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Generic, Sequence, TypeVar

logger = logging.getLogger(__name__)

__all__ = [
    "Critique",
    "LoopResult",
    "INTERACTIVE_ATTEMPTS",
    "INTERACTIVE_DEADLINE",
    "BACKGROUND_ATTEMPTS",
    "BACKGROUND_DEADLINE",
    "ok",
    "reject",
    "run_loop",
    "reflection_text",
]

T = TypeVar("T")

#: A candidate or a recruiter is blocked on the request. Two attempts, because
#: the second is the one that acts on the reflection and a third would push the
#: turn past what anyone will sit through. The deadline is the real bound: it is
#: checked BEFORE each attempt, so the loop can decline to start an attempt it
#: has no time to finish rather than starting one that overruns.
INTERACTIVE_ATTEMPTS = 2
INTERACTIVE_DEADLINE = 26.0

#: Nobody is watching. Worth more attempts, because the alternative here is a
#: deterministic template landing in a report a client reads.
BACKGROUND_ATTEMPTS = 3
BACKGROUND_DEADLINE = 240.0


@dataclass(frozen=True)
class Critique:
    """The verdict on one attempt, and WHY.

    `reasons` are written to be read by the next attempt, so they are phrased as
    instructions rather than as complaints: "return exactly 5 items" beats "the
    list was too short". They are also what `LoopResult` carries into telemetry,
    which is how a criterion that rejects everything becomes visible instead of
    just looking like a provider outage.
    """

    ok: bool
    reasons: tuple[str, ...] = ()

    def __bool__(self) -> bool:  # `if critique:` reads naturally
        return self.ok


def ok() -> Critique:
    return Critique(True, ())


def reject(*reasons: str) -> Critique:
    """A failed critique. Empty reasons are dropped rather than rejected: a
    caller that computes its reasons should not be able to produce a failure
    that says nothing, but neither should it have to filter its own list."""
    cleaned = tuple(reason.strip() for reason in reasons if reason and reason.strip())
    return Critique(False, cleaned or ("the output did not meet the criteria",))


@dataclass
class LoopResult(Generic[T]):
    """The structured handoff out of a loop.

    Deliberately not a bare value. Every caller needs to know whether what it is
    holding was written by a model or fell out of the fallback, because that
    decides whether it stamps a `generated_at`, what it logs, and in one case
    (`functional_assessment`) what `scoring_mode` the report carries. Returning
    only the value is how a degradation becomes invisible.
    """

    value: T
    #: True when no attempt satisfied the criteria and `value` is the fallback.
    degraded: bool = False
    #: Model calls actually made. 0 means the loop never got to try (no time, or
    #: `plan` refused), which is a different operational story from 2 rejections.
    attempts: int = 0
    #: Why the last attempt was rejected, for telemetry. Never shown to a user.
    reasons: tuple[str, ...] = ()
    #: Wall clock spent, milliseconds.
    elapsed_ms: int = 0
    #: Set when an attempt raised rather than merely failing the criteria. The
    #: two look identical to a caller and are very different to an operator.
    error: str | None = field(default=None)


def reflection_text(reasons: Sequence[str]) -> str:
    """Turn a critique into an instruction the next attempt can act on.

    Mechanical on purpose. Asking a model to reflect on its own output is
    another call that can fail, and it makes the loop's behaviour depend on a
    provider being healthy at exactly the moment it has already proved it is
    not.
    """
    if not reasons:
        return ""
    bullets = "\n".join(f"- {reason}" for reason in reasons)
    return (
        "Your previous attempt was rejected. Fix exactly these problems and "
        "return the corrected result in the same JSON shape:\n"
        f"{bullets}"
    )


async def run_loop(
    *,
    name: str,
    execute: Callable[[str], Awaitable[T]],
    evaluate: Callable[[T], Critique],
    fallback: T,
    verify: Callable[[T], Critique] | None = None,
    max_attempts: int = INTERACTIVE_ATTEMPTS,
    deadline_seconds: float = INTERACTIVE_DEADLINE,
) -> LoopResult[T]:
    """Run `execute` until `evaluate` accepts it, bounded twice over.

    `execute` receives the reflection for the previous rejection -- an empty
    string on the first attempt -- and is expected to fold it into its prompt.
    It returns a parsed value or raises; both are handled, and neither ends the
    loop early while attempts and time remain.

    `verify` runs ONCE on the accepted value. Use it for a check too expensive
    to run on every attempt, or one that is only meaningful on a final answer. A
    value that passes `evaluate` and fails `verify` is a degradation: the
    fallback is returned, because a value that failed the final gate is exactly
    what the gate exists to keep out.

    Returns a `LoopResult`. Never raises.
    """
    started = time.monotonic()
    reflection = ""
    reasons: tuple[str, ...] = ()
    attempts = 0
    error: str | None = None

    def _elapsed_ms() -> int:
        return int((time.monotonic() - started) * 1000)

    # The longest attempt seen so far, and the best available estimate of what
    # the NEXT one will cost. See the deadline check below.
    longest_attempt = 0.0

    for attempt in range(1, max(1, max_attempts) + 1):
        # THE DEADLINE HAS TO PREDICT, NOT JUST OBSERVE.
        #
        # This originally read `elapsed >= deadline_seconds`, which sounds
        # right and is not. Measured: one `conversation_turn` call is bounded by
        # the router at 24s and the interactive deadline is 26s, so after a slow
        # first attempt elapsed is 24, `24 >= 26` is False, attempt two starts,
        # and the true worst case is 48 seconds -- with a candidate watching a
        # text box. The loop would have been advertising a bound it did not have.
        #
        # Refusing to START an attempt there is not enough either; the question
        # is whether the attempt can FINISH inside the budget. So the check is
        # against elapsed PLUS what an attempt has actually been costing. A fast
        # first attempt (300ms) still permits a second, which is the case the
        # retry exists for; a slow one does not, which is the case the deadline
        # exists for. Self-tuning, and it needs no per-task configuration.
        elapsed = time.monotonic() - started
        if elapsed + longest_attempt >= deadline_seconds:
            reasons = reasons or ("the loop ran out of time before this attempt",)
            logger.info(
                "agent_loop.deadline name=%s attempts=%d elapsed_ms=%d "
                "longest_attempt_ms=%d",
                name, attempts, _elapsed_ms(), int(longest_attempt * 1000),
            )
            break

        attempts = attempt
        attempt_started = time.monotonic()
        try:
            candidate = await execute(reflection)
        except Exception as exc:  # noqa: BLE001
            # Deliberately broad, and identical in effect to a failed critique.
            # A provider outage, a timeout, a malformed JSON body and a response
            # that was not JSON at all all mean the same thing here: this
            # attempt produced nothing usable. Logged at info because a
            # degradation is expected operation, not an incident.
            #
            # The duration counts even though the attempt failed, and especially
            # then: a TIMEOUT is the slowest and most informative thing that can
            # happen, and ignoring it would let the loop keep starting attempts
            # it has no time for.
            longest_attempt = max(longest_attempt, time.monotonic() - attempt_started)
            error = type(exc).__name__
            reasons = (f"the previous attempt failed with {error}",)
            logger.info(
                "agent_loop.attempt_error name=%s attempt=%d error=%s",
                name, attempt, error,
            )
            reflection = ""  # nothing to reflect on; just try again
            continue

        longest_attempt = max(longest_attempt, time.monotonic() - attempt_started)
        critique = evaluate(candidate)
        if critique.ok:
            if verify is not None:
                final = verify(candidate)
                if not final.ok:
                    logger.info(
                        "agent_loop.verify_rejected name=%s attempt=%d reasons=%s",
                        name, attempt, list(final.reasons),
                    )
                    return LoopResult(
                        value=fallback,
                        degraded=True,
                        attempts=attempts,
                        reasons=final.reasons,
                        elapsed_ms=_elapsed_ms(),
                        error=error,
                    )
            return LoopResult(
                value=candidate,
                degraded=False,
                attempts=attempts,
                elapsed_ms=_elapsed_ms(),
                error=error,
            )

        reasons = critique.reasons
        reflection = reflection_text(reasons)
        logger.info(
            "agent_loop.rejected name=%s attempt=%d reasons=%s",
            name, attempt, list(reasons),
        )

    logger.info(
        "agent_loop.degraded name=%s attempts=%d elapsed_ms=%d reasons=%s",
        name, attempts, _elapsed_ms(), list(reasons),
    )
    return LoopResult(
        value=fallback,
        degraded=True,
        attempts=attempts,
        reasons=reasons,
        elapsed_ms=_elapsed_ms(),
        error=error,
    )


# ── Criteria worth sharing ───────────────────────────────────────────────────
# Small, deterministic, and reused by more than one loop. Anything task-specific
# belongs beside its own loop, not here -- a shared criteria module that grows
# task-specific rules is how prompt logic ends up somewhere nobody looks.


def require_keys(payload: Any, *keys: str) -> Critique:
    """`payload` is a dict carrying every named key with a non-empty value."""
    if not isinstance(payload, dict):
        return reject("return a JSON object")
    missing = [key for key in keys if not str(payload.get(key) or "").strip()]
    if missing:
        return reject(
            "include a non-empty value for every one of these fields: "
            + ", ".join(missing)
        )
    return ok()


def require_length(text: str, *, maximum: int, what: str = "the text") -> Critique:
    """Bound the length of a generated string.

    Rejecting rather than truncating is the standing rule in this codebase: a
    truncated question loses its question mark and a truncated remark loses its
    verb, and both reach a person looking like a bug rather than like a limit.
    """
    if len(text) > maximum:
        return reject(
            f"keep {what} under {maximum} characters; the previous attempt was "
            f"{len(text)}"
        )
    return ok()
