"""Whether a candidate's background verification is complete, and what that blocks.

DETERMINISTIC, BY CONSTRUCTION
-------------------------------
Nothing in this module calls a model, and it must stay that way. The agent
writes an email; a recruiter reads a reply and makes a decision; THIS decides
what those decisions add up to, and it decides it by counting rows. An LLM that
could answer "is this candidate verified" would be an LLM that can authorise an
offer, which is the one thing the brief and `hiring/layers.INVARIANTS` both
refuse. `tests/test_bgv_workflow.py` asserts the absence of a router import by
AST, the same way the Miti aggregator's determinism is asserted.

THE CANDIDATE-LEVEL STATUS IS DERIVED AND NEVER STORED
--------------------------------------------------------
Same rule as `profile_age` and `posting_status`. A stored copy of a derived
fact is a copy that can disagree with the fact, and this particular fact
decides whether somebody receives an offer. It is computed from the employment
rows and the tenant's own verification rows, every time.

WHAT "REQUIRED" MEANS, EXACTLY
-------------------------------
Only an explicit `experienced` declaration makes verification required. A
fresher is never blocked, and a candidate who has not answered the question is
not blocked either: an unanswered profile field is an unanswered question, and
ending somebody's candidacy over one would be the product deciding something
nobody asked it to decide. That asymmetry is deliberate and is tested in both
directions.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bgv_verification import (
    CANDIDATE_BGV_NOT_REQUIRED,
    CANDIDATE_BGV_NOT_STARTED,
    CANDIDATE_BGV_NOT_VERIFIED,
    CANDIDATE_BGV_PENDING,
    CANDIDATE_BGV_VERIFIED,
    VERIFICATION_NOT_VERIFIED,
    VERIFICATION_VERIFIED,
)
from app.models.employment import BACKGROUND_EXPERIENCED

# ── Audit actions ────────────────────────────────────────────────────────────
#
# Named here rather than spelled at each call site, so the audit log's
# vocabulary is a list somebody can read rather than a set of strings somebody
# has to grep for.

AUDIT_HISTORY_SUBMITTED = "bgv_history_submitted"
AUDIT_VERIFICATION_INITIATED = "bgv_verification_initiated"
AUDIT_EMAIL_GENERATED = "bgv_email_generated"
AUDIT_EMAIL_SENT = "bgv_email_sent"
AUDIT_EMAIL_FAILED = "bgv_email_failed"
AUDIT_RESPONSE_RECEIVED = "bgv_response_received"
AUDIT_MARKED_VERIFIED = "bgv_marked_verified"
AUDIT_MARKED_NOT_VERIFIED = "bgv_marked_not_verified"
AUDIT_SELECTION_BLOCKED = "bgv_selection_blocked"
AUDIT_SELECTION_UNLOCKED = "bgv_selection_unlocked"

#: The candidate-level states from which an offer may proceed. Two, and both
#: are positive statements: "this person does not need verification" and "this
#: person has been verified". Everything else, including the state where nobody
#: has started, holds the offer.
OFFER_ALLOWED_STATUSES: frozenset[str] = frozenset(
    {CANDIDATE_BGV_NOT_REQUIRED, CANDIDATE_BGV_VERIFIED}
)


class BGVIncomplete(RuntimeError):
    """An offer was attempted before the required verification was complete.

    Carries the message the caller shows verbatim, so no route invents its own
    wording for a refusal the recruiter has to act on. Same shape as
    `hiring/company_requirements.creation_blocked`.
    """

    def __init__(self, message: str, status: str) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def derive_status(
    *, background: str | None, employer_count: int, statuses: list[str]
) -> str:
    """The candidate-level status, from counts alone.

    Pure, so the whole truth table is testable without a database. The order of
    the branches is the specification:

      * not experienced           -> NOT_REQUIRED, whatever else is true
      * any employer NOT_VERIFIED -> NOT_VERIFIED. One employer who says the
        claim is wrong is a finding about the candidate, and it outranks six
        employers who confirmed: a status that averaged them would hide it.
      * no verification rows      -> NOT_STARTED
      * every employer VERIFIED   -> VERIFIED, and only then
      * anything else             -> PENDING
    """
    if background != BACKGROUND_EXPERIENCED:
        return CANDIDATE_BGV_NOT_REQUIRED
    if VERIFICATION_NOT_VERIFIED in statuses:
        return CANDIDATE_BGV_NOT_VERIFIED
    if not statuses:
        return CANDIDATE_BGV_NOT_STARTED
    verified = sum(1 for value in statuses if value == VERIFICATION_VERIFIED)
    # EVERY submitted employer, not every verification row that happens to
    # exist. A candidate with seven employers and three verifications is
    # PENDING even when all three say verified, because the other four have
    # not been asked. Counting only the rows that exist is how a partial
    # verification comes to read as a complete one.
    if employer_count > 0 and verified == employer_count:
        return CANDIDATE_BGV_VERIFIED
    return CANDIDATE_BGV_PENDING


async def candidate_status(
    session: AsyncSession, *, tenant_id: uuid.UUID, candidate_id: uuid.UUID
) -> str:
    """This tenant's view of one candidate's background verification."""
    background = (
        await session.execute(
            text("SELECT employment_background FROM candidates WHERE id = :cid"),
            {"cid": str(candidate_id)},
        )
    ).scalar()
    employer_count = (
        await session.execute(
            text("SELECT count(*) FROM candidate_employments WHERE candidate_id = :cid"),
            {"cid": str(candidate_id)},
        )
    ).scalar_one()
    statuses = list(
        (
            await session.execute(
                text(
                    "SELECT status FROM bgv_verifications "
                    "WHERE tenant_id = :tid AND candidate_id = :cid"
                ),
                {"tid": str(tenant_id), "cid": str(candidate_id)},
            )
        )
        .scalars()
        .all()
    )
    return derive_status(
        background=background, employer_count=employer_count, statuses=statuses
    )


def blocking_message(status: str) -> str | None:
    """The sentence a recruiter sees, or None when nothing is blocked.

    One place, so the refusal reads the same in the API, in the pipeline and on
    the dashboard. Each message names the next action rather than only the
    problem: a gate that says "blocked" and stops has moved the work to the
    person least able to see why.
    """
    if status in OFFER_ALLOWED_STATUSES:
        return None
    if status == CANDIDATE_BGV_NOT_STARTED:
        return (
            "Background verification has not been started for this candidate. "
            "Open Background Verification on their profile and send a request "
            "to each previous employer before extending an offer."
        )
    if status == CANDIDATE_BGV_NOT_VERIFIED:
        return (
            "An employer reported that this candidate's employment details "
            "could not be confirmed. Review that response before extending an "
            "offer; if it was resolved, update the employer's verification."
        )
    return (
        "Background verification is still in progress. Every previous employer "
        "this candidate submitted has to be verified before an offer can be "
        "extended."
    )


async def offer_blocked(
    session: AsyncSession, *, tenant_id: uuid.UUID, candidate_id: uuid.UUID
) -> str | None:
    """The reason an offer cannot proceed, or None. Never a bare boolean.

    Returning the MESSAGE rather than a flag is the same choice
    `creation_blocked` makes: a caller cannot accidentally invent its own
    wording for a rule it did not implement.
    """
    return blocking_message(
        await candidate_status(session, tenant_id=tenant_id, candidate_id=candidate_id)
    )
