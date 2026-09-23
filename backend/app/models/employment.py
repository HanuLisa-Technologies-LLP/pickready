"""The candidate's own employment history, submitted once and then final.

WHOSE DATA THIS IS, AND WHY THAT DECIDES THE SCHEMA
----------------------------------------------------
A person's employment history is a fact about the PERSON, not about whichever
company is currently hiring them. So it hangs off `candidates` with a NULL
tenant, exactly like `bgv_inquiries` and for the same reason: the candidate
carries it between applications and between customers, and a databank profile
is visible to more than one tenant.

VERIFICATION is the opposite: it is a fact about one TENANT's hiring decision,
it is recorded per tenant in `bgv_verifications`, and one customer's result
never silently clears another customer's diligence. The two live in different
tables because they have different owners, and that is the whole design.

THIS IS NOT `bgv_inquiries`, AND THE DIFFERENCE IS THE POINT
--------------------------------------------------------------
`bgv_inquiries` (migration 0085) is the CANDIDATE's own verification of their
history: departmental mailboxes, the candidate triggers it, the reply is parsed
automatically, and a tenant sees the result only through an explicit
`bgv_share_consents` row. It gates nothing, by design.

What this table feeds is the RECRUITER's verification: a named HR contact per
employer, the recruitment team sends and reviews, a human marks the outcome,
and the result BLOCKS an offer. Folding both onto one table would put two
ownership models and two sets of rules on one row, which is the dual-code-path
problem restated as a schema.

IMMUTABILITY IS A DATABASE FACT, NOT A DISABLED BUTTON
--------------------------------------------------------
`candidates.employment_history_finalized_at` is the gate. Once stamped, the
service layer refuses every write to these rows, and a Postgres trigger refuses
them too (migration 0095) so a future route, a script or a psql session cannot
quietly rewrite what an employer is being asked to confirm. The candidate is
warned before submitting, in those words, because a one-way door that is not
announced is a trap rather than a guarantee.

A row is never deleted either: an employer that could be removed after the fact
would let a candidate drop the job they did not want verified, which is the
single thing this whole workflow exists to detect.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin

# ── Is background verification required at all ───────────────────────────────
#
# Asked once, on the candidate's own profile, and answered in their own words
# rather than inferred from a resume: a parsed work history is evidence, not a
# declaration, and this answer decides whether an offer can be blocked.

BACKGROUND_FRESHER = "fresher"
BACKGROUND_EXPERIENCED = "experienced"

EMPLOYMENT_BACKGROUNDS: frozenset[str] = frozenset(
    {BACKGROUND_FRESHER, BACKGROUND_EXPERIENCED}
)

#: No ceiling on employers, deliberately. The brief's worked example has seven
#: and a real senior candidate can have more; a cap would silently truncate
#: somebody's history and produce a verification that looks complete while
#: missing a job. `bgv_inquiries.MAX_INQUIRIES_PER_CANDIDATE` is 2 because that
#: is a different feature with a different owner, and it stays 2.


class CandidateEmployment(Base, UUIDPKMixin, CreatedAtMixin):
    """One previous employer, as the CANDIDATE stated it.

    Every field here is candidate-provided and must be read as a claim, never
    as a confirmed fact. `bgv_verifications` is where a confirmation lives, and
    nothing in this row is ever rewritten by a reply: an employer who says the
    dates are wrong produces a NOT_VERIFIED decision, not an edit, or the
    discrepancy the verification found would be erased by recording it.
    """

    __tablename__ = "candidate_employments"
    __table_args__ = (
        Index("ix_candidate_employments_candidate", "candidate_id", "started_on"),
    )

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidates.id", ondelete="CASCADE"),
        nullable=False,
    )
    employer_name: Mapped[str] = mapped_column(String(200), nullable=False)
    designation: Mapped[str] = mapped_column(String(200), nullable=False)
    started_on: Mapped[date] = mapped_column(Date, nullable=False)
    #: NOT nullable. This table holds PREVIOUS employers, and an open-ended row
    #: would ask an HR contact to confirm an end date the candidate never gave.
    #: A current employer is not submitted here; the database CHECK also
    #: refuses an end before a start, because "employed from 2024 to 2019" is a
    #: typo somebody would otherwise be asked to verify.
    ended_on: Mapped[date] = mapped_column(Date, nullable=False)
    #: The named person the verification goes to. A named contact rather than a
    #: departmental mailbox is what separates this from `bgv_inquiries`: the
    #: recruiter is asking a specific person to confirm a specific claim.
    hr_name: Mapped[str] = mapped_column(String(200), nullable=False)
    hr_email: Mapped[str] = mapped_column(String(320), nullable=False)


class EmploymentHistoryLocked(RuntimeError):
    """A write was attempted after final submission.

    Raised by the service layer so a route can answer 409 with a sentence the
    candidate can act on. The database refuses the same write independently;
    this exists so the refusal has an explanation, not so it has a gate.
    """


def finalized(candidate) -> bool:
    """Whether this candidate's employment history is closed to edits.

    One reading of the stamp, shared by every caller, so a route cannot invent
    a second definition of "submitted".
    """
    return getattr(candidate, "employment_history_finalized_at", None) is not None


def bgv_required(candidate) -> bool:
    """Whether an offer to this candidate must wait for verification.

    NOT the same question as "has this candidate any employment rows". A
    candidate who has answered `fresher` is never blocked, and a candidate who
    has not answered at all is not blocked either: the absence of an answer is
    an unanswered question, and refusing an offer over it would let an
    unfinished profile field end somebody's candidacy. What blocks is an
    explicit `experienced`, which is a statement the candidate made.
    """
    return getattr(candidate, "employment_background", None) == BACKGROUND_EXPERIENCED
