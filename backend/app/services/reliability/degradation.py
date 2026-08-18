"""Three levels of answer, and the honest record of which one was given.

FULL, DEGRADED, STUB
--------------------
  full      every stage ran, the verifier passed. Confidence 0.9.
  degraded  reflection and replanning were skipped, or the verifier passed with
            findings. The output is real and was produced by a shorter path.
  stub      nothing generative survived. A caller-supplied fallback, flagged for
            human review, never presented as an assessment.

This is the shape `agent_loop` already has -- a value plus `degraded=True` --
generalised to a task made of several loops, where "some of it worked" is a real
and common state that a boolean cannot express.

WHY A STUB IS RETURNED AT ALL
------------------------------
Because the alternative is a 500 on a path where a candidate is mid-assessment
or a recruiter is opening a report. The product's standing answer to a provider
outage is its previous behaviour, not an error page. What makes that safe rather
than dishonest is `needs_human_review`: a stub is never silently indistinguishable
from a real result, and anything consuming one is expected to say so.

CONFIDENCE IS ASSIGNED, NOT MEASURED
-------------------------------------
0.9 / 0.5 / 0.1 are labels for three known situations, not estimates of
correctness. They exist so a caller can apply one threshold consistently. Where
a real computed confidence exists -- `verification.Verdict.confidence` -- it
overrides these, because that one is arithmetic over actual findings.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Generic, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

LEVEL_FULL = "full"
LEVEL_DEGRADED = "degraded"
LEVEL_STUB = "stub"

_DEFAULT_CONFIDENCE = {LEVEL_FULL: 0.9, LEVEL_DEGRADED: 0.5, LEVEL_STUB: 0.1}


@dataclass
class Outcome(Generic[T]):
    """A value, how it was produced, and whether anybody must look at it."""

    value: T
    level: str = LEVEL_FULL
    confidence: float = 0.9
    reasons: tuple[str, ...] = ()
    #: True for a stub, and for anything a verifier passed only marginally.
    needs_human_review: bool = False
    stages_skipped: tuple[str, ...] = field(default=())

    @property
    def degraded(self) -> bool:
        return self.level != LEVEL_FULL

    def as_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "confidence": self.confidence,
            "reasons": list(self.reasons),
            "needs_human_review": self.needs_human_review,
            "stages_skipped": list(self.stages_skipped),
        }


def full(value: T, *, confidence: float | None = None) -> Outcome[T]:
    return Outcome(
        value=value,
        level=LEVEL_FULL,
        confidence=confidence if confidence is not None else _DEFAULT_CONFIDENCE[LEVEL_FULL],
    )


def degraded(
    value: T,
    *,
    reasons: tuple[str, ...] = (),
    skipped: tuple[str, ...] = (),
    confidence: float | None = None,
) -> Outcome[T]:
    return Outcome(
        value=value,
        level=LEVEL_DEGRADED,
        confidence=confidence if confidence is not None else _DEFAULT_CONFIDENCE[LEVEL_DEGRADED],
        reasons=reasons,
        stages_skipped=skipped,
        needs_human_review=False,
    )


def stub(value: T, *, reasons: tuple[str, ...] = ()) -> Outcome[T]:
    return Outcome(
        value=value,
        level=LEVEL_STUB,
        confidence=_DEFAULT_CONFIDENCE[LEVEL_STUB],
        reasons=reasons,
        needs_human_review=True,
    )


async def with_fallbacks(
    *,
    full_path: Callable[[], Awaitable[T]],
    degraded_path: Callable[[], Awaitable[T]] | None = None,
    fallback: T,
    label: str = "task",
) -> Outcome[T]:
    """Try full, then degraded, then the caller's fallback. Never raises.

    The caller supplies the fallback rather than this module inventing one: what
    a usable minimum looks like is a product question with a different answer for
    a report, an email and an interview turn, and a generic "empty" would be
    rendered to somebody.
    """
    try:
        return full(await full_path())
    except Exception as exc:  # noqa: BLE001 -- classified into a level, never swallowed silently
        first = f"{type(exc).__name__}: {exc}"
        logger.warning("degradation.full_path_failed label=%s err=%s", label, type(exc).__name__)

    if degraded_path is not None:
        try:
            return degraded(
                await degraded_path(),
                reasons=(first,),
                skipped=("reflect", "replan"),
            )
        except Exception as exc:  # noqa: BLE001
            second = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "degradation.degraded_path_failed label=%s err=%s", label, type(exc).__name__
            )
            return stub(fallback, reasons=(first, second))

    return stub(fallback, reasons=(first,))
