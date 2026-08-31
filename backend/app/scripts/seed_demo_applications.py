"""Put the 30 demonstration candidates onto the demo tenants' jobs, for real.

    python -m app.scripts.seed_demo_applications [--dry-run] [--rank]

WHY THIS EXISTS
---------------
`seed_demo_candidates` creates candidate rows and uploads their resumes. That
is ALL it does, by its own docstring: "If a candidate is missing it is created;
nothing else is touched." It was written to fix a different defect (the corpus
never reached the image) and it fixed that one completely.

What it never did -- and was never written to do -- is create an APPLICATION.
Measured on production 2026-08-05, with RLS bypassed so the counts are real:

    candidates                  32
    job_candidate_links          9   (ACRM 1, Sarkar 5, Specter 0, Workify 3)

Thirty demonstration candidates existed and nine applications existed, so every
demo company's candidate table was empty or near enough. The seed "ran
successfully" on every deploy while being structurally incapable of producing
the thing anyone was checking for. A script that exits 0 is not evidence that
the row you wanted is in the table.

THE SECOND, LARGER HOLE
-----------------------
The same measurement found that 30 of the 33 demo jobs had ZERO competencies:

    Sarkar / AI Generative AI Engineer   5 / 5 / 5   framework approved
    Sarkar / Prompt Engineer             5 / 5 / 5   framework approved
    Sarkar / Python Backend Developer    5 / 5 / 5   framework approved
    every other demo job                 0 / 0 / 0   NOT approved

`pickready.generate_ppi_framework`
had evidently never run for them. So even with applications seeded, those jobs
answer 409 to `select-candidates` and nobody can be invited. Seeding the
applications alone would have produced a demo that still did not work, and a
second round of "it says it is done but it is not".

So this script closes all three gaps in one pass, in dependency order:

    1. Generate a PPI framework for any demo job that has none.
    2. Approve it -- DEMO TENANTS ONLY, see below.
    3. Create the applications.

WHY APPROVING IS SAFE HERE, AND ONLY HERE
-----------------------------------------
The framework review gate is the product's only comparability guarantee: it is
the fixed criteria every candidate on a job is graded against, and a report
states a grade against those exact criteria. Auto-approving it is a real
concession, so it is scoped exactly like the billing exemption it mirrors: to
`tenants.is_demo`, read from the COLUMN, never a UUID list and never a name
match. Workify Corp is a REAL tenant and keeps its manual gate, which is why
every loop below filters on `is_demo` rather than on the three seed UUIDs.

The framework being approved is still an honest statement. `generate_framework`
pads deterministically to MINIMUM_PER_CATEGORY on an LLM outage, and this script
re-checks `framework_is_complete` before stamping anything -- so a framework is
never approved unless it would have passed the same check a human's Save press
goes through.

IDEMPOTENT, AND THAT IS LOAD-BEARING
------------------------------------
Thirty LLM framework generations over a network is exactly the kind of thing
that fails halfway. Every step below is skip-if-present:

  * a job with competencies keeps them (`generate_framework` is idempotent by
    default; `replace=False` is never overridden here)
  * a job already stamped `framework_approved_at` is left alone, so a framework
    a human edited and saved is never re-approved underneath them
  * an application is keyed on (job_id, candidate_id), which is UNIQUE in the
    schema, so a re-run adds nothing

It only ever ADDS. There is no delete and no reconciliation: the demo fixtures
are permanent, and a script that could remove them is a script that could
remove them by accident.

RANKING IS OPT-IN, DELIBERATELY
-------------------------------
`--rank` dispatches `pickready.run_matching` per job. It is OFF by default
because 33 jobs times 30 candidates is a large LLM re-rank bill and a thundering
herd against the worker pool, and the applications are visible in the recruiter's
table without it. When it is off, this script SAYS so in its output rather than
leaving the reader to assume the candidates arrived ranked.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select

from app.core.db import superadmin_scope
from app.models.candidate import Candidate, JobCandidateLink, Profile
from app.models.enums import LinkSource
from app.models.job import Job
from app.models.tenant import Tenant
from app.services import ppi
from app.services.application_validation import MANDATORY_KEYS
from app.workers.celery_app import celery_app

#: The three procurement types, cycled so the demo exercises the filter on the
#: candidate table. Nothing branches on `source_type` (claude.md): all three are
#: parsed, embedded, matched and assessed identically, so this is presentation
#: variety and nothing more.
SOURCE_TYPES = ("applied", "sourced", "databank")

#: Answers to the six MANDATORY application fields. Cycled so a recruiter
#: opening two candidates does not see the same CTC twice, which is what makes
#: the Validation section of a report look real in a demonstration.
#:
#: These are the CURRENT six fields (services/application_validation). The
#: 40-aspect profile form they replaced on 2026-07-30 is not collected here and
#: is not what the report's Validation section reads any more.
_VALIDATION_SAMPLES: tuple[dict[str, str], ...] = (
    {
        "current_ctc": "18,00,000 INR",
        "expected_ctc": "24,00,000 INR",
        "notice_period": "30 days",
        "joining_date": "2026-09-15",
        "document_readiness": "All documents ready",
        "role_interest": (
            "The team owns the product end to end rather than handing work "
            "across a wall, and that is how I want to work at this stage of my "
            "career."
        ),
    },
    {
        "current_ctc": "12,50,000 INR",
        "expected_ctc": "17,00,000 INR",
        "notice_period": "60 days",
        "joining_date": "2026-10-01",
        "document_readiness": "All documents ready",
        "role_interest": (
            "I have spent two years on the same stack this role is built on and "
            "want a place where that depth is used rather than treated as "
            "interchangeable."
        ),
    },
    {
        "current_ctc": "26,00,000 INR",
        "expected_ctc": "32,00,000 INR",
        "notice_period": "90 days",
        "joining_date": "2026-11-01",
        "document_readiness": "Some documents pending",
        "role_interest": (
            "The scope covers the parts of the system I have only ever "
            "consumed, and I would rather learn them here than read about them."
        ),
    },
    {
        "current_ctc": "9,00,000 INR",
        "expected_ctc": "14,00,000 INR",
        "notice_period": "Immediate",
        "joining_date": "2026-08-20",
        "document_readiness": "All documents ready",
        "role_interest": (
            "I am looking for a first role where the work is reviewed properly, "
            "and the description is specific enough that I believe it will be."
        ),
    },
)


def _validation_for(index: int) -> dict[str, Any]:
    """One complete, valid answer set. Complete is the point.

    `missing_fields` refuses a partial payload, so a sample that dropped a key
    would produce applications the product itself considers invalid -- visible
    in the report's Validation section as a blank the recruiter cannot explain.
    """
    sample = dict(_VALIDATION_SAMPLES[index % len(_VALIDATION_SAMPLES)])
    missing = [key for key in MANDATORY_KEYS if not sample.get(key)]
    if missing:  # pragma: no cover  -  a sample edited badly
        raise ValueError(f"validation sample is missing {missing}")
    return sample


async def _demo_tenants(session) -> list[Tenant]:
    """Demonstration tenants, from the COLUMN.

    Never a UUID list in Python and never a name match: `is_demo` is a column
    precisely so a fourth demo tenant is an UPDATE rather than a code change,
    and so Workify Corp -- a REAL tenant whose name sorts alongside the demo
    ones -- can never be swept in by a string comparison.
    """
    return list(
        (
            await session.execute(
                select(Tenant).where(Tenant.is_demo.is_(True)).order_by(Tenant.name)
            )
        ).scalars().all()
    )


async def _demo_candidates(session) -> list[tuple[Candidate, uuid.UUID | None]]:
    """The shared Databank corpus, each with its most recent profile.

    `tenant_id IS NULL` is what makes a candidate a Databank row visible to
    every tenant, which is exactly the set `seed_resume_corpus` creates. The
    profile carries the resume, so the link points at it: an application is an
    immutable snapshot of the resume it was sent with, and a link with no
    profile would render a candidate with no resume to open.
    """
    candidates = list(
        (
            await session.execute(
                select(Candidate)
                .where(Candidate.tenant_id.is_(None))
                .order_by(Candidate.email)
            )
        ).scalars().all()
    )
    rows: list[tuple[Candidate, uuid.UUID | None]] = []
    for candidate in candidates:
        profile_id = (
            await session.execute(
                select(Profile.id)
                .where(Profile.candidate_id == candidate.id)
                .order_by(Profile.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        rows.append((candidate, profile_id))
    return rows


async def _open_jobs(session, tenant_id: uuid.UUID) -> list[Job]:
    """Live, non-archived jobs for one tenant, oldest first for a stable order."""
    return list(
        (
            await session.execute(
                select(Job)
                .where(Job.tenant_id == tenant_id, Job.archived_at.is_(None))
                .order_by(Job.created_at, Job.id)
            )
        ).scalars().all()
    )


async def _ensure_framework(session, job: Job, dry_run: bool) -> str:
    """Approve this job's Tatva matrix, if Sutra has already built one.

    CHANGED 2026-08-29. This used to CALL the matrix generator and then approve
    what came back. It no longer generates anything, and the reason is spec-doc6
    §4.3's own: a matrix is built from Bodha's SWOT session with the Hiring
    Manager and the client's compiled Company DNA, neither of which a seed
    script has. The old single-pass generator would produce one from the JD
    alone, which is what made this call look reasonable.

    So a demo job with no matrix is REPORTED and left alone. The approval half
    stays, because a demo tenant genuinely does need its matrices approved
    without a human present, and `matrix_is_complete` is the same check the
    Hiring Manager's Save press goes through.
    """
    if job.framework_approved_at is not None:
        return "already approved"

    rows = await ppi.load_framework(session, job.id)
    if not rows:
        return (
            "NO MATRIX: run the SWOT session for this job and let Sutra build "
            "one. A seed script has neither the hiring manager nor the "
            "company's philosophy to build it from."
        )
    ok, reason = ppi.matrix_is_complete(
        list(rows), job.assessment_grade, job.role_classification
    )
    if not ok:
        return f"INCOMPLETE, left pending: {reason}"
    if dry_run:
        return f"would approve ({len(rows)} competencies)"

    job.framework_approved_at = datetime.now(timezone.utc)
    # Mirrors api/assessments._refresh_setup_status, which is the one place that
    # normally moves this column. Kept in step by hand here because importing an
    # API-layer helper into a script would drag its request dependencies along.
    job.assessment_status = "ready_for_candidates"
    await session.flush()
    return f"approved ({len(rows)} competencies)"


async def _run(dry_run: bool, rank: bool) -> int:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import get_settings

    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    created = 0
    skipped = 0
    frameworks: list[str] = []
    ranked_jobs: list[uuid.UUID] = []

    async with factory() as session:
        async with superadmin_scope(session):
            tenants = await _demo_tenants(session)
            if not tenants:
                print(
                    "No tenant has is_demo set. Nothing to seed. This is a "
                    "refusal, not a success: the demo tenants are seeded rows "
                    "and their absence means the database is not the one you "
                    "think it is.",
                    file=sys.stderr,
                )
                return 1

            candidates = await _demo_candidates(session)
            if not candidates:
                print(
                    "No Databank candidates found (candidates.tenant_id IS "
                    "NULL). Run `python -m app.scripts.seed_demo_candidates` "
                    "first; this script links candidates, it does not create "
                    "them.",
                    file=sys.stderr,
                )
                return 1

            print(f"Demo tenants: {', '.join(t.name for t in tenants)}")
            print(f"Databank candidates: {len(candidates)}")
            print()

            for tenant in tenants:
                jobs = await _open_jobs(session, tenant.id)
                if not jobs:
                    print(f"{tenant.name}: no jobs, skipped")
                    continue

                print(f"{tenant.name}: {len(jobs)} job(s)")
                for job in jobs:
                    outcome = await _ensure_framework(session, job, dry_run)
                    frameworks.append(f"  {tenant.name} / {job.title}: {outcome}")

                # Every candidate applies to this tenant, spread round-robin
                # across its jobs. Round-robin rather than random so a re-run
                # against a fresh database produces the same distribution, and
                # so no job is left with nobody in its table.
                for index, (candidate, profile_id) in enumerate(candidates):
                    job = jobs[index % len(jobs)]
                    exists = (
                        await session.execute(
                            select(func.count())
                            .select_from(JobCandidateLink)
                            .where(
                                JobCandidateLink.job_id == job.id,
                                JobCandidateLink.candidate_id == candidate.id,
                            )
                        )
                    ).scalar_one()
                    if exists:
                        skipped += 1
                        continue
                    if dry_run:
                        created += 1
                        continue

                    link = JobCandidateLink(
                        tenant_id=tenant.id,
                        job_id=job.id,
                        candidate_id=candidate.id,
                        profile_id=profile_id,
                        # The ENUM column: how the candidate reached the job.
                        # Every one of these came from the shared corpus, so
                        # `databank` is the truthful value. `source_type` below
                        # answers a different question (procurement type) and
                        # is what the recruiter's filter reads.
                        source=LinkSource.databank,
                        source_type=SOURCE_TYPES[index % len(SOURCE_TYPES)],
                        application_source="direct",
                        # `applied` is the entry stage of the 10-step pipeline.
                        # Deliberately NOT written further along: every later
                        # stage carries a promise (`assessment_completed` means
                        # a report exists), and only `apply_transition` may
                        # move a link, so inventing a stage here would produce
                        # a link whose status the product cannot honour.
                        status="applied",
                        status_updated_at=datetime.now(timezone.utc)
                        - timedelta(days=index % 7),
                        validation_json=_validation_for(index),
                    )
                    session.add(link)
                    created += 1

                if rank:
                    ranked_jobs.extend(job.id for job in jobs)

            if dry_run:
                await session.rollback()
            else:
                await session.commit()

    print()
    print("Frameworks:")
    for line in frameworks:
        print(line)
    print()
    print(
        f"= applications: {created} created, {skipped} already present"
        f"{' (DRY RUN, nothing written)' if dry_run else ''}"
    )

    if rank and not dry_run:
        for job_id in ranked_jobs:
            celery_app.send_task("pickready.run_matching", args=[str(job_id)])
        print(f"= dispatched run_matching for {len(ranked_jobs)} job(s)")
    else:
        # Said out loud rather than left to inference. A candidate table with no
        # rating reads as a broken ranker; it is simply work that was not asked
        # for on this run.
        print(
            "= ranking NOT run. Candidates are visible and applied, but carry "
            "no match score or remarks. Re-run with --rank to dispatch it."
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Seed applications from the demonstration candidate corpus onto "
            "every demo tenant's jobs, generating and approving each job's PPI "
            "framework first."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change and write nothing.",
    )
    parser.add_argument(
        "--rank",
        action="store_true",
        help=(
            "Also dispatch pickready.run_matching per job. Off by default: it "
            "is a large LLM re-rank across every job and candidate."
        ),
    )
    args = parser.parse_args()
    return asyncio.run(_run(args.dry_run, args.rank))


if __name__ == "__main__":
    raise SystemExit(main())
