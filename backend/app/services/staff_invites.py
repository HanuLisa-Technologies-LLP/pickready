"""Acceptance of an outstanding staff invitation.

WHY THIS IS NOT INSIDE THE /join HANDLER
----------------------------------------
"Did this person join?" had two independent answers and no wire between them:

  * `users.status` — flipped invited -> active by ANY proven sign-in (the
    Firebase exchange in `api/auth`, and both OTP paths in `services/otp`);
  * `staff_invites.accepted_at` — written by exactly ONE endpoint,
    `POST /companies/invites/{token}/accept`, reached only by clicking the
    emailed link.

An invitee who ignores the link and simply signs in with the invited address —
which identity resolution explicitly supports, it matches on email — therefore
showed as `Active` in one column and `Pending` in the next, for ever, because
nothing else in the product ever writes `accepted_at`. The staff table reports
the second column, so a colleague who had been working in the product for weeks
still read as someone who had not responded to their invitation.

Signing in AS the invited user is acceptance: it proves ownership of the
invited address exactly as the token does. This function is called from every
place that issues a session, so the two trackers cannot drift apart again.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invite import StaffInvite


async def accept_pending_invite(
    session: AsyncSession, user_id: uuid.UUID, *, now: datetime | None = None
) -> StaffInvite | None:
    """Mark this user's outstanding invitation accepted; return it, or None.

    Deliberately narrow — only a genuinely PENDING row is touched:

      * already accepted -> left alone (the timestamp records the FIRST
        acceptance, and re-stamping it on every subsequent login would turn an
        audit fact into a last-seen clock);
      * revoked -> never resurrected by a later sign-in, or withdrawing an
        invitation would be undone by the invitee logging in;
      * expired -> still has to be resent, matching `_resolve_invite`'s 410.

    At most one pending row exists per staff user (models/invite.StaffInvite:
    resending revokes the previous one), and a candidate with no invitation
    matches nothing. Flushing is left to the caller, which is already inside a
    transaction that commits the session it issued.
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
    if invite is not None:
        invite.accepted_at = moment
    return invite
