"""Write the hidden assessment context through Sutra for saved, unlocked jobs.

    python -m app.scripts.backfill_assessment_context                  # dry run
    python -m app.scripts.backfill_assessment_context --apply \\
        --operator-email ops@example.com [--limit 10] [--job <uuid> ...]

DRY RUN IS THE DEFAULT. Without `--apply` nothing is written and no model is
called: the eligible jobs are listed with what a save would meet.

WHY THIS EXISTS
---------------
Migration 0118 kept every already-saved matrix READY for candidates (PLAN-p1
risk R1: an invitation outage on deploy) by stamping an HONEST EMPTY context,
`{"role_summary": "", "generated_by": "migration"}`, rather than inventing a
summary no model wrote. The demo and mock seeds and the report backfill stamp
the same shape under their own names. Those jobs work, and their contract has
no role summary and, for most, no evidence line a person could watch happen.
This fills both, through the product, when an operator chooses to spend the
model calls.

WHICH JOBS
----------
Every job that is not archived, whose skills are SAVED (the stamp AND a
context, `assessment_contract.skills_saved`), whose context was written by
anybody OTHER than Sutra, and which is NOT LOCKED. The last is not a filter of
convenience: a locked job's contract is the immutable snapshot a candidate
started against, and nothing here may move it; `skills.save` refuses a locked
job anyway, and it is excluded before the call so no model spend is wasted on
a refusal.

HOW, AND WHY IT IS `skills.save` RATHER THAN A SECOND WRITER
-------------------------------------------------------------
Each job goes through `skills.save`, the one implementation of Save Skills
(rule 5). So the backfill is held to every rule a person's press is: the set
must pass `validate_for_save` (a migrated bucket over the limit is REPORTED
with the count, never truncated, per 0118), Sutra writes the context in ONE
call BEFORE any row changes, a writer outage saves NOTHING, a refusal names
every skill it refused, and the two audit rows are written in one INSERT each.
A job re-saved this way gets Sutra's evidence lines and priorities, the saved
stamp moves, and a job saved before counts a revision (`criteria_version`),
exactly as a person's re-save would.

WHO THE AUDIT ROWS NAME
-----------------------
The OPERATOR who ran it, by `--operator-email`: an active PLATFORM super admin
(`Role.super_admin`, no tenant). `ck_audit_log_agent_has_principal` needs a
person behind Sutra's row, and the honest person is the one who pressed the
button. Naming the job's original saver or the tenant's Super Admin would
record a save they never made.

ONE TRANSACTION PER JOB, AND THE SCOPE IS RE-ENTERED EACH TIME
---------------------------------------------------------------
A save holds the job's skills lock until its commit and may wait on the
writer for the interactive budget, so jobs are never batched into one long
transaction. `superadmin_scope` sets transaction-local settings, so each
transaction enters it afresh (the 2026-09-16 probing mistake: a query after a
commit without it reads zero rows and looks like an empty database).

Exit status: 0 when every eligible job was listed (dry run) or saved; 1 when
any job was refused, so a partial run is never read as a clean one; 2 when the
operator cannot be resolved.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import get_session_factory, superadmin_scope
from app.models.assessment import JobCompetency
from app.models.enums import Role, UserStatus
from app.models.job import Job
from app.models.user import User
from app.services import assessment_contract, skills
from app.services.hiring import pipeline_halt

#: The writer's name on a context Sutra wrote. Anything else is a candidate.
SUTRA = "sutra"

#: Saved, not archived, not locked, and the context not Sutra's. Locked means
#: a snapshot row exists: the table, never a stamp (rule 8). `{only}` narrows
#: to named jobs when the operator passes `--job`.
_ELIGIBLE_SQL = """
    SELECT j.id, t.name AS tenant, j.title,
           COALESCE(j.assessment_context_json ->> 'generated_by', '') AS writer
      FROM jobs j
      JOIN tenants t ON t.id = j.tenant_id
     WHERE j.archived_at IS NULL
       AND j.framework_approved_at IS NOT NULL
       AND j.assessment_context_json IS NOT NULL
       AND COALESCE(j.assessment_context_json ->> 'generated_by', '') <> :sutra
       AND NOT EXISTS (SELECT 1 FROM job_skill_snapshots s WHERE s.job_id = j.id)
       {only}
     ORDER BY j.created_at, j.id
"""

#: What a save refuses with, each carrying the sentence a reviewer would read.
#: The same set the Save Skills route maps (`api/job_setup`).
_SAVE_REFUSALS = (
    skills.SkillsError,
    assessment_contract.SkillsLocked,
    pipeline_halt.PipelineHalted,
)


class OperatorRefused(RuntimeError):
    """The named operator is not an active platform super admin."""


@dataclass(frozen=True)
class Eligible:
    job_id: uuid.UUID
    tenant: str
    title: str
    writer: str


async def eligible_jobs(
    session: AsyncSession,
    *,
    limit: int | None = None,
    job_ids: tuple[uuid.UUID, ...] = (),
) -> list[Eligible]:
    params: dict[str, object] = {"sutra": SUTRA}
    only = ""
    if job_ids:
        only = "AND j.id = ANY(CAST(:ids AS uuid[]))"
        params["ids"] = [str(job_id) for job_id in job_ids]
    rows = (await session.execute(text(_ELIGIBLE_SQL.format(only=only)), params)).all()
    found = [Eligible(row.id, row.tenant, row.title, row.writer or "unnamed") for row in rows]
    return found if limit is None else found[:limit]


async def resolve_operator(session: AsyncSession, email: str) -> User:
    """The platform super admin the audit rows will name, or a refusal."""
    user = (
        await session.execute(
            select(User).where(
                User.email == email.strip().lower(),
                User.role == Role.super_admin,
                User.tenant_id.is_(None),
                User.status == UserStatus.active,
            )
        )
    ).scalars().first()
    if user is None:
        raise OperatorRefused(
            f"{email!r} is not an active platform super admin. The audit rows "
            "name the person who ran the backfill, so it must be one."
        )
    return user


async def _active_skills(session: AsyncSession, job_id: uuid.UUID) -> list[JobCompetency]:
    """The job's ACTIVE skill rows, the set a save would submit."""
    return list(
        (
            await session.execute(
                select(JobCompetency).where(
                    JobCompetency.job_id == job_id, JobCompetency.is_active.is_(True)
                )
            )
        ).scalars().all()
    )


async def _backfill_one(
    session: AsyncSession, job_id: uuid.UUID, operator_id: uuid.UUID | None, *, apply: bool
) -> tuple[bool, str]:
    """(saved, outcome) for one job, re-checking eligibility inside its own
    transaction: a person may have saved, or a candidate started, since the
    list was read."""
    job = await session.get(Job, job_id)
    if job is None or job.archived_at is not None:
        return False, "skipped: the job is gone or archived"
    if await assessment_contract.is_locked(session, job.id):
        return False, "skipped: locked since the list was read; the snapshot stands"
    if (job.assessment_context_json or {}).get("generated_by") == SUTRA:
        return False, "skipped: Sutra wrote this context since the list was read"
    problems = skills.validate_for_save(await _active_skills(session, job.id))
    if problems:
        return False, "NOT SAVED, the set cannot be saved as it stands: " + " ".join(problems)
    if not apply:
        return False, "would save through skills.save (one context call)"
    if operator_id is None:
        raise OperatorRefused("an apply needs a resolved operator; `run` resolves it first")
    try:
        await skills.save(
            session, job, actor_user_id=operator_id, actor_role=Role.super_admin.value
        )
    except _SAVE_REFUSALS as exc:
        detail = getattr(exc, "detail", None) or str(exc)
        return False, f"NOT SAVED ({type(exc).__name__}): {detail}"
    return True, "saved: Sutra wrote the context"


async def run(
    *,
    apply: bool,
    operator_email: str | None,
    limit: int | None = None,
    job_ids: tuple[uuid.UUID, ...] = (),
    factory: async_sessionmaker[AsyncSession] | None = None,
) -> tuple[int, list[str]]:
    """(exit status, one line per job). Prints nothing, so a test can read it."""
    sessions = factory or get_session_factory()
    #: The operator's ID, never the row: a rollback expires every ORM instance,
    #: and a row read after its session closed would reload from nowhere.
    operator_id: uuid.UUID | None = None
    async with sessions() as session:
        async with superadmin_scope(session):
            if apply:
                if not operator_email:
                    return 2, ["--apply needs --operator-email: the audit rows name a person"]
                try:
                    operator_id = (await resolve_operator(session, operator_email)).id
                except OperatorRefused as exc:
                    return 2, [str(exc)]
            found = await eligible_jobs(session, limit=limit, job_ids=job_ids)
        await session.rollback()

    lines: list[str] = []
    refused = 0
    for item in found:
        async with sessions() as session:
            async with superadmin_scope(session):
                saved, outcome = await _backfill_one(
                    session, item.job_id, operator_id, apply=apply
                )
                if saved:
                    await session.commit()
                else:
                    await session.rollback()
        if outcome.startswith("NOT SAVED"):
            refused += 1
        lines.append(f"{item.tenant} / {item.title} [{item.writer}]: {outcome}")
    if not found:
        lines.append("no saved, unlocked job carries a context Sutra did not write")
    return (1 if refused else 0), lines


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Write the hidden assessment context through Sutra for saved, "
            "unlocked jobs whose context Sutra did not write. Dry run unless "
            "--apply."
        )
    )
    parser.add_argument("--apply", action="store_true", help="Save through skills.save.")
    parser.add_argument(
        "--operator-email",
        help="The active platform super admin the audit rows will name. Required with --apply.",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="At most this many jobs, oldest first."
    )
    parser.add_argument(
        "--job",
        action="append",
        type=uuid.UUID,
        default=[],
        help="Only this job (repeatable). It must still be eligible.",
    )
    args = parser.parse_args()
    status, lines = asyncio.run(
        run(
            apply=args.apply,
            operator_email=args.operator_email,
            limit=args.limit,
            job_ids=tuple(args.job),
        )
    )
    for line in lines:
        print(line)
    if not args.apply:
        print("= DRY RUN: nothing was written and no model was called.")
    if status == 2:
        print(lines[0], file=sys.stderr)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
