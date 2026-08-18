"""The phrases that mean a remark was written about nobody in particular.

WHY THIS IS A LIST AND NOT A MODEL
----------------------------------
"Is this prose specific to the candidate?" is a judgement, and asking a model
for it costs a call, fails during an outage, and cannot be regression-tested.
"Does this prose contain a phrase that appears in every resume ever written?"
is a lookup, and it catches most of the same outputs. The second question is
the one worth automating; the first is what the evaluation dataset and a human
reviewer are for.

WHY IT DOES NOT USE `agent_loop.banned_phrase_gate`
---------------------------------------------------
The obvious move is to reuse the shared gate, and the first version of this
module did. Its close-variant window allowed a window SHORTER than the banned
phrase to match by containment, with no floor on how short: at two words the
narrowest window was one word, so "we" matched "well rounded" and every
ordinary sentence in the product tripped the detector. Measured, not theorised:
it fired on "We would like to move ahead" and on a Kafka probe.

That defect is FIXED as of 2026-08-18 -- `_MIN_PARTIAL_MATCH_WORDS` now floors
the partial direction at three words -- so the shared gate is no longer unsafe
for a list like this one. This module still does not use it, for a different
and smaller reason: the gate also matches on a character-similarity ratio, and
against two-word targets that is a judgement call nobody has calibrated. Exact
contiguous word-sequence containment is predictable, and predictability is what
a detector needs when the cost of a false positive is that its callers learn to
ignore it.

So: word-normalised, exact contiguous containment. Casing, punctuation and
spacing still cannot evade it, and "a strong communicator" still contains
"strong communicator". What it will not do is guess.

CALIBRATION MATTERS MORE THAN COVERAGE
--------------------------------------
Every phrase here is one that carries no information about a specific person.
That is the only test for membership. Deliberately absent: anything a real,
specific remark might legitimately need. "Led a team of engineers" is not on
this list and must not be added to it. A detector that fires on genuine content
teaches its callers to ignore it, which is worse than not having one.
"""
from __future__ import annotations

import re
from typing import Sequence

from app.services.verification import base

#: Filler that survives being moved between two unrelated candidates unchanged.
GENERIC_PHRASES: tuple[str, ...] = (
    "strong communicator",
    "excellent communication skills",
    "team player",
    "self motivated",
    "self starter",
    "quick learner",
    "fast learner",
    "detail oriented",
    "passionate about",
    "proven ability",
    "proven track record",
    "excellent problem solving",
    "strong problem solving skills",
    "results driven",
    "goes above and beyond",
    "wears many hats",
    "hit the ground running",
    "think outside the box",
    "strong work ethic",
    "highly motivated",
    "dynamic professional",
    "seasoned professional",
    "valuable asset",
    "well rounded",
    "good cultural fit",
)

_WORD = re.compile(r"[a-z0-9]+")


def _normalise(text: str) -> str:
    """Words only, lowercased, single-spaced, padded so a match is word-aligned.

    The padding is what makes containment safe: without it, "team player" would
    match inside "downstream player", and a detector with that failure mode is
    one nobody will keep switched on.
    """
    words = _WORD.findall(str(text or "").casefold())
    return f" {' '.join(words)} " if words else " "


def matched_phrases(
    text: str, phrases: Sequence[str] = GENERIC_PHRASES
) -> tuple[str, ...]:
    """Every listed phrase this text actually contains, in list order."""
    haystack = _normalise(text)
    return tuple(
        phrase
        for phrase in phrases
        if (needle := _normalise(phrase)).strip() and needle in haystack
    )


def findings(
    text: str,
    *,
    location: str,
    phrases: Sequence[str] = GENERIC_PHRASES,
    severity: str = base.SEVERITY_MEDIUM,
) -> list[base.Finding]:
    """Generic-language findings for one piece of generated prose.

    Medium by default rather than high: one filler phrase in an otherwise
    specific 50-word remark is worth fixing and is not worth discarding the
    remark over. A caller that knows better, such as a report section whose
    entire value is specificity, raises the severity itself.
    """
    return [
        base.Finding(
            severity=severity,
            issue="generic_language",
            location=location,
            detail=f"the text contains the filler phrase {phrase!r}",
            recommendation=(
                "replace filler with something that could only be written "
                "about this person: name the system, the decision or the "
                "number of people involved"
            ),
        )
        for phrase in matched_phrases(text, phrases)
    ]


def rate(texts: Sequence[str], phrases: Sequence[str] = GENERIC_PHRASES) -> float:
    """Share of `texts` containing at least one generic phrase, 0.0 to 1.0.

    The metric the evaluation dataset tracks over time. Reported per output set
    rather than per phrase: two filler phrases in one remark is one bad remark,
    and counting occurrences would make a single verbose failure look like two.
    """
    if not texts:
        return 0.0
    hits = sum(1 for text in texts if matched_phrases(text, phrases))
    return round(hits / len(texts), 4)
