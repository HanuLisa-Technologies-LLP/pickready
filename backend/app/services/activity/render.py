"""Turning one structured event into one sentence. Ordinary code, no model.

PROVENANCE
----------
`ai-upgrade-spec-doc.md` "Case 3" sections 6, 18 and 19. Section 6 forbids an
architecture that asks a model for the next status message; section 19 sets the
order this module implements, top to bottom:

  1. the phrase the catalogue holds for THIS task and THIS event, with the
     workflow's real facts in it;
  2. the same phrase without the facts, when the workflow did not supply them;
  3. the task's own default sentence, when the catalogue has no entry;
  4. the generic sentence, which nothing in a registered workflow can reach.

Every step down is recorded on the line as `source`, and steps 3 and 4 log. A
fallback nobody can see is the same defect as no fallback at all, and this one
is visible in the payload the browser receives.

WHY A FACT IS VALIDATED RATHER THAN COERCED
-------------------------------------------
Section 18 forbids fabricated precision. The failure mode it is guarding is not
a workflow that lies; it is a workflow that passes `None`, or `0` for "not
counted yet", and a renderer that prints it anyway. So a count must be a real
non-negative `int` and a text fact must be short, non-empty and free of the
characters this product bans from copy. A fact that fails any of those does not
get cleaned up, defaulted or truncated: the sentence that would have used it is
not rendered, and the reader gets the true sentence that needs no facts.
"""
from __future__ import annotations

import logging
import re
from typing import Mapping

from app.services.activity import events as ev
from app.services.activity import phrasing

logger = logging.getLogger(__name__)

#: Built from code points rather than typed, so a repository-wide sweep for the
#: character cannot rewrite the line that rejects it. The same trick
#: `test_platform_audit` uses, and for the same reason.
_EM_DASH = chr(8212)
_EN_DASH = chr(8211)

#: A control character, a newline or a tab in a title would break the line
#: layout; a brace would be re-interpreted by the next `str.format`.
_UNSAFE_TEXT = re.compile(r"[\x00-\x1f\x7f{}]")


def _count_text(name: str, value: int) -> str:
    singular, plural = phrasing.COUNT_NOUNS[name]
    return f"{value} {singular if value == 1 else plural}"


def _usable_fact(name: str, value: object) -> str | None:
    """The spoken form of one fact, or None when it may not be spoken.

    None is the important return. It is what makes an absent, negative or
    malformed value drop the whole detailed sentence instead of appearing in it.
    """
    if name in phrasing.COUNT_NOUNS:
        # `bool` is an `int` in Python and would print as "True candidates".
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        return _count_text(name, value)
    if name in phrasing.TEXT_FACTS:
        if not isinstance(value, str):
            return None
        text = value.strip()
        if not text or len(text) > phrasing.MAX_TEXT_FACT_CHARS:
            return None
        if _EM_DASH in text or _EN_DASH in text or _UNSAFE_TEXT.search(text):
            return None
        return text
    # A fact nobody declared. Refused rather than interpolated: an undeclared
    # name has no agreed spoken form, so printing it would be this module
    # inventing one.
    return None


_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


def _fill(template: str, facts: Mapping[str, int | str]) -> str | None:
    """The template with every placeholder filled, or None if any cannot be."""
    names = _PLACEHOLDER.findall(template)
    if not names:
        return None
    spoken: dict[str, str] = {}
    for name in names:
        if name not in facts:
            return None
        usable = _usable_fact(name, facts[name])
        if usable is None:
            return None
        spoken[name] = usable
    return template.format(**spoken)


def render(event: ev.ActivityEvent, *, operation_id: str, sequence: int) -> ev.ActivityLine:
    """One event, one line. Pure: same event, same sentence, every time.

    Determinism is the point of doing this in code. A status that varies between
    two runs of identical work makes a real change indistinguishable from the
    provider having sampled differently, which is the argument
    `config/llm_providers` already makes about temperature.
    """
    phrase = phrasing.phrase_for(event.task, event.kind)
    if phrase is not None:
        text = _fill(phrase.detailed, event.facts) if phrase.detailed else None
        if text is not None:
            source = ev.SOURCE_EVENT
        else:
            text = phrase.plain
            source = ev.SOURCE_EVENT
    else:
        default = phrasing.TASK_DEFAULT.get(event.task)
        if default is not None:
            logger.warning(
                "activity.no_phrase task=%s kind=%s, using the task default",
                event.task,
                event.kind,
            )
            text = default
            source = ev.SOURCE_TASK_DEFAULT
        else:
            logger.warning(
                "activity.no_task_default task=%s kind=%s, using the generic line",
                event.task,
                event.kind,
            )
            text = phrasing.GENERIC
            source = ev.SOURCE_GENERIC

    return ev.ActivityLine(
        operation_id=operation_id,
        task=event.task,
        kind=event.kind,
        sequence=sequence,
        text=text,
        detail=event.note.strip(),
        source=source,
        terminal=event.terminal,
    )
