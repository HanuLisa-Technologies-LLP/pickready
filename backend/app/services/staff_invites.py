"""Acceptance of an outstanding staff invitation at sign-in.

WHY THIS IS NOT ONLY INSIDE THE /join HANDLER
---------------------------------------------
"Did this person join?" had two independent answers and no wire between them:

  * `users.status`, flipped invited -> active by ANY proven sign-in (the
    Firebase exchange in `api/auth._finalize_single` and the workspace chooser
    in `services/login_context.select_context`);
  * `staff_invites.accepted_at`, written by exactly ONE endpoint,
    `POST /companies/invites/{token}/accept`, reached only by clicking the
    emailed link.

An invitee who ignores the link and simply signs in with the invited address
(identity resolution matches on the email, on purpose) therefore showed as
Active in one column and Pending in the next, for ever, because nothing else
in the product wrote `accepted_at`. The staff table reports the second column,
so a colleague who had been working in the product for weeks still read as
someone who had not answered their invitation. Reported 2026-09-12 and first
fixed on PR #5; ported here because main never carried it.

Signing in AS the invited user is acceptance: it proves ownership of the
invited address exactly as the token does. Both session-issuing paths call
`accept_pending_invite`, so the two trackers cannot drift apart again, and the
/join handler treats an invitation this sign-in already accepted for the SAME
user as accepted rather than as a spent link.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invite import StaffInvite
from app.services.audit import audit

__all__ = ["STAFF_INVITE_ACCEPTED", "accept_pending_invite"]

#: The same audit action the /join handler writes, so "who accepted, when"
#: has one name whichever door the invitee came through.
STAFF_INVITE_ACCEPTED = "staff_invite_accepted"


async def accept_pending_invite(
    session: AsyncSession, user_id: uuid.UUID, *, now: datetime | None = None
) -> StaffInvite | None:
    """Mark this user's outstanding invitation accepted; return it, or None.

    Deliberately narrow, only a genuinely PENDING row is touched:

      * already accepted: left alone, because the timestamp records the FIRST
        acceptance and re-stamping it on every later sign-in would turn an
        audit fact into a last-seen clock;
      * revoked: never resurrected by a later sign-in, or withdrawing an
        invitation would be undone by the invitee signing in;
      * expired: still has to be resent, matching `_resolve_invite`'s 410.

    At most one pending row exists per staff user (resending revokes the
    previous one), and a candidate or any user with no invitation matches
    nothing, so this is a quiet no-op on every ordinary sign-in. The audit row
    is written in ONE insert, inside the caller's transaction, which commits
    with the session it issues.
    """
    moment = now or datetime.now(timezone.utc)
    invite = (
        await session.execute(
            select(StaffInvite)
            .where(
                StaffInvite.user_id == user_id,
                StaffInvite.accepted_at.is_(None),
                StaffInvite.revoked_at.is_(None),
                StaffInvite.expires_at > moment,
            )
            .order_by(StaffInvite.created_at.desc())
        )
    ).scalars().first()
    if invite is None:
        return None
    invite.accepted_at = moment
    await audit(
        session,
        tenant_id=invite.tenant_id,
        actor_user_id=user_id,
        action=STAFF_INVITE_ACCEPTED,
        target_type="user",
        target_id=user_id,
        metadata={"invite_id": str(invite.id), "via": "sign_in"},
    )
    return invite
