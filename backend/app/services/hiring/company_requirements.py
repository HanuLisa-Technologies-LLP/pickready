"""Gate 1: a client says something about itself before it can create a job.

WHAT "COMPANY HIRING REQUIREMENTS" IS IN THIS CODEBASE
-------------------------------------------------------
It is the Company Profile, and there is no second thing. The workflow document
describes a company-level statement of what this organisation is, gathered
behind guiding questions rather than an empty textbox, editable afterwards, and
read by the job setup that follows. `Company.about_company`, `work_life` and
`benefits_text` are exactly that: the sections a client authors once on
Customer Portal -> Company Profile, which every new job SNAPSHOTS at creation
and which the research agent can draft for a person to edit.

Building a second company-level questionnaire beside it would give one product
concept two implementations that immediately disagree about which one a job is
derived from, and the profile is already the one every job copies.

WHY THE GATE ASKS FOR `about_company` AND NOT ALL THREE SECTIONS
-----------------------------------------------------------------
`about_company` is the section that says what the organisation does, and it is
the one a job's public description is seeded from. Work Life and Benefits are
real fields a company may legitimately leave empty -- an organisation with no
benefits blurb is not an organisation that has told us nothing -- and requiring
them would refuse job creation over a section whose absence costs nothing
downstream. One required section, stated here once, so no caller invents a
second answer to "is the profile filled in".

WHY THE GATE ASKS THE TABLE
-----------------------------
It reads the `companies` row, not a completion flag on the tenant. The product
has been bitten once by a health check that asked a stamp while the rows behind
it were empty, and that cost 19 live jobs. A stamp can be true while the text
is blank; the text cannot.

Whitespace is not content. A profile holding three spaces has told a recruiter
nothing and would seed a job's About section with three spaces, so the check
strips before it decides.

WHY IT REFUSES AT CREATE AND NOT AT PUBLISH
---------------------------------------------
The workflow document is explicit that job CREATION is blocked. That is also the
useful place: the profile is what seeds the job's own narrative sections and
what the JD generator reads, so a job drafted without it has been drafted
against nothing and every downstream step would need re-doing. Refusing at
publish would let a recruiter write a whole JD first and then be told the work
rests on a page nobody has filled in.

A job created BEFORE the client wrote their profile stays created. The gate is
on the act, never a sweep over existing rows: retro-invalidating a live job
would close a posting candidates are already applying to.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company

__all__ = ["MISSING_MESSAGE", "is_complete", "creation_blocked"]

#: The refusal a recruiter reads. It names the page and what to write there,
#: because "job creation blocked" sends a recruiter to ask a colleague what is
#: wrong. Spec 11's rule for the credit gate applies here for the same reason:
#: loud, immediate, with the way out named.
MISSING_MESSAGE = (
    "Your organisation has not filled in its Company Profile yet, so there is "
    "nothing for this job to be built on. Open Company Profile and describe "
    "what your company does, then create the job."
)


async def is_complete(session: AsyncSession, tenant_id: uuid.UUID) -> bool:
    """True when this tenant's Company Profile says what the company does.

    A tenant with no `companies` row at all has never opened the page, which is
    the same answer as a row whose About section is empty: nothing has been
    said. Both are False rather than one being an error, because a recruiter
    reading the refusal takes the same action either way.
    """
    about = (
        await session.execute(
            select(Company.about_company)
            .where(Company.tenant_id == tenant_id)
            .limit(1)
        )
    ).scalars().first()
    return bool((about or "").strip())


async def creation_blocked(
    session: AsyncSession, tenant_id: uuid.UUID
) -> str | None:
    """The reason job creation is refused, or None when nothing blocks it.

    Returns a MESSAGE rather than a boolean so the caller cannot invent its own
    wording, which is how two routes end up telling a recruiter two different
    stories about the same missing page.
    """
    if await is_complete(session, tenant_id):
        return None
    return MISSING_MESSAGE
