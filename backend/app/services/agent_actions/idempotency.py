"""Idempotency keys derived from STABLE LOGICAL INPUTS (W5.1).

THE PATTERN IS ALREADY IN THIS REPOSITORY AND IT WORKS
--------------------------------------------------------
`credits` derives one key from a Razorpay payment id, and both the
checkout-verify path and the webhook derive THE SAME key from it. That is why
both can run for one payment and the customer is granted one month. The key is
a function of what the effect is ABOUT, never of when or by whom it was asked.

So the rule here is stated as a type constraint rather than as advice: a key
part must be a string, an integer or a UUID. A `datetime` is refused outright
by `derive`, because "never a timestamp" is the mistake that makes every retry
a fresh action and every duplicate invisible, and it is the one form of the
mistake a machine can catch. A per-attempt UUID cannot be caught by type, so
it is caught by test instead: `test_action_ledger.py` derives each key twice
from the same logical inputs and asserts they are equal.

WHY THE KIND IS PART OF THE KEY
---------------------------------
`agent_actions.idempotency_key` is UNIQUE across the whole table, so two
different kinds of action about the same row must not collide. Prefixing with
the kind makes the namespace explicit rather than hoping the identifiers
differ, and it makes an operator reading the column able to tell what the row
is without joining anything.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime
from typing import Any, Mapping

__all__ = [
    "KIND_ASSESSMENT_INVITATION",
    "KIND_PRISM_REPORT",
    "MAX_KEY_LENGTH",
    "IdempotencyKeyError",
    "args_digest",
    "assessment_invitation_key",
    "derive",
    "prism_report_key",
]

#: An assessment invitation: which application, which template, which version
#: of it. Re-sending the same template to the same application is the SAME
#: action; editing the template makes it a different one, which is why the
#: version is in the key rather than only the template name.
KIND_ASSESSMENT_INVITATION = "assessment_invitation"

#: A delivered PRISM report: which candidate, and every version of the inputs
#: the report was written from. A rescore against a new scorecard is a new
#: report and must not be deduped against the old one.
KIND_PRISM_REPORT = "prism_report"

#: `agent_actions.idempotency_key` is `varchar(200)`. A key that would be
#: truncated by the column is refused here rather than silently stored short,
#: because two truncated keys that differ past the cut would collide and the
#: second action would read as already done.
MAX_KEY_LENGTH = 200

_PART_TYPES = (str, int, uuid.UUID)


class IdempotencyKeyError(ValueError):
    """A key that cannot be trusted to be stable, refused at derivation.

    Raised rather than repaired. A key silently coerced from an unstable input
    is a key that looks fine in the column and dedupes nothing.
    """


def derive(kind: str, *parts: Any) -> str:
    """`kind:part:part:...`, from logical inputs only.

    Every part must be a string, an integer or a UUID, and none may be empty.
    A `datetime` or `date` is refused by name because it is the specific
    unstable input the specification calls out.
    """
    if not kind or not kind.strip():
        raise IdempotencyKeyError("an idempotency key needs a kind")
    if not parts:
        raise IdempotencyKeyError(
            f"{kind}: a key with no logical inputs identifies nothing"
        )
    rendered: list[str] = []
    for index, part in enumerate(parts):
        if isinstance(part, (datetime, date)):
            raise IdempotencyKeyError(
                f"{kind}: part {index} is a timestamp. An idempotency key is "
                "derived from stable logical inputs, and a timestamp makes "
                "every retry a new action."
            )
        if isinstance(part, bool) or not isinstance(part, _PART_TYPES):
            raise IdempotencyKeyError(
                f"{kind}: part {index} is {type(part).__name__}; a key part is "
                "a string, an integer or a UUID"
            )
        text = str(part).strip()
        if not text:
            raise IdempotencyKeyError(f"{kind}: part {index} is empty")
        if ":" in text:
            # A colon inside a part would let two different tuples render to
            # one key ("a:b", "c") and ("a", "b:c"), which is a collision the
            # UNIQUE constraint would report as "already done".
            raise IdempotencyKeyError(
                f"{kind}: part {index} contains the separator ':'"
            )
        rendered.append(text)
    key = ":".join([kind.strip(), *rendered])
    if len(key) > MAX_KEY_LENGTH:
        raise IdempotencyKeyError(
            f"{kind}: key is {len(key)} characters, over the "
            f"{MAX_KEY_LENGTH}-character column"
        )
    return key


def assessment_invitation_key(
    *, link_id: uuid.UUID | str, template: str, template_version: str | int
) -> str:
    """W5.1's worked example: `link_id + template + template_version`."""
    return derive(KIND_ASSESSMENT_INVITATION, link_id, template, template_version)


def prism_report_key(
    *,
    candidate_id: uuid.UUID | str,
    scorecard_version: str | int,
    assessment_version: str | int,
    report_version: str | int,
) -> str:
    """W5.1's second worked example.

    All three versions are in the key because all three change what the report
    SAYS. A report written against a frozen scorecard and one written after a
    rescore are different documents, and deduping them together would mean the
    second one silently never gets written.
    """
    return derive(
        KIND_PRISM_REPORT,
        candidate_id,
        scorecard_version,
        assessment_version,
        report_version,
    )


def args_digest(args: Mapping[str, Any]) -> str:
    """A sha256 over the arguments the effect was requested with.

    NOT the dedupe: the idempotency key is. This exists to catch a key REUSED
    for different arguments, which is a caller bug that would otherwise present
    as a successful no-op, with the second, different action never performed
    and nothing saying so.

    Canonical JSON with sorted keys, so two callers building the same mapping
    in a different order agree. Non-JSON values are rendered by `str`, which is
    stable for the identifiers and words that appear here; a mapping carrying
    something whose `str` is not stable (an object at its default `repr`) would
    make the digest differ between processes, so `default` refuses one.
    """
    return hashlib.sha256(
        json.dumps(dict(args), sort_keys=True, separators=(",", ":"), default=_render)
        .encode("utf-8")
    ).hexdigest()


def _render(value: Any) -> str:
    if isinstance(value, (uuid.UUID, datetime, date)):
        return str(value)
    raise IdempotencyKeyError(
        f"argument of type {type(value).__name__} has no stable rendering for "
        "the action digest"
    )
