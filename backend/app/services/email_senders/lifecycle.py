"""Sender lifecycle FSM + the business-email gate (spec sections 2 and 5).

THE FSM IS DATA, AND ILLEGAL MOVES ARE REFUSED HERE
---------------------------------------------------
`TRANSITIONS` is the whole state machine. Every route that changes a sender's
status calls `assert_transition` first, so "a revoked sender came back to
life" is impossible at one chokepoint rather than a property each handler has
to remember. The spec's AUTHORIZED stage is collapsed into ACTIVE by design:
the Super Admin's authorize action IS activation (recorded as
`authorized_by`/`authorized_at` on the row), so a separate stored AUTHORIZED
state would exist only between two lines of one handler.

THE BLOCKLIST IS CONFIGURATION, NOT CODE
----------------------------------------
`settings.sender_domain_blocklist` (spec section 2 asks for a configurable
list, not a hardcoded few). Matching refuses exact domains AND their
subdomains, because `hr@mail.gmail.com` is not a business address either.
"""
from __future__ import annotations

import re

from app.core.config import get_settings
from app.models.email_sender import (
    SENDER_ACTIVE,
    SENDER_DISABLED,
    SENDER_EMAIL_VERIFIED,
    SENDER_PENDING_VERIFICATION,
    SENDER_REVOKED,
    SENDER_STATUSES,
    SENDER_VERIFICATION_EXPIRED,
)

__all__ = [
    "IllegalSenderTransition",
    "SenderDomainBlocked",
    "SenderEmailInvalid",
    "TRANSITIONS",
    "assert_transition",
    "validate_business_email",
]

#: current status -> the statuses it may move to. Everything absent is refused.
#: `revoked` is terminal: the spec's section 11 removal has no way back, and a
#: client who wants the mailbox again registers and verifies it afresh.
TRANSITIONS: dict[str, frozenset[str]] = {
    SENDER_PENDING_VERIFICATION: frozenset(
        {SENDER_EMAIL_VERIFIED, SENDER_VERIFICATION_EXPIRED, SENDER_REVOKED}
    ),
    SENDER_VERIFICATION_EXPIRED: frozenset(
        # A resend re-arms verification; nothing else leaves this state alive.
        {SENDER_PENDING_VERIFICATION, SENDER_REVOKED}
    ),
    SENDER_EMAIL_VERIFIED: frozenset({SENDER_ACTIVE, SENDER_REVOKED}),
    SENDER_ACTIVE: frozenset({SENDER_DISABLED, SENDER_REVOKED}),
    SENDER_DISABLED: frozenset({SENDER_ACTIVE, SENDER_REVOKED}),
    SENDER_REVOKED: frozenset(),
}

# The map must cover the vocabulary exactly; a status the CHECK constraint
# accepts but this dict does not know would make every action on it a KeyError.
assert set(TRANSITIONS) == set(SENDER_STATUSES)


class IllegalSenderTransition(ValueError):
    """The requested status change is not an edge of the FSM."""

    def __init__(self, current: str, target: str) -> None:
        self.current = current
        self.target = target
        super().__init__(
            f"A sender cannot move from {current} to {target}."
        )


def assert_transition(current: str, target: str) -> None:
    """Refuse anything that is not a declared edge. The single chokepoint."""
    if target not in TRANSITIONS.get(current, frozenset()):
        raise IllegalSenderTransition(current, target)


# ── The business-email gate (spec section 2) ────────────────────────────────

class SenderEmailInvalid(ValueError):
    """Not a plausible mailbox address at all."""


class SenderDomainBlocked(ValueError):
    """A personal/free provider domain; refused with the domain named."""

    def __init__(self, domain: str) -> None:
        self.domain = domain
        super().__init__(
            f"{domain} is a personal email provider. Corporate senders must "
            "use a business domain your company controls."
        )


#: Deliberately simple: one local part, one @, a dotted domain. Deeper
#: validation is the OTP's job -- an address that cannot receive the code
#: never verifies, which is a stronger check than any regex.
_EMAIL_RE = re.compile(r"^[^@\s]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)$")


def validate_business_email(email: str) -> str:
    """Normalise and gate one sender address.

    Returns the LOWERCASED address (the stored form; the per-tenant unique
    constraint and the lower() CHECK in migration 0080 both assume it).
    Raises `SenderEmailInvalid` for a malformed address and
    `SenderDomainBlocked` for a free-provider domain or any subdomain of one.
    """
    candidate = (email or "").strip().lower()
    match = _EMAIL_RE.match(candidate)
    if match is None:
        raise SenderEmailInvalid(
            "Enter a full email address, like hr@yourcompany.com."
        )
    domain = match.group(1)
    blocked = get_settings().sender_blocked_domains()
    parts = domain.split(".")
    for start in range(len(parts) - 1):
        if ".".join(parts[start:]) in blocked:
            raise SenderDomainBlocked(domain)
    return candidate
