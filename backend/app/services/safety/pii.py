"""Masking personal data before it reaches a log, a trace or a debug dump.

WHY THIS IS NEEDED WHEN TELEMETRY ALREADY EXCLUDES CONTENT
------------------------------------------------------------
Because exclusion is a rule people follow and masking is a function that runs.
Every telemetry path in this framework is written to carry identifiers only, and
that will hold until somebody adds one field to one log line during an incident
at two in the morning. This is the net under that.

WHAT IT MASKS, AND WHY EACH PATTERN IS SHAPED THE WAY IT IS
-------------------------------------------------------------
An email keeps its first two characters and its domain, because "was this the
right recipient" is a real debugging question and `***@***` cannot answer it. A
phone keeps its last four, which is how every system a person has ever phoned
identifies it back to them. An identity number keeps nothing useful at all,
because there is no debugging question worth the risk.

WHAT IT DELIBERATELY DOES NOT DO
---------------------------------
It does not try to find names. A name is not a pattern -- it is any word --
and a masker that guessed would either mangle ordinary prose or produce a false
sense of coverage. Names are kept out of logs by not logging content, which is
the rule this module is the net under, not a replacement for.
"""
from __future__ import annotations

import re
from typing import Any

_EMAIL = re.compile(r"\b([A-Za-z0-9._%+-]{1,2})[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
#: Indian mobile numbers with or without a country code, plus generic 10-15 digit
#: runs. Bounded on both sides so an ordinal or a year is not mistaken for one.
_PHONE = re.compile(r"(?<!\d)(\+?\d{1,3}[\s-]?)?(\d{6,12})(\d{4})(?!\d)")
_PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
_AADHAAR = re.compile(r"(?<!\d)\d{4}[\s-]?\d{4}[\s-]?\d{4}(?!\d)")
_SSN = re.compile(r"(?<!\d)\d{3}-\d{2}-(\d{4})(?!\d)")
_CARD = re.compile(r"(?<!\d)(?:\d{4}[\s-]?){3}(\d{4})(?!\d)")

MASK = "***"


def mask_text(value: str) -> str:
    """Mask every recognised identifier in one string.

    Card and identity numbers are masked BEFORE the phone pattern, because a
    16-digit card matches the generic long-number rule and would otherwise be
    masked as a phone -- leaving four digits in a different position than a
    reader would expect, which is worse than either outcome alone.
    """
    text = str(value or "")
    text = _CARD.sub(lambda m: f"{MASK}{m.group(1)}", text)
    text = _AADHAAR.sub(f"{MASK}", text)
    text = _SSN.sub(lambda m: f"***-**-{m.group(1)}", text)
    text = _PAN.sub(MASK, text)
    text = _EMAIL.sub(lambda m: f"{m.group(1)}{MASK}@{m.group(2)}", text)
    text = _PHONE.sub(lambda m: f"{MASK}{m.group(3)}", text)
    return text


def mask(value: Any, *, _depth: int = 0) -> Any:
    """Recursively mask strings inside dicts, lists and tuples.

    Depth-bounded: a cyclic or pathologically nested structure reaching a logger
    should produce a truncated line, not a recursion error inside logging.
    """
    if _depth > 6:
        return value
    if isinstance(value, str):
        return mask_text(value)
    if isinstance(value, dict):
        return {key: mask(item, _depth=_depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        masked = [mask(item, _depth=_depth + 1) for item in value]
        return type(value)(masked) if isinstance(value, tuple) else masked
    return value


def contains_pii(value: str) -> bool:
    """Whether a string carries anything this module would mask.

    Used by tests that assert a telemetry path stayed clean, which is the only
    way "logs carry no content" stops being an intention.
    """
    text = str(value or "")
    return any(
        pattern.search(text)
        for pattern in (_EMAIL, _PAN, _AADHAAR, _SSN, _CARD, _PHONE)
    )
