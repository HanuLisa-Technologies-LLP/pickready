"""Mandatory application fields -- validation, captured not assessed (spec §7).

Validation stopped being an agent on 2026-07-30. It is now five mandatory fields
on the application form itself, submitted together with the candidate's resume
before they can proceed to the assessment conversation. It was six until
2026-08-09, when the earliest joining date was removed (see VALIDATION_FIELDS).

Three consequences, all deliberate:

1. **Nothing here is scored, interpreted, or judged.** No agent reads it, no
   grade is attached to it, and the report shows it exactly as the candidate
   typed it. The RECRUITER decides whether a candidate's stated interest is
   genuine -- that judgement is not the product's to make.
2. **It is captured BEFORE any assessment spend.** CTC and notice period at
   application time let a recruiter filter out candidates plainly outside the
   budget or notice window before a single credit is consumed on them.
3. **It lives on the APPLICATION, not the candidate profile.** Current CTC and
   notice period change over time and are answered per opportunity; snapshotting
   them onto `job_candidate_links.validation_json` keeps each application an
   accurate record of what was true when it was submitted.

This module is the single source of truth for the field list. The API schema,
the report's Validation section and the frontend form all read it from here.
"""
from __future__ import annotations

from typing import Any

__all__ = [
    "DOCUMENT_READINESS_OPTIONS",
    "MANDATORY_KEYS",
    "NOTICE_PERIOD_OPTIONS",
    "REQUIRED_DOCUMENTS",
    "ROLE_INTEREST_MAX_CHARS",
    "ROLE_INTEREST_MIN_CHARS",
    "SECTION_INTRO",
    "VALIDATION_FIELDS",
    "missing_fields",
    "normalise",
    "reusable_defaults",
]

#: Shown above the mandatory fields. It states the REUSE behaviour, which is a
#: fact about the product rather than reassurance: the answers are prefilled on
#: every later application from the last one submitted, and the candidate edits
#: them when something has changed. `reusable_defaults` is what makes the
#: sentence true, so the two live in the same module and cannot drift.
SECTION_INTRO = (
    "The following information is mandatory. You need to fill this information "
    "only one time and automatically applicable to all other jobs which you "
    "apply, otherwise you edit."
)

#: Named because "All documents ready" listing nothing told the candidate to
#: attest to a set they could not see. This is the set the readiness answer
#: refers to; it is displayed beside the field and is never scored.
REQUIRED_DOCUMENTS: tuple[str, ...] = (
    "Government photo identity (Aadhaar, passport, or driving licence)",
    "PAN card",
    "Class X and Class XII certificates",
    "Degree certificates and consolidated marksheets",
    "Relieving or experience letters from every previous employer",
    "Latest three months of pay slips",
    "Most recent Form 16 or income tax return acknowledgement",
    "Provident Fund account number or UAN",
    "Passport size photograph",
)

NOTICE_PERIOD_OPTIONS: tuple[str, ...] = (
    "Immediate",
    "15 days",
    "30 days",
    "45 days",
    "60 days",
    "90 days",
    "Serving notice period",
)

DOCUMENT_READINESS_OPTIONS: tuple[str, ...] = (
    "All documents ready",
    "Most documents ready",
    "Some documents pending",
    "Documents not yet available",
)

#: The open-text field is the candidate's own words and is shown verbatim. The
#: floor stops a one-word answer from passing as a considered response; the
#: ceiling is a storage bound, not an editorial one.
ROLE_INTEREST_MIN_CHARS = 30
ROLE_INTEREST_MAX_CHARS = 2000

#: (key, label, type, options). `type` is what the frontend renders.
VALIDATION_FIELDS: tuple[dict[str, Any], ...] = (
    {
        "key": "current_ctc",
        "label": "Current CTC",
        "type": "text",
        "hint": "Your current annual fixed compensation.",
    },
    {
        "key": "expected_ctc",
        "label": "Expected CTC",
        "type": "text",
        "hint": "The annual compensation you are looking for.",
    },
    {
        "key": "notice_period",
        "label": "Notice period",
        "type": "select",
        "options": list(NOTICE_PERIOD_OPTIONS),
    },
    # `joining_date` (Earliest joining date) was removed on 2026-08-09, client
    # decision. It was a mandatory field that duplicated the notice period a
    # candidate had already answered one field earlier, and a date typed months
    # before an offer is not evidence of anything. Reports written before today
    # still carry the key in their own `validation_json` and still render it;
    # `normalise` simply stops accepting new values for it. No column was
    # dropped because there is none: the six fields are keys inside
    # `job_candidate_links.validation_json`.
    {
        "key": "document_readiness",
        "label": "Document readiness",
        "type": "select",
        "options": list(DOCUMENT_READINESS_OPTIONS),
        "hint": "Your readiness to produce the documents listed below.",
        "documents": list(REQUIRED_DOCUMENTS),
    },
    {
        "key": "role_interest",
        "label": "Why does this role interest you?",
        "type": "textarea",
        "hint": "In your own words. Shown to the recruiter exactly as you write it.",
    },
)

MANDATORY_KEYS: tuple[str, ...] = tuple(field["key"] for field in VALIDATION_FIELDS)


def reusable_defaults(previous: dict[str, Any] | None) -> dict[str, Any]:
    """The answers a candidate's NEXT application starts pre-filled with.

    The rule that these fields live on the APPLICATION and not the candidate
    profile is unchanged and is the reason this returns a copy rather than
    moving the storage: current CTC and notice period are true when they are
    answered and stale a quarter later, so every application keeps its own
    immutable snapshot of what was stated at the time. What the candidate is
    spared is RETYPING, not the chance to correct: the values arrive filled in
    and every one of them is editable before submitting.
    """
    return normalise(previous)


def normalise(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Trim and keep only the known keys. Unknown keys are dropped, not
    stored: this blob renders straight into the report, so it accepts exactly
    the fields the form defines and nothing a caller invents."""
    source = payload or {}
    out: dict[str, Any] = {}
    for key in MANDATORY_KEYS:
        value = source.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            out[key] = text[:ROLE_INTEREST_MAX_CHARS]
    return out


def missing_fields(payload: dict[str, Any] | None) -> list[str]:
    """Labels of the mandatory fields still unanswered, in form order.

    Returns LABELS rather than keys because the only caller is an error message
    the candidate reads.
    """
    values = normalise(payload)
    missing = [
        field["label"] for field in VALIDATION_FIELDS if not values.get(field["key"])
    ]
    interest = values.get("role_interest", "")
    if interest and len(interest) < ROLE_INTEREST_MIN_CHARS:
        missing.append(
            "Why does this role interest you? (please write at least a sentence)"
        )
    return missing
