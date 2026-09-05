"""Writing the candidate's Updates feed (workflow sections 14 and 15).

THE COPY IS A FIXED CATALOGUE, NOT A PROMPT
---------------------------------------------
Every title and body in this module is written here, once, in code. No model is
called and nothing is generated, for three reasons that all point the same way:

  * this is the surface that exists because the EMAIL might not arrive, and the
    email is the thing that already depends on a provider. A feed that failed
    the same way at the same time would protect nobody;
  * the copy is rendered to a candidate, so it is bound by the no-numbers rule
    and by the naming rules, and a fixed string can be checked once rather than
    guarded on every generation;
  * an update is a factual record of something that happened. It is not a place
    for tone.

WHAT MAY AND MAY NOT BE SAID
------------------------------
No score, no grade, no rank, no percentage, no count of other candidates.
`test_candidate_updates.py` sweeps every entry in the catalogue for digits, for
em dashes, and for the four grade words, because a rule enforced only at the
call site is a rule the next entry will break.

A stage change tells the candidate WHAT HAPPENED, never what it means about
their chances. "The team is reviewing your assessment" is a fact; "you did
well" is a grade in prose.

WRITING IS A SIDE EFFECT THAT MUST NEVER FAIL THE THING IT DESCRIBES
----------------------------------------------------------------------
`record` is called from inside transitions and route handlers that have already
done the real work. If writing the feed row raises, the candidate has been
moved, mailed, and told -- and the transaction rolls it all back over a
notification. So `record` raises only on a programming error (an unknown kind),
which is a bug rather than a runtime condition, and the caller does no
error handling because there is nothing to handle.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.candidate_update import CandidateUpdate

__all__ = [
    "KINDS",
    "TEMPLATES",
    "UpdateTemplate",
    "for_stage",
    "record",
    "APPLICATION_SUBMITTED",
    "ASSESSMENT_INVITED",
    "ASSESSMENT_STARTED",
    "ASSESSMENT_COMPLETED",
    "SHORTLISTED",
    "INTERVIEW_SCHEDULED",
    "INTERVIEW_COMPLETED",
    "OFFER_EXTENDED",
    "JOINED",
    "ON_HOLD",
    "NOT_PROCEEDING",
    "INVITED_TO_APPLY",
    "JOB_CLOSED",
]


# ── The kinds ────────────────────────────────────────────────────────────────

APPLICATION_SUBMITTED = "application_submitted"
ASSESSMENT_INVITED = "assessment_invited"
ASSESSMENT_STARTED = "assessment_started"
ASSESSMENT_COMPLETED = "assessment_completed"
SHORTLISTED = "shortlisted"
INTERVIEW_SCHEDULED = "interview_scheduled"
INTERVIEW_COMPLETED = "interview_completed"
OFFER_EXTENDED = "offer_extended"
JOINED = "joined"
ON_HOLD = "on_hold"
NOT_PROCEEDING = "not_proceeding"
#: A recruiter asked somebody in their databank to sign in and apply. The only
#: kind that can exist for a candidate with no application at all.
INVITED_TO_APPLY = "invited_to_apply"
#: The client filled the role and closed the posting.
JOB_CLOSED = "job_closed"


@dataclass(frozen=True)
class UpdateTemplate:
    """One entry in the catalogue. `title` and `body` take `{job}` and
    `{company}` and nothing else -- a template with more holes than that is a
    template somebody will eventually fill from candidate-supplied text."""

    kind: str
    title: str
    body: str
    #: Where in the portal this leads. `{link_id}` is substituted when the
    #: update belongs to one application.
    link_path: str | None = None


TEMPLATES: dict[str, UpdateTemplate] = {
    APPLICATION_SUBMITTED: UpdateTemplate(
        kind=APPLICATION_SUBMITTED,
        title="Application received",
        body=(
            "Your application for {job} at {company} is in. The hiring team "
            "has it and you will see every update on this page."
        ),
        link_path="/portal/applications?application={link_id}",
    ),
    ASSESSMENT_INVITED: UpdateTemplate(
        kind=ASSESSMENT_INVITED,
        title="You have been invited to an assessment",
        body=(
            "The team reviewed your application for {job} at {company} and "
            "would like you to complete an assessment. Your answers save as "
            "you go, so you can stop and come back."
        ),
        link_path="/portal/assessments",
    ),
    ASSESSMENT_STARTED: UpdateTemplate(
        kind=ASSESSMENT_STARTED,
        title="Assessment in progress",
        body=(
            "You have started the assessment for {job} at {company}. Your "
            "answers are saved, so you can finish it whenever suits you."
        ),
        link_path="/portal/assessments",
    ),
    ASSESSMENT_COMPLETED: UpdateTemplate(
        kind=ASSESSMENT_COMPLETED,
        title="Assessment received",
        body=(
            "Your assessment for {job} at {company} is complete and with the "
            "hiring team. There is nothing further for you to do right now."
        ),
        link_path="/portal/applications?application={link_id}",
    ),
    SHORTLISTED: UpdateTemplate(
        kind=SHORTLISTED,
        title="You are moving forward",
        body=(
            "You have been shortlisted for {job} at {company}. The team will "
            "be in touch about the next step."
        ),
        link_path="/portal/applications?application={link_id}",
    ),
    INTERVIEW_SCHEDULED: UpdateTemplate(
        kind=INTERVIEW_SCHEDULED,
        title="Interview scheduled",
        body=(
            "An interview has been arranged for {job} at {company}. The "
            "details are in the email the team sent you."
        ),
        link_path="/portal/applications?application={link_id}",
    ),
    INTERVIEW_COMPLETED: UpdateTemplate(
        kind=INTERVIEW_COMPLETED,
        title="Interview complete",
        body=(
            "Thank you for your time on the {job} interview at {company}. The "
            "team is considering it now."
        ),
        link_path="/portal/applications?application={link_id}",
    ),
    OFFER_EXTENDED: UpdateTemplate(
        kind=OFFER_EXTENDED,
        title="An offer has been made",
        body=(
            "{company} has extended an offer for {job}. The details are in "
            "the email the team sent you."
        ),
        link_path="/portal/applications?application={link_id}",
    ),
    JOINED: UpdateTemplate(
        kind=JOINED,
        title="Welcome aboard",
        body="Your application for {job} at {company} is complete. Congratulations.",
        link_path="/portal/applications?application={link_id}",
    ),
    ON_HOLD: UpdateTemplate(
        kind=ON_HOLD,
        title="Your application is on hold",
        body=(
            "{company} has paused the process for {job} for now. Your "
            "application stays where it is and the team will come back to you."
        ),
        link_path="/portal/applications?application={link_id}",
    ),
    NOT_PROCEEDING: UpdateTemplate(
        kind=NOT_PROCEEDING,
        title="Not proceeding with this application",
        body=(
            "{company} is not taking your application for {job} further. Your "
            "profile stays with you and you can apply for other roles at any "
            "time."
        ),
        link_path="/portal/applications?application={link_id}",
    ),
    INVITED_TO_APPLY: UpdateTemplate(
        kind=INVITED_TO_APPLY,
        title="A role you may be interested in",
        body=(
            "{company} has your resume on file and thinks {job} could suit "
            "you. You have not applied. If you would like to be considered, "
            "complete your profile and apply."
        ),
        # Deliberately the public application page rather than an application
        # of theirs: they do not have one, which is the entire point.
        link_path="/apply/{job_id}",
    ),
    JOB_CLOSED: UpdateTemplate(
        kind=JOB_CLOSED,
        title="This role has closed",
        body=(
            "{company} has closed the {job} role. Your application stays on "
            "your record and your profile is unaffected."
        ),
        link_path="/portal/applications?application={link_id}",
    ),
}

KINDS: tuple[str, ...] = tuple(TEMPLATES)


# ── Mapping a pipeline stage onto a kind ─────────────────────────────────────
#
# Not every stage produces an update, and the exceptions are deliberate:
#
#   `sourced`  a resume landing in a recruiter's databank is not an event the
#              candidate caused and not something they can act on. The
#              invitation, when the recruiter sends one, is what they hear
#              about, and it has its own kind.
#
# `assessment_in_progress` DOES produce one, unlike the email, which is
# deliberately suppressed there. The reasons differ: mailing somebody about
# something they just did is noise arriving in their inbox, whereas the same
# fact sitting in a feed is the answer to "did my assessment save?" -- which is
# the single most common thing a candidate wants to check.
_STAGE_KIND: dict[str, str] = {
    "applied": APPLICATION_SUBMITTED,
    "assessment_invited": ASSESSMENT_INVITED,
    "assessment_in_progress": ASSESSMENT_STARTED,
    "assessment_completed": ASSESSMENT_COMPLETED,
    "shortlisted": SHORTLISTED,
    "interview_scheduled": INTERVIEW_SCHEDULED,
    "interview_completed": INTERVIEW_COMPLETED,
    "offer_extended": OFFER_EXTENDED,
    # The retired synonym, still stored on historic rows.
    "offered": OFFER_EXTENDED,
    "joined": JOINED,
    "hold": ON_HOLD,
    "rejected": NOT_PROCEEDING,
}


def for_stage(status: str | None) -> str | None:
    """The update kind for a pipeline stage, or None when it produces none.

    Returning None rather than raising: a stage with no feed entry is a
    designed outcome (see `sourced` above), and a new stage added to the FSM
    should not take down every transition in the product until somebody
    remembers this table.
    """
    if not status:
        return None
    return _STAGE_KIND.get(str(status))


# ── Writing one ──────────────────────────────────────────────────────────────

def _fill(text: str, values: dict[str, Any]) -> str:
    """Substitute only the keys the catalogue declares.

    `str.format` on a template with an unexpected brace raises, and these
    strings are constants, so the only way this fails is a typo in this file.
    That is a programming error and should raise loudly in a test rather than
    be swallowed into a candidate-facing blank.
    """
    return text.format(**values)


async def record(
    session: AsyncSession,
    *,
    kind: str,
    candidate_id: uuid.UUID,
    job_title: str | None = None,
    company_name: str | None = None,
    tenant_id: uuid.UUID | None = None,
    job_id: uuid.UUID | None = None,
    link_id: uuid.UUID | None = None,
    emailed: bool = False,
) -> CandidateUpdate:
    """Write one feed row. Returns it, unflushed by design.

    The caller owns the transaction: this is always part of something larger
    (a transition, an application, a closure), and flushing here would split a
    unit of work that has to succeed or fail together.
    """
    template = TEMPLATES.get(kind)
    if template is None:
        # A programming error, not a runtime condition. Silently dropping it
        # would give the candidate an incomplete feed with nothing anywhere
        # recording the gap, which is the failure this whole table exists to
        # prevent, reintroduced one level down.
        raise KeyError(f"unknown candidate update kind: {kind!r}")

    values = {
        "job": job_title or "this role",
        "company": company_name or "the hiring team",
        "link_id": str(link_id) if link_id else "",
        "job_id": str(job_id) if job_id else "",
    }
    path = template.link_path
    if path is not None:
        needs_link = "{link_id}" in path and link_id is None
        needs_job = "{job_id}" in path and job_id is None
        # A link that leads to a page built from a missing identifier is worse
        # than no link: it renders as an affordance and then 404s.
        path = None if (needs_link or needs_job) else _fill(path, values)

    row = CandidateUpdate(
        candidate_id=candidate_id,
        tenant_id=tenant_id,
        job_id=job_id,
        job_candidate_link_id=link_id,
        kind=template.kind,
        title=template.title,
        body=_fill(template.body, values),
        link_path=path,
        emailed=emailed,
    )
    session.add(row)
    return row
