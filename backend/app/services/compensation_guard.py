"""The ONE implementation of "no compensation reaches a model".

Owner acceptance criterion 7 (Vivekium release, Appendix A): CTC never appears
in any LLM prompt, and a test asserts it across every prompt builder
(`tests/test_ctc_never_in_prompt.py`). This module is what a builder calls to
make that true. It has two halves because compensation reaches a prompt by two
different roads, and the first half alone never covered the second.

1. STRUCTURED DATA: `strip_keys` drops every dict key that looks like pay
   (`compensation_json`, a parsed `current_ctc`, a form's `expected_salary`).
   Moved here from `matching._strip_compensation`, which was the only copy and
   was applied at one call site.
2. FREE TEXT: `redact_text` drops the LINES of prose that state pay. Key
   stripping cannot see prose, and prose is where pay actually travels: a
   resume carries "Current CTC: 18 LPA", a pasted job description carries a
   "Compensation" section, a SWOT names a salary budget. Before this module
   every one of those reached Yukti's prompt verbatim (PLAN-p2 NF-4).

WHY WHOLE LINES, AND NEVER A WORD IN THE MIDDLE
-----------------------------------------------
Deleting "18 LPA" out of "Current CTC: 18 LPA" leaves "Current CTC:", which a
model reads as an invitation to guess, and editing a sentence in place leaves
prose that reads as broken. A missing line is invisible where a mangled one is
not, which is the rule `conversation_guardrails.inspect_agent_output` already
follows for sentences. A line longer than `_LONG_LINE_CHARS` is the exception
and is split into sentences first, because a resume extracted from a PDF can
arrive as one enormous line, and dropping that line would drop the whole
resume to remove one sentence of it.

WHAT IT COSTS, SAID OUT LOUD
----------------------------
The pattern is deliberately wide, so it removes some lines that are not about
anybody's pay: a payroll engineer's "built the salary computation engine", an
insurance analyst's "workers' compensation claims". That is the chosen
direction. A missed CTC line is a breach of a stated acceptance criterion that
nothing downstream can detect; a dropped work line costs one sentence of
evidence from a resume that almost always states the same skill elsewhere.
Tech vocabulary that merely LOOKS like pay is NOT matched, because losing it
would cost real evidence for no protection: "package" (an npm package),
"pay" on its own ("pay-as-you-go billing", "PayPal integration"), "gross"
("gross margin"). Only the pay-specific phrases of those words are listed.

No model is called and nothing here raises on odd input: a guard that failed
would be a prompt builder that failed, and the honest failure for a builder is
its own.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

__all__ = [
    "KEY_MARKERS",
    "Redaction",
    "mentions_compensation",
    "redact",
    "redact_text",
    "strip_keys",
]

#: A dict key containing any of these (case-insensitive) is dropped by
#: `strip_keys`. Substring matching on KEYS is safe in the over-strip direction
#: because a key is a name somebody chose for a field, not prose: `ctc_min`,
#: `current_ctc`, `expected_salary`, `compensation_json`, `gross_pay`,
#: `lpa_range`, `salary_package`. The prose list below is narrower on purpose.
KEY_MARKERS: tuple[str, ...] = (
    "ctc",
    "salary",
    "compensation",
    "gross",
    "pay",
    "remuneration",
    "lpa",
    "package",
    "stipend",
)

#: The pay vocabulary in PROSE, matched on word boundaries. Each entry is a
#: phrase that names somebody's pay, never a word that merely can.
_PAY_PHRASES = re.compile(
    r"(?<![a-z])(?:"
    r"ctc"
    r"|salar(?:y|ies)"
    r"|compensation"
    r"|remuneration"
    r"|stipend"
    r"|emoluments?"
    r"|lpa"
    r"|per\s+annum|p\.\s?a\."
    r"|pay\s?slips?|pay\s?stubs?|pay\s?scales?|pay\s?bands?"
    r"|take[\s-]?home\s+pay"
    r"|(?:gross|net|basic|fixed|variable|monthly|annual|current|expected|drawn)\s+pay"
    r")(?![a-z])",
    re.IGNORECASE,
)

#: An AMOUNT that is pay-shaped whatever words surround it:
#:   * a rupee marker next to a number ("Rs. 4,00,000", "INR 18", "\u20b9 12");
#:   * a number stated in LPA ("18 LPA", "12.5LPA");
#:   * Indian digit grouping ("73,19,000"), which is how every CTC on the
#:     application form's own worked example is written.
#: A bare "lakh" or "crore" is NOT here: Indian resumes state VOLUMES that way
#: ("processed 5 lakh transactions a day") and that is evidence, not pay. Pay
#: stated in lakhs carries "per annum", "LPA" or "CTC", which the phrase list
#: above already catches.
_PAY_AMOUNT = re.compile(
    r"(?:"
    r"(?:\brs\.?|\binr\b|\u20b9)\s?\d"
    r"|\d[\d,.]*\s?lpa\b"
    r"|\b\d{1,2},\d{2},\d{3}\b"
    r")",
    re.IGNORECASE,
)

#: A line longer than this is split into sentences before redaction, so one
#: offending sentence in a resume that was extracted as a single line does not
#: take the whole resume with it.
_LONG_LINE_CHARS = 300

#: Sentence and bullet boundaries inside an over-long line.
_SEGMENT_BREAK = re.compile(r"(?<=[.;!?|\u2022\u25cf\u00b7])\s+")


def strip_keys(value: Any) -> Any:
    """Recursively drop every dict key that looks like compensation data.

    Lists are walked, scalars are returned unchanged, and nothing is ever
    added: the output is always a subset of the input.
    """
    if isinstance(value, dict):
        return {
            key: strip_keys(item)
            for key, item in value.items()
            if not any(marker in str(key).lower() for marker in KEY_MARKERS)
        }
    if isinstance(value, list):
        return [strip_keys(item) for item in value]
    if isinstance(value, tuple):
        return tuple(strip_keys(item) for item in value)
    return value


def mentions_compensation(text: str | None) -> bool:
    """True when `text` states or names somebody's pay (either pattern)."""
    if not text:
        return False
    return bool(_PAY_PHRASES.search(text) or _PAY_AMOUNT.search(text))


@dataclass(frozen=True)
class Redaction:
    """The redacted text, and whether anything was removed from it.

    `removed` is a BOOLEAN on purpose. It is recorded in provenance so a reader
    can tell a resume that never mentioned pay from one that had lines taken
    out, and a count would put a number into a record that is otherwise words.
    """

    text: str
    removed: bool


def _redact_line(line: str) -> tuple[str | None, bool]:
    """(kept text or None, whether anything was removed) for ONE line."""
    if not mentions_compensation(line):
        return line, False
    if len(line) <= _LONG_LINE_CHARS:
        return None, True
    kept = [
        segment
        for segment in _SEGMENT_BREAK.split(line)
        if segment.strip() and not mentions_compensation(segment)
    ]
    return (" ".join(kept) if kept else None), True


def redact(text: str | None) -> Redaction:
    """Drop every line (or, in an over-long line, every sentence) stating pay.

    Line structure is preserved for everything kept, because Yukti cuts a
    resume on a line boundary and grounds a quote against the same lines.
    """
    if not text:
        return Redaction("", False)
    kept: list[str] = []
    removed = False
    for line in str(text).splitlines():
        survivor, dropped = _redact_line(line)
        removed = removed or dropped
        if survivor is not None:
            kept.append(survivor)
    return Redaction("\n".join(kept), removed)


def redact_text(text: str | None) -> str:
    """`redact(text).text`, for a caller that needs only the text."""
    return redact(text).text
