"""Retrieved content is DATA. Validating it before it reaches a prompt.

THE RULE THIS EXTENDS
---------------------
`conversation_guardrails.inspect_answer` already treats a candidate's typed
answer as data and never as instructions. Retrieval opens a second door to the
same risk: a resume is a file a candidate uploaded, a JD is text a client typed,
and both are now chunked, ranked and pasted into a prompt. An injection in a PDF
reaches the model by exactly the path an injection in a chat message does.

WHY IT REUSES THE EXISTING DETECTOR
------------------------------------
Because two injection detectors drift, and the one that drifts is the one that
gets less traffic. `inspect_answer` is deterministic, calls no model, and is
already the product's definition of what an instruction-shaped string looks
like. This module is the retrieval-side application of it, not a second opinion.

QUARANTINE, NOT REFUSAL
-----------------------
A chunk that looks like an injection is dropped from the retrieved set and
counted. Failing the whole retrieval would let one poisoned paragraph in one
resume disable assessment for that candidate, which is a denial of service with
extra steps. Dropping it costs that paragraph.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Sequence

from app.services import conversation_guardrails

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScreenResult:
    """What survived screening, and what did not."""

    kept: tuple
    quarantined: int = 0
    #: Violation labels seen, for telemetry. Never the offending text.
    violations: tuple[str, ...] = ()

    @property
    def clean(self) -> bool:
        return self.quarantined == 0


def screen_chunks(chunks: Sequence) -> ScreenResult:
    """Drop retrieved chunks whose content is instruction-shaped.

    Note the contract inherited from `inspect_answer`: a violation being present
    does NOT mean refused -- only `allowed` does. A resume that legitimately
    DISCUSSES prompt injection, which a security engineer's resume will, is
    still a resume.
    """
    kept, violations, quarantined = [], [], 0
    for chunk in chunks:
        content = getattr(chunk, "content", None)
        if content is None:
            kept.append(chunk)
            continue
        verdict = conversation_guardrails.inspect_answer(str(content))
        if verdict.allowed:
            kept.append(chunk)
            continue
        quarantined += 1
        if verdict.violation:
            violations.append(str(verdict.violation))
        logger.warning(
            "safety.chunk_quarantined source=%s section=%s violation=%s",
            getattr(chunk, "source_type", "?"),
            getattr(chunk, "section_type", "?"),
            verdict.violation,
        )
    return ScreenResult(
        kept=tuple(kept), quarantined=quarantined, violations=tuple(dict.fromkeys(violations))
    )


def screen_text(value: str) -> bool:
    """Whether one retrieved string is safe to place in a prompt."""
    return conversation_guardrails.inspect_answer(str(value or "")).allowed
