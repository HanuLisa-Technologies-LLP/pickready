"""The one answer to "which candidate record is this signed-in person".

WHY THIS MODULE EXISTS
----------------------
Before it, FOUR resolvers answered the question and they disagreed. The portal
matched `user_id = uid OR email = users.email` with no ORDER BY and lazily
wrote `user_id` onto whatever row came back first; the BGV and conversation
routes ran the same OR in raw SQL ordered by `created_at` and never linked;
the assessment and proctoring routes matched `user_id` alone. So one person
could be two candidates depending on which screen asked, and the email half
matched an address Firebase had never verified: a password sign-up carrying
somebody else's address resolved to THEIR sourced record, which is an account
takeover shaped read.

THE RULES, AND WHERE EACH ONE IS ENFORCED
-----------------------------------------
* A request resolves its candidate by `candidates.user_id` and NOTHING else
  (`resolve_candidate_id`, `require_candidate`). There is no request-time email
  fallback anywhere, so what a request can read is decided by one indexed
  column that only this module writes for a signed-in person.
* Email matching happens at SIGN-IN only, and only on an address Firebase says
  is verified (`link_on_sign_in`). An unverified identity gets a record of its
  own and is linked to nothing: it proved nothing about the address, so
  treating the address as proof would hand it a stranger's history.
* One user, one candidate. Migration 0120 makes `candidates.user_id` UNIQUE
  where it is set, so the rule is the database's and a second linked row is a
  constraint violation rather than a question every reader has to answer.
* An upload that names an email reuses the CANONICAL record for it
  (`find_canonical_by_email`): the account holder's own row first, then the
  oldest, case-insensitively. Upload paths are recruiter actions, not sign-in,
  so they may match by email; what they match is never a signed-in read.
* A sourced link converts to an application by RE-POINTING that one link
  (`rehome_sourced_link`), never by merging candidate rows. Twenty-odd tables
  reference `candidates.id` (erasure's own enumeration), and a bulk merge on
  live data is erasure-grade risk for a problem one UPDATE solves.

Nothing here commits. The caller's transaction owns every write, so a sign-in
that fails after linking leaves no link behind.
"""
from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.candidate import Candidate, JobCandidateLink
from app.models.enums import PipelineStatus
from app.models.user import User
from app.services import engagement
from app.services.audit import audit

if TYPE_CHECKING:
    from app.services.firebase_auth import FirebaseIdentity

logger = logging.getLogger(__name__)

#: Audit actions this module writes. Plain strings, like every other action in
#: `audit_log`; named here so a test can look for exactly them.
ACTION_CANDIDATE_LINKED = "candidate_linked"
ACTION_LINK_REHOMED = "candidate_link_rehomed"

#: The sentence a signed-in person with no candidate record reads. Unchanged
#: from the portal resolver it replaces, so no screen's copy moves.
NO_CANDIDATE_DETAIL = (
    "No candidate record yet, you appear after an employer's first outreach"
)


def normalise_email(email: str | None) -> str | None:
    """Trimmed and lower-cased; an empty or absent address is None.

    Email local parts are technically case-sensitive and no mail system this
    product talks to treats them that way. The comparison is case-insensitive
    everywhere in this module, and `ix_candidates_email_lower` is the index it
    reads through.
    """
    if email is None:
        return None
    cleaned = email.strip().lower()
    return cleaned or None


async def resolve_candidate_id(
    session: AsyncSession, user_id: uuid.UUID
) -> uuid.UUID | None:
    """The candidate id linked to this user, or None.

    Ordered even though `uq_candidates_user_id` allows one row, so the answer is
    deterministic on a database the migration has not reached yet.
    """
    return (
        await session.execute(
            select(Candidate.id)
            .where(Candidate.user_id == user_id)
            .order_by(Candidate.created_at, Candidate.id)
            .limit(1)
        )
    ).scalar_one_or_none()


async def require_candidate(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    record_engagement: bool = True,
) -> Candidate:
    """The signed-in person's candidate row, or 404.

    THE CHOKEPOINT for every candidate-audience handler, which is why the
    engagement stamp (feature 8) lives here: one call covers signing in,
    reading the board, applying and editing the profile. Not a commit; the stamp
    lands with whatever the request does, or with nothing if it fails.

    Takes the user id rather than the request principal so this service imports
    nothing from `app.api`.
    """
    candidate = (
        await session.execute(
            select(Candidate)
            .where(Candidate.user_id == user_id)
            .order_by(Candidate.created_at, Candidate.id)
            .limit(1)
        )
    ).scalars().first()
    if candidate is None:
        raise HTTPException(status_code=404, detail=NO_CANDIDATE_DETAIL)
    if record_engagement:
        engagement.record_engagement(candidate)
    return candidate


async def find_canonical_by_email(
    session: AsyncSession, email: str | None
) -> Candidate | None:
    """The record an upload naming `email` should attach to, or None.

    Case-insensitive. A row a person has signed in to wins over an unlinked
    one, because that is the record they will actually see; among equals the
    OLDEST wins, so two uploads of the same address agree with each other.
    """
    address = normalise_email(email)
    if address is None:
        return None
    return (
        await session.execute(
            select(Candidate)
            .where(func.lower(Candidate.email) == address)
            .order_by(
                Candidate.user_id.is_(None), Candidate.created_at, Candidate.id
            )
            .limit(1)
        )
    ).scalars().first()


async def link_on_sign_in(
    session: AsyncSession, user: User, identity: "FirebaseIdentity"
) -> Candidate:
    """Make sure this candidate user has exactly one candidate record.

    1. A record already linked to the user is returned untouched. That makes
       the call idempotent, so it runs on EVERY candidate sign-in and also
       repairs an account whose record an administrator erased.
    2. Otherwise, and ONLY when Firebase says the address is verified, the
       OLDEST unlinked record carrying that address (case-insensitive) is
       linked and the link is audited. That is the recruiter-sourced person
       signing in for the first time and finding their own history.
    3. Otherwise a fresh record is created. An unverified identity whose address
       matches a sourced record lands here too, deliberately: it is linked to
       nothing and can read nothing it did not create.

    NO CONSENT IS WRITTEN HERE. A sign-in shows no consent wording, so a row
    written here would record an agreement that never happened; the Stage A
    items are stamped where they are shown (PUT /portal/me/profile-form).
    """
    existing = (
        await session.execute(
            select(Candidate)
            .where(Candidate.user_id == user.id)
            .order_by(Candidate.created_at, Candidate.id)
            .limit(1)
        )
    ).scalars().first()
    if existing is not None:
        return existing

    address = normalise_email(identity.email)
    if identity.email_verified and address is not None:
        unlinked = (
            await session.execute(
                select(Candidate)
                .where(
                    Candidate.user_id.is_(None),
                    func.lower(Candidate.email) == address,
                )
                .order_by(Candidate.created_at, Candidate.id)
                .limit(1)
                .with_for_update()
            )
        ).scalars().first()
        if unlinked is not None:
            unlinked.user_id = user.id
            await session.flush()
            await audit(
                session,
                tenant_id=None,
                actor_user_id=user.id,
                action=ACTION_CANDIDATE_LINKED,
                target_type="candidate",
                target_id=unlinked.id,
                metadata={"candidate_id": str(unlinked.id),
                          "provider": identity.provider},
            )
            logger.info(
                "candidate_identity.linked user=%s candidate=%s", user.id, unlinked.id
            )
            return unlinked

    created = Candidate(
        tenant_id=None,
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
    )
    session.add(created)
    await session.flush()
    return created


async def rehome_sourced_link(
    session: AsyncSession,
    *,
    applicant: Candidate,
    job_id: uuid.UUID,
    verified_email: bool,
) -> JobCandidateLink | None:
    """Move a recruiter-sourced link on this job onto the applying person.

    The conversion merge rule. A recruiter uploaded a resume (an UNLINKED
    record, `status = sourced`), and the same person later signed up under a
    record of their own and applied. Without this, the application lands as a
    second link beside the sourced one and the recruiter sees one person twice.

    Re-points exactly one link when ALL of these hold, and otherwise returns
    None and touches nothing:

    * the applicant's address is verified (`verified_email`), because the
      address is the only thing connecting the two records;
    * the applicant has no link on the job yet, so the (job, candidate)
      uniqueness cannot collide;
    * the link is `sourced`, belongs to a DIFFERENT record that nobody has
      signed in to (`user_id IS NULL`), and that record carries the same
      address case-insensitively.

    The caller then runs the ordinary `sourced -> applied` transition on the
    returned link. The old record, its profile and its feed rows stay, as
    history; nothing else is re-pointed.
    """
    if not verified_email:
        return None
    address = normalise_email(applicant.email)
    if address is None:
        return None

    already = (
        await session.execute(
            select(JobCandidateLink.id).where(
                JobCandidateLink.job_id == job_id,
                JobCandidateLink.candidate_id == applicant.id,
            )
        )
    ).first()
    if already is not None:
        return None

    link = (
        await session.execute(
            select(JobCandidateLink)
            .join(Candidate, Candidate.id == JobCandidateLink.candidate_id)
            .where(
                JobCandidateLink.job_id == job_id,
                JobCandidateLink.status == PipelineStatus.sourced.value,
                Candidate.id != applicant.id,
                Candidate.user_id.is_(None),
                func.lower(Candidate.email) == address,
            )
            .order_by(JobCandidateLink.created_at, JobCandidateLink.id)
            .limit(1)
            .with_for_update(of=JobCandidateLink)
        )
    ).scalars().first()
    if link is None:
        return None

    previous = link.candidate_id
    link.candidate_id = applicant.id
    await session.flush()
    await audit(
        session,
        tenant_id=link.tenant_id,
        actor_user_id=applicant.user_id,
        action=ACTION_LINK_REHOMED,
        target_type="job_candidate_link",
        target_id=link.id,
        metadata={"from": str(previous), "to": str(applicant.id),
                  "link": str(link.id)},
    )
    return link
