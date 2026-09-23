"""The renewal ACT: the one-click token, and what confirming actually writes.

THE HOLE THIS CLOSES
----------------------
`consent_renewed_at` was READ in three places and WRITTEN IN NONE. There was
no route, no link and no confirm endpoint anywhere in the product, so a
candidate who did exactly what the reminder letter told them to could not
renew: signing in stamps `last_engagement_at`, which is the INACTIVITY clock
and not the consent clock, and the two are independent by design (C6).

The consequence was live. Every candidate advanced to `deletion_due` whatever
they did, and the only reason nobody was erased is that
`consent_auto_deletion_enabled` defaults to off. The letter's own sentence,
"sign in and confirm to stay visible", described a control that did not exist.

WHY A TOKEN AT ALL, AND WHY IT IS NOT A CREDENTIAL
----------------------------------------------------
Signing in and pressing a button is the authenticated path and it is here too
(`renew`, called by the portal route). The token exists for the other path:
somebody who has not signed in for six months and whose six-month letter is in
front of them right now. Asking them to remember a password to answer "yes,
keep my profile" is how a compliance feature turns into a deletion queue.

Four properties, and every one of them is what stops it being a bearer
credential for anything else:

  * IT AUTHORISES EXACTLY ONE ACT. `renew` is the only function that consumes
    it. There is no session minted, no cookie set, and no route anywhere
    accepts it as identification. Presenting it gets the holder one outcome:
    a profile that was going to be deleted is not.
  * IT IS SINGLE USE. Renewal CLEARS the stored hash in the same statement
    that stamps the renewal, so a link in a forwarded mailbox is spent the
    first time it is followed.
  * IT IS SHORT LIVED. `consent_renewal_token_expires_at` is checked before
    anything is written, and the sweep mints a fresh one with each letter.
  * ONLY THE HASH IS STORED. The token itself exists in the letter and nowhere
    else, the posture `services/otp` already takes: a database copy would be
    replayable by anybody who could read the row, which defeats the point of
    hashing at all.

It is also the SAFE DIRECTION if it ever leaks. The worst an attacker achieves
is keeping somebody's profile on a platform they asked to stay on. There is no
read, no write to any other field, and no way to reach a deletion.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings

#: Bytes of entropy in the token. 32 bytes is 256 bits, url-safe encoded, the
#: same weight `conversations.thread_token` carries and for the same reason:
#: the string is the whole thing standing between a mailbox and an act.
TOKEN_BYTES = 32

#: How long a renewal link lives. The GRACE period, deliberately, rather than a
#: number of its own: the letter opens a window in which the candidate may
#: answer, and a link that died before that window closed would tell somebody
#: their link had expired while the letter in front of them said they still had
#: time. One number, read from settings, so the two cannot drift.
def token_ttl() -> timedelta:
    return timedelta(days=get_settings().consent_grace_days)


#: What the candidate is told after a successful renewal. Server-authored, like
#: every other sentence describing a rule, so the page and the rule cannot
#: drift. No number and no em dash.
RENEWED_MESSAGE = (
    "Thank you. Your profile will stay on the platform and you are visible to "
    "employer clients registered on the platform."
)

#: What an unusable link is answered with. Says what to do next and nothing
#: about whose link it was: this route is unauthenticated, so a message that
#: distinguished "expired" from "never existed" would confirm to a stranger
#: that a particular token had once been real.
INVALID_TOKEN_MESSAGE = (
    "This link is no longer valid. Sign in to confirm that you would like to "
    "keep your profile."
)


@dataclass(frozen=True)
class MintedToken:
    """The token to put in a letter, and the hash to put on the row."""

    token: str
    token_hash: str
    expires_at: datetime


def hash_token(token: str) -> str:
    """The stored form. SHA-256, hex, so the column is a fixed 64 characters.

    Not a password hash and deliberately not one: the input is 256 bits of
    machine-generated entropy with no dictionary behind it, so the slow-hash
    argument that applies to a human-chosen secret buys nothing here, and this
    lookup is on the path of a link somebody just clicked.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def mint(*, now: datetime | None = None) -> MintedToken:
    """A fresh single-use renewal token."""
    moment = now or datetime.now(timezone.utc)
    token = secrets.token_urlsafe(TOKEN_BYTES)
    return MintedToken(
        token=token, token_hash=hash_token(token), expires_at=moment + token_ttl()
    )


def renewal_url(token: str) -> str:
    """Where the one-click link in the letter points.

    A frontend page rather than the API, so following a link from a mailbox is
    a GET that renders something a person can read, and the act itself is the
    POST that page makes. The shape `verify-employment/{token}` already uses.
    """
    base = get_settings().frontend_url.rstrip("/")
    return f"{base}/keep-profile/{token}"


class RenewalTokenInvalid(RuntimeError):
    """The token names no candidate, or names one whose token has expired."""


async def store_token(
    session: AsyncSession, candidate_id: uuid.UUID | str, minted: MintedToken
) -> None:
    """Attach a freshly minted token to one candidate.

    Overwrites any previous one, which is what makes the LATEST letter the live
    one: a candidate who receives the reminder and then the final warning has
    two letters in their mailbox, and following the older link must not renew
    them under a token the platform has stopped tracking.
    """
    await session.execute(
        text(
            "UPDATE candidates SET consent_renewal_token_hash = :hash, "
            "consent_renewal_token_expires_at = :expires WHERE id = :id"
        ),
        {
            "hash": minted.token_hash,
            "expires": minted.expires_at,
            "id": str(uuid.UUID(str(candidate_id))),
        },
    )


async def candidate_id_for_token(
    session: AsyncSession, token: str, *, now: datetime | None = None
) -> uuid.UUID:
    """Which candidate this token renews. Raises when it renews nobody.

    THE EXPIRY IS IN THE STATEMENT. Checking it in Python after the row comes
    back would work and would also mean an expired token still produced a row
    for something later to act on; here an expired token produces nothing at
    all, and there is no branch that can forget the check.
    """
    moment = now or datetime.now(timezone.utc)
    row = (
        await session.execute(
            text(
                "SELECT id FROM candidates "
                "WHERE consent_renewal_token_hash = :hash "
                "AND consent_renewal_token_expires_at > :now"
            ),
            {"hash": hash_token(token), "now": moment},
        )
    ).first()
    if row is None:
        raise RenewalTokenInvalid(INVALID_TOKEN_MESSAGE)
    return uuid.UUID(str(row[0]))


async def renew(
    session: AsyncSession, candidate_id: uuid.UUID | str, *, now: datetime | None = None
) -> datetime:
    """Confirm this candidate's consent and restart the cycle.

    FOUR COLUMNS IN ONE STATEMENT, and every one of them is load bearing:

      consent_renewed_at        the new start of the cycle. `consented_at_for`
                                reads it in preference to `created_at`, so
                                `stage_for` answers ACTIVE from here.
      consent_reminder_sent_at  cleared. The stamps gate the STAGES, so a
                                reminder left standing would put the candidate
                                back at the final-warning step the moment the
                                grace period elapsed, having just told them
                                they were safe.
      consent_final_warning_at  cleared, for the same reason.
      the token hash            cleared, which is what makes the link single
                                use, together with its expiry.

    The renewal itself is not reimplemented here: this writes the stamps, and
    `consent_lifecycle.stage_for` is still the only thing that says what they
    mean. `tests/test_consent_renewal.py` asserts the stage AFTERWARDS through
    that function rather than restating a threshold.
    """
    moment = now or datetime.now(timezone.utc)
    await session.execute(
        text(
            "UPDATE candidates SET consent_renewed_at = :now, "
            "consent_reminder_sent_at = NULL, consent_final_warning_at = NULL, "
            "consent_renewal_token_hash = NULL, "
            "consent_renewal_token_expires_at = NULL "
            "WHERE id = :id"
        ),
        {"now": moment, "id": str(uuid.UUID(str(candidate_id)))},
    )
    return moment
