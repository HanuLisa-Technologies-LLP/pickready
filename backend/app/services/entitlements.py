"""What a company may START while its credit pool reads what it reads.

Owner spec (2026-09-29), sections 2.10 and 25. The first successful credit
purchase activates a company, and from then on ACCESS IS NOT CREDIT: a
company at zero still signs in, reads its jobs, candidates and reports,
manages its team and buys more credits. What an empty (or short) pool stops
is new work that will cost credits, and only at the moment that work is
committed. Work already underway follows the existing hold and release logic
(`pickready.run_functional_assessment` holds a finished assessment's report
until a top-up, and `pickready.release_held_assessments` releases it).

ONE QUESTION, ONE ANSWER
------------------------
`restriction_reason(session, tenant_id, action)` answers every gate, and it
answers with the SENTENCE the caller shows or None. Callers never re-derive a
refusal from a balance, so a refusal cannot be worded two ways for one
situation, and the balance arithmetic stays in `services/credits`
(`has_positive_balance`, `can_start_assessment`), which this wraps and never
repeats. Reads are never gated, so there is no read action here at all:
a route that reads does not ask.

THE ACTIONS
-----------
* `create_job`: starting a new job (and drafting its JD). Refused at zero.
* `send_assessment`: inviting `count` candidates, one question for the whole
  batch at the job's role rate. Refused when the pool does not hold the full
  cost, with the shortfall named.
* `start_assessment`: the candidate's own first START, asked again because
  two batches that each fit the balance are not jointly covered by it. The
  sentence is the CANDIDATE's and deliberately names no balance, credit or
  billing state of the employer.
* `billable_ai_work`: any other AI run that will be charged, such as scoring
  a finished assessment into its report. Refused at zero; the worker HOLDS
  rather than fails, and the release sweep picks it up after a top-up.

A demonstration tenant is never refused (`credits.is_demo_tenant`, inside
the wrapped functions), exactly as before.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import ROLE_STEM
from app.services import credits

ACTION_CREATE_JOB = "create_job"
ACTION_SEND_ASSESSMENT = "send_assessment"
ACTION_START_ASSESSMENT = "start_assessment"
ACTION_BILLABLE_AI_WORK = "billable_ai_work"

ACTIONS: frozenset[str] = frozenset(
    {
        ACTION_CREATE_JOB,
        ACTION_SEND_ASSESSMENT,
        ACTION_START_ASSESSMENT,
        ACTION_BILLABLE_AI_WORK,
    }
)

#: The refusal at job creation and JD generation (spec §11).
CREATE_JOB_DETAIL = (
    "Your credit pool is exhausted, so new jobs cannot be created. "
    "Purchase a credit bundle to continue creating jobs and "
    "assessing candidates."
)

#: The refusal a CANDIDATE reads at the first start. It names no balance and
#: no billing state: that is the employer's business, and the candidate can
#: do nothing about it but try later or ask the hiring team.
START_ASSESSMENT_DETAIL = (
    "This assessment cannot be started right now. Please try again later, and "
    "contact the hiring team if this continues."
)

#: The refusal for any other charged AI run.
BILLABLE_AI_WORK_DETAIL = (
    "Your credit pool is exhausted, so new AI work that uses credits cannot "
    "start. Purchase credits to continue. Work already in progress is kept "
    "and resumes after a top-up."
)


def shortfall_detail(
    *, count: int, role_classification: str | None, required: Decimal, balance: Decimal
) -> str:
    """The one sentence a short invitation batch is refused with: how many,
    at what rate, the balance and the gap, and that nobody was invited."""
    role_word = "STEM" if role_classification == ROLE_STEM else "Non-STEM"
    per_report = (required / count).quantize(Decimal("0.01"))
    short = (required - balance).quantize(Decimal("0.01"))
    people = "1 candidate" if count == 1 else f"{count} candidates"
    return (
        f"Insufficient credits. Inviting {people} to this {role_word} role "
        f"requires {required} credits ({per_report} per assessment). Current "
        f"balance: {balance} credits, {short} short. Nobody was invited. Top "
        "up, or invite fewer candidates."
    )


async def restriction_reason(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    action: str,
    *,
    role_classification: str | None = None,
    count: int = 1,
) -> str | None:
    """None when `action` may proceed now; otherwise the sentence to show.

    `role_classification` and `count` matter to the two assessment actions
    only: each completed assessment consumes one credit, and an invitation
    batch is asked about as a whole.
    An unknown action RAISES: a typo here would otherwise read as "allowed".
    """
    if action not in ACTIONS:
        raise ValueError(f"unknown entitlement action: {action!r}")

    if action in (ACTION_CREATE_JOB, ACTION_BILLABLE_AI_WORK):
        if await credits.has_positive_balance(session, tenant_id):
            return None
        return (
            CREATE_JOB_DETAIL if action == ACTION_CREATE_JOB else BILLABLE_AI_WORK_DETAIL
        )

    allowed, required, balance = await credits.can_start_assessment(
        session,
        tenant_id,
        role_classification=role_classification,
        count=count,
    )
    if allowed:
        return None
    if action == ACTION_START_ASSESSMENT:
        return START_ASSESSMENT_DETAIL
    return shortfall_detail(
        count=count,
        role_classification=role_classification,
        required=required,
        balance=balance,
    )
