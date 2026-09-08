"""Whether SES will actually send as a given corporate sender.

WHAT THIS REPLACED, AND WHY IT IS NOT THE SAME CHECK TWICE
----------------------------------------------------------
A mailbox OTP used to prove the POC controlled the address. It was withdrawn
because it established nothing AWS had not already established: SES refuses to
send as any identity the account has not verified, so an address that is not a
verified identity -- directly, or through its domain -- cannot send whatever a
six-digit code proved. Keeping both meant two mechanisms for one property, and
one of them put an OTP screen in a portal that bans OTP everywhere else.

The two checks that remain are genuinely different questions:

    Super Admin approval   Does this COMPANY authorize this address to speak
                           for it? A business decision, ReadyPick's to record.
    SES eligibility        Will AWS carry mail from it? An infrastructure
                           fact, AWS's to answer.

DOMAIN IDENTITY IS THE INTENDED SHAPE
-------------------------------------
Verifying `company.com` once makes every mailbox on it eligible, which is why
this resolves an address by walking up to its domain. Verifying each employee
address individually would mean one AWS resource per person, which is the very
thing the sender registry exists to avoid.

THIS NEVER CREATES AN IDENTITY, and the IAM policy grants no action that
could: `ses:GetIdentityVerificationAttributes` and `ses:ListIdentities`, both
read-only. Creating one would start a DNS conversation with a customer from
inside a request handler, and the result would be a sender marked eligible
that cannot actually send until somebody edits a zone file days later.

FAILING CLOSED, AND SAYING SO
-----------------------------
An AWS call that fails does NOT read as "eligible". It reads as UNKNOWN, and
the caller is told the check could not be completed rather than handed a
default. That matters because this informs an approval a Super Admin is about
to make: silently passing on an API timeout would record a decision the person
never actually got an answer for.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)

__all__ = [
    "SenderEligibility",
    "check_sender_eligibility",
    "reset_client",
]

#: What SES reports for an identity it will send as. Anything else -- Pending,
#: Failed, TemporaryFailure, NotStarted -- is not sending-capable yet.
_VERIFIED = "Success"

_client: Any = None


def reset_client() -> None:
    """Drop the cached client. Tests, and after a credential change."""
    global _client
    _client = None


def _ses_client() -> Any:
    """One classic `ses` client, bounded like every boto3 client here: an
    endpoint that HANGS defeats every try/except wrapped around the call."""
    global _client
    if _client is None:
        import boto3  # noqa: PLC0415 -- optional at import time
        from botocore.config import Config  # noqa: PLC0415

        settings = get_settings()
        _client = boto3.client(
            "ses",
            region_name=settings.aws_region,
            config=Config(
                connect_timeout=5,
                read_timeout=10,
                retries={"max_attempts": 2, "mode": "standard"},
            ),
        )
    return _client


@dataclass(frozen=True)
class SenderEligibility:
    """The answer, plus enough of the reason to act on it.

    `eligible is False` and `known is False` are DIFFERENT and callers must
    not collapse them: the first means AWS said no, the second means AWS could
    not be reached and nobody knows yet.
    """

    eligible: bool
    #: False when the lookup itself failed. `eligible` is then meaningless.
    known: bool
    #: Which identity carried the answer: the address, or its domain.
    matched_identity: str | None
    #: Operator-facing and safe to show a client. Never a raw AWS exception.
    detail: str

    @property
    def blocks_approval(self) -> bool:
        """True only for a DEFINITE no.

        An unknown does not block the Super Admin's business decision: the
        company's authorization and AWS's readiness are separate facts, and a
        transient AWS blip must not veto the former. The send path re-checks
        anyway, so an approval made during an outage still cannot send mail
        SES would refuse.
        """
        return self.known and not self.eligible


async def check_sender_eligibility(email: str) -> SenderEligibility:
    """Ask SES whether it would send as `email`.

    Checks the address as an identity, then its domain, because a verified
    domain covers every mailbox beneath it and that is the intended shape.
    """
    address = (email or "").strip().lower()
    if "@" not in address:
        return SenderEligibility(
            eligible=False, known=True, matched_identity=None,
            detail="Not a valid email address.",
        )
    domain = address.rsplit("@", 1)[1]
    candidates = [address, domain]

    try:
        client = _ses_client()
        response = await asyncio.to_thread(
            client.get_identity_verification_attributes, Identities=candidates
        )
    except Exception as exc:  # noqa: BLE001 -- normalised to `known=False`
        # NOT "eligible". The lookup failed, and that is all this knows.
        logger.warning(
            "email_senders.eligibility_lookup_failed err=%s", type(exc).__name__
        )
        return SenderEligibility(
            eligible=False, known=False, matched_identity=None,
            detail="Sending eligibility could not be checked right now.",
        )

    attributes = response.get("VerificationAttributes") or {}
    for identity in candidates:
        status = str((attributes.get(identity) or {}).get("VerificationStatus", ""))
        if status == _VERIFIED:
            return SenderEligibility(
                eligible=True, known=True, matched_identity=identity,
                detail=(
                    "The sending domain is verified."
                    if identity == domain
                    else "This address is verified for sending."
                ),
            )

    return SenderEligibility(
        eligible=False, known=True, matched_identity=None,
        # NO AWS VOCABULARY. This string reaches the client portal, where SES,
        # SNS, IAM and DKIM are deliberately not concepts the Super Admin has.
        detail=(
            "This address cannot send yet because its domain is not set up for "
            "sending. Your ReadyPick contact can complete that setup."
        ),
    )
