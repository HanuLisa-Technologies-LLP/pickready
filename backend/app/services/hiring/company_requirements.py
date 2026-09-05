"""Gate 1: a client states its hiring requirements before it can create a job.

WHAT "COMPANY HIRING REQUIREMENTS" IS IN THIS CODEBASE
-------------------------------------------------------
It is the Company DNA artifact, and there is no second thing. The workflow
document describes a company-level statement of what this organisation
considers a strong hire, gathered behind guiding questions rather than an empty
textbox, editable afterwards, and used as an input to matching and ranking.
`services/hiring/company_dna.py` is exactly that instrument: twelve sections,
forced trade-off scales instead of "we value excellence", and an observable
evidence detector that refuses an adjective and asks again.

Building a second free-text field beside it would give one product concept two
implementations that immediately disagree about which one Sutra reads, and
Sutra reads the COMPILED artifact by design (an unbounded client-authored
string in a prompt that decides what every candidate is graded on is an
injection surface). So this module gates on the artifact that already exists.

WHY THE GATE ASKS THE TABLE
-----------------------------
The completion of a Company DNA session is `status = complete` on the CURRENT
row, which is the same question `api/company_dna._current_complete` asks. It is
not a timestamp on the tenant, deliberately: the product has been bitten once
by a health check that asked a stamp while the rows behind it were empty, and
that cost 19 live jobs.

WHY IT REFUSES AT CREATE AND NOT AT PUBLISH
---------------------------------------------
The workflow document is explicit that job CREATION is blocked. That is also the
useful place: the requirements are what the JD generator, the SWOT session and
the scorecard derivation all read, so a job drafted without them has been
drafted against nothing and every downstream step would need re-doing. Refusing
at publish would let a recruiter write a whole JD first and then be told the
work rests on an artifact that does not exist.

A job created BEFORE the client completed their DNA stays created. The gate is
on the act, never a sweep over existing rows: retro-invalidating a live job
would close a posting candidates are already applying to.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.hiring import CompanyDNA
from app.schemas.company_dna import STATUS_COMPLETE

__all__ = ["MISSING_MESSAGE", "is_complete", "creation_blocked"]

#: The refusal a recruiter reads. It names the artifact, says who completes it
#: and where, because "job creation blocked" sends a recruiter to ask a
#: colleague what is wrong. Spec §11's rule for the credit gate applies here for
#: the same reason: loud, immediate, with the way out named.
MISSING_MESSAGE = (
    "Your organisation has not completed its Company Hiring Requirements yet, "
    "so there is nothing for this job to be evaluated against. Complete the "
    "Company DNA session under Company Hiring Requirements, then create the job."
)


async def is_complete(session: AsyncSession, tenant_id: uuid.UUID) -> bool:
    """True when this tenant has a CURRENT, COMPLETED Company DNA row.

    A draft in progress is not completion: the compiled artifact only exists
    once the session closes, and a half-answered instrument would hand Sutra a
    document with sections missing rather than sections answered.
    """
    row = (
        await session.execute(
            select(CompanyDNA.id)
            .where(
                CompanyDNA.tenant_id == tenant_id,
                CompanyDNA.is_current.is_(True),
                CompanyDNA.status == STATUS_COMPLETE,
            )
            .limit(1)
        )
    ).first()
    return row is not None


async def creation_blocked(
    session: AsyncSession, tenant_id: uuid.UUID
) -> str | None:
    """The reason job creation is refused, or None when nothing blocks it.

    Returns a MESSAGE rather than a boolean so the caller cannot invent its own
    wording, which is how two routes end up telling a recruiter two different
    stories about the same missing artifact.
    """
    if await is_complete(session, tenant_id):
        return None
    return MISSING_MESSAGE
