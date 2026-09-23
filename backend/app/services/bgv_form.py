"""The employer HR checkbox form (vivekium feature 4, built under C4 and C8).

WHY A FORM SUBMISSION MAY SET A STATUS when the inbound-reply rule says
nothing infers a verdict: that rule forbids a MODEL inferring a verdict from
free text. An HR person ticking the boxes IS the human decision, made by a
better-placed human than the recruiter, and the submission is evidence of
the same kind as a recruiter's click. The free-text reply path keeps its
human verdict and `parse_reply` still never writes a status; this module is
the ONLY other writer, and what it writes is exactly what the employer
submitted.

THE TOKEN IS THE CREDENTIAL, unique per employer-candidate verification and
single-use, with the 3-day expiry the brief specifies (SEC-20's lesson: an
un-expiring form link in a third party's mailbox is a standing credential).
The TTL is `verification_link_ttl_days`, the same setting the retiring
system used, because 3 days is the brief's number for both.

THE SEVEN ITEMS ARE DATA, verbatim from the brief in substance, re-set
without the characters and terms the platform forbids. Six are required;
"eligible for rehire" is optional by the brief's own marking. A submission
that confirms every required item verifies the employment; one that cannot
is recorded as NOT verified, with what was and was not confirmed stored, and
the recruiter's decision route can still override it either way, which keeps
the human path above the form rather than beneath it.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.config import get_settings
from app.models.bgv_verification import (
    VERIFICATION_NOT_VERIFIED,
    VERIFICATION_VERIFIED,
)


@dataclass(frozen=True)
class FormItem:
    key: str
    label: str
    required: bool = True


#: The brief's seven checkbox items. Keys land in `form_answers_json`, so
#: they are permanent identifiers.
FORM_ITEMS: tuple[FormItem, ...] = (
    FormItem("employment_period_accurate", "The employment period stated is accurate."),
    FormItem("job_title_accurate", "The job title stated is accurate."),
    FormItem(
        "compensation_accurate", "The compensation details stated are accurate."
    ),
    FormItem(
        "third_party_bgv_conducted",
        "A third-party background verification was conducted during employment.",
    ),
    FormItem(
        "education_verified_at_onboarding",
        "Education certificates were verified at onboarding.",
    ),
    FormItem(
        "address_verified_at_onboarding",
        "Address proof was verified at onboarding.",
    ),
    FormItem("eligible_for_rehire", "Eligible for rehire.", required=False),
)

ITEM_KEYS: frozenset[str] = frozenset(item.key for item in FORM_ITEMS)
REQUIRED_KEYS: tuple[str, ...] = tuple(
    item.key for item in FORM_ITEMS if item.required
)


def items_payload() -> list[dict[str, Any]]:
    """The items as served to the form, so the page authors no copy."""
    return [
        {"key": item.key, "label": item.label, "required": item.required}
        for item in FORM_ITEMS
    ]


def mint_token() -> str:
    """A single-use, unguessable form token."""
    return secrets.token_urlsafe(32)


def expires_at(issued_at: datetime) -> datetime:
    """When a form link stops working: issue time plus the configured TTL."""
    return issued_at + timedelta(days=get_settings().verification_link_ttl_days)


def is_expired(issued_at: datetime, now: datetime | None = None) -> bool:
    return (now or datetime.now(timezone.utc)) >= expires_at(issued_at)


def clean_answers(raw: Any) -> dict[str, bool]:
    """Only known keys, only booleans. Anything else is dropped, so a stale
    or hostile client cannot grow the stored blob."""
    if not isinstance(raw, dict):
        return {}
    return {
        key: bool(value) for key, value in raw.items() if key in ITEM_KEYS
    }


def status_from_answers(answers: dict[str, bool]) -> str:
    """VERIFIED when every required item is confirmed; NOT_VERIFIED otherwise.

    A partial confirmation is an employer declining to confirm a claim, which
    is the finding `derive_status` lets dominate; what was and was not
    confirmed is stored beside it, and the recruiter's decision route remains
    the human override in both directions.
    """
    if all(answers.get(key) is True for key in REQUIRED_KEYS):
        return VERIFICATION_VERIFIED
    return VERIFICATION_NOT_VERIFIED


def masked_email(address: str | None) -> str:
    """The partially masked HR address the candidate-facing emails carry.

    First character of the local part, then the domain: `h***@corp.example`.
    The candidate supplied this address themselves, but the brief masks it in
    outbound mail and a masked address in a forwarded email leaks less.
    """
    if not address or "@" not in address:
        return "(address unavailable)"
    local, _, domain = address.partition("@")
    head = local[0] if local else "?"
    return f"{head}***@{domain}"
