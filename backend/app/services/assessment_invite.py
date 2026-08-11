"""Signed, expiring assessment-invitation links (2026-08-11).

The problem this solves
-----------------------
The invitation email used to carry `{frontend}/portal/assessments/{link_id}`.
That is a raw application id inside a route the candidate portal shell guards,
so the practical behaviour was:

  * the id is a bare identifier in an email, with nothing tying the link to the
    person it was sent to, and nothing that expires;
  * an unauthenticated click hit the portal shell, which bounced to `/login`
    with NO `next`, so after signing in the candidate landed on the jobs board
    and had to go and find the assessment they had just been mailed a link to.

So the link is now a TOKEN, and the token is the thing that carries identity
and an expiry. It resolves through a public endpoint that decides which of the
five terminal states the candidate is actually in before any portal route is
involved.

Design notes
------------
* It is a JWT because the product already signs stateless links this way
  (`deps.make_outreach_token`) and `exp` is then enforced by the library rather
  than by hand. It carries its OWN purpose and audience so it can never be
  replayed as a session token, and `deps._decode_or_401` will never accept it:
  the audience does not match any portal.

* The token is bound to BOTH the application link and the invited email. The
  email binding is what makes "you are signed in as someone else" a detectable
  state instead of silently attaching one person's assessment to another
  person's account. Emails are compared casefolded and stripped, because a mail
  client is free to change the case of an address and a candidate is free to
  type theirs differently when they register.

* The TTL is the posting window plus its grace tail plus a day
  (`INVITE_TTL_DAYS`), so the signature outliving the job is never the reason a
  link fails. The window itself is authoritative and is checked separately
  against the job -- an expired token and a closed posting are different
  states and the candidate is told which one they hit.

* `verify` raises a typed error rather than an HTTPException. The API layer
  turns each reason into a state the page can render; a service that raises
  HTTP has already decided how the failure looks.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import jwt as pyjwt

from app.core.config import get_settings
from app.core.security import ALGORITHM

#: Distinct from every portal audience in `core.security`. A token minted here
#: must never satisfy an authentication dependency, and vice versa.
AUDIENCE_INVITE = "pickready:assessment-invite"

#: Purpose claim, checked in addition to the audience. Belt and braces: it
#: costs one comparison and it makes a future second invite type impossible to
#: confuse with this one.
PURPOSE = "assessment_invitation"

#: 30-day posting window + 5-day grace + 1 day of slack. The signature must not
#: be what expires first -- if a link is dead, the reason should be a product
#: rule the candidate can be told about, not a cryptographic lifetime.
INVITE_TTL_DAYS = 36


class InviteTokenError(Exception):
    """Raised when a token cannot be trusted.

    `reason` is one of `expired` | `invalid`, and the two are kept apart
    because they read very differently to a candidate: one is "this link is too
    old", the other is "this is not one of our links".
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def mint(
    *,
    link_id: uuid.UUID | str,
    email: str,
    conversation_id: uuid.UUID | str | None = None,
    ttl_days: int = INVITE_TTL_DAYS,
) -> str:
    """Sign an invitation for one application, addressed to one email."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "link_id": str(link_id),
        "email": normalize_email(email),
        "purpose": PURPOSE,
        "aud": AUDIENCE_INVITE,
        "iat": now,
        "exp": now + timedelta(days=ttl_days),
        "type": "assessment_invite",
    }
    if conversation_id is not None:
        payload["conversation_id"] = str(conversation_id)
    return pyjwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def verify(token: str) -> dict:
    """Return the payload, or raise `InviteTokenError`.

    Note what is NOT done here: no database is touched. Whether the assessment
    still exists, is still open, or has already been submitted are questions
    about the world, not about the token, and answering them here would make
    this untestable without a session.
    """
    settings = get_settings()
    try:
        payload = pyjwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[ALGORITHM],
            audience=AUDIENCE_INVITE,
        )
    except pyjwt.ExpiredSignatureError as exc:
        raise InviteTokenError("expired") from exc
    except pyjwt.PyJWTError as exc:
        raise InviteTokenError("invalid") from exc

    if payload.get("purpose") != PURPOSE:
        raise InviteTokenError("invalid")
    if not payload.get("link_id") or not payload.get("email"):
        raise InviteTokenError("invalid")
    try:
        uuid.UUID(str(payload["link_id"]))
    except (ValueError, TypeError) as exc:
        raise InviteTokenError("invalid") from exc
    return payload


def normalize_email(email: str) -> str:
    """The one comparison form. Case and surrounding whitespace are noise: a
    mail client may echo `Asha@Example.com` and the same person may register as
    `asha@example.com `, and those are one candidate, not two."""
    return (email or "").strip().casefold()


def emails_match(invited: str | None, signed_in: str | None) -> bool:
    """True when the account opening the link is the one it was sent to.

    An empty value on either side is NOT a match. An account with no email
    recorded must not walk into someone else's assessment because both sides
    normalised to the empty string.
    """
    left = normalize_email(invited or "")
    right = normalize_email(signed_in or "")
    if not left or not right:
        return False
    return left == right


def mask_email(email: str | None) -> str:
    """`asha@example.com` -> `as***@example.com`.

    Shown on the wrong-account screen so the candidate can tell WHICH of their
    addresses the invitation went to, without the page printing a full address
    to whoever is holding the link at the time.
    """
    value = (email or "").strip()
    if "@" not in value:
        return "the invited address"
    local, _, domain = value.partition("@")
    if len(local) <= 2:
        head = local[:1] or "*"
    else:
        head = local[:2]
    return f"{head}***@{domain}"


def invite_path(token: str) -> str:
    """The frontend route a minted token belongs in.

    Kept here so the email builder, the tests and any future caller cannot
    drift apart on the path -- a wrong path in an email is invisible until a
    candidate reports a dead link.
    """
    return f"/assessments/invite/{token}"


def assessment_link_url(
    frontend_url: str,
    *,
    link_id: uuid.UUID | str,
    email: str | None,
    conversation_id: uuid.UUID | str | None = None,
) -> str:
    """The URL that goes in an assessment email. The ONLY builder.

    Falls back to the old direct portal URL when the candidate has no email on
    record. That case cannot arise for an invitation (there is nowhere to send
    it), but a link with no addressee cannot be bound to one, and a working
    link that costs the wrong-account check is a better failure than a token
    bound to the empty string -- which `emails_match` would refuse for
    everybody, turning a rare data gap into a dead link.
    """
    base = (frontend_url or "").rstrip("/")
    if not email or not normalize_email(email):
        return f"{base}/portal/assessments/{link_id}"
    token = mint(link_id=link_id, email=email, conversation_id=conversation_id)
    return f"{base}{invite_path(token)}"
