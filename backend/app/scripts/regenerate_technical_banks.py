"""Rebuild job technical question banks to the current grade-driven contract.

Needed whenever the bank shape changes — the count per grade, the question
angles, or (as of 2026-07-26) the rule that a question's `skill` must be a short
skill label rather than a whole JD sentence, because that label is rendered
verbatim as a Technical dimension name in the client-facing report.

Safety:
- A job with an assessment conversation already in flight is SKIPPED. Spec §5
  guarantees every candidate for a role answers the identical question set, so a
  bank is never swapped underneath a candidate mid-conversation.
- ``--deterministic`` skips the LLM entirely and builds the bank from the JD's
  declared skills. Use it when provider quota is exhausted, or when you want a
  reproducible bank; it is also far faster across a large catalogue.

Dry-run by default; pass ``--apply`` to persist.
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import delete, select

from app.core.db import get_session_factory, superadmin_scope
from app.models.assessment import AssessmentConversation, TechnicalQuestion
from app.models.job import Job
from app.services.functional_assessment import (
    _DEFAULT_RUBRIC,
    _question_fallback,
    generate_question_bank,
    technical_question_count,
)

MAX_SKILL_LABEL = 60


async def _deterministic_bank(session, job: Job) -> int:
    """Build the exact grade-sized bank from the JD, preserving question IDs.

    Conversation messages reference technical questions by UUID. Updating rows
    by ordinal keeps completed and active transcripts scoreable after a safe
    deterministic refresh.
    """
    required = technical_question_count(job.assessment_grade or "non_managerial")
    rows = _question_fallback(job, required)
    existing = (
        await session.execute(
            select(TechnicalQuestion)
            .where(TechnicalQuestion.job_id == job.id)
            .order_by(TechnicalQuestion.ordinal)
        )
    ).scalars().all()
    for index, row in enumerate(rows, 1):
        if index <= len(existing):
            question = existing[index - 1]
            question.ordinal = index
            question.skill = str(row["skill"])[:MAX_SKILL_LABEL]
            question.prompt = str(row["prompt"])
            question.rubric_json = row.get("rubric") or dict(_DEFAULT_RUBRIC)
            question.is_active = True
        else:
            session.add(
                TechnicalQuestion(
                    tenant_id=job.tenant_id,
                    job_id=job.id,
                    ordinal=index,
                    skill=str(row["skill"])[:MAX_SKILL_LABEL],
                    prompt=str(row["prompt"]),
                    rubric_json=row.get("rubric") or dict(_DEFAULT_RUBRIC),
                )
            )
    if len(existing) > len(rows):
        await session.execute(
            delete(TechnicalQuestion).where(
                TechnicalQuestion.id.in_([row.id for row in existing[len(rows):]])
            )
        )
    await session.flush()
    return len(rows)


async def run(*, apply: bool, deterministic: bool, only_stale: bool) -> dict[str, int]:
    factory = get_session_factory()
    async with factory() as session:
        async with superadmin_scope(session):
            in_flight = {
                row.job_id
                for row in (
                    await session.execute(
                        select(AssessmentConversation).where(
                            AssessmentConversation.status != "completed"
                        )
                    )
                ).scalars()
            }
            job_ids = [
                row.id for row in (await session.execute(select(Job).order_by(Job.created_at))).scalars()
            ]
            stale: set = set()
            if only_stale:
                stale = {
                    row.job_id
                    for row in (
                        await session.execute(select(TechnicalQuestion).where(TechnicalQuestion.skill != ""))
                    ).scalars()
                    if len(row.skill) > MAX_SKILL_LABEL
                }

    stats = {"regenerated": 0, "skipped_in_flight": 0, "skipped_fresh": 0, "failed": 0}
    for job_id in job_ids:
        if job_id in in_flight:
            stats["skipped_in_flight"] += 1
            continue
        if only_stale and job_id not in stale:
            stats["skipped_fresh"] += 1
            continue
        try:
            async with factory() as session:
                async with superadmin_scope(session):
                    job = await session.get(Job, job_id)
                    if deterministic:
                        await _deterministic_bank(session, job)
                    else:
                        await session.execute(
                            delete(TechnicalQuestion).where(
                                TechnicalQuestion.job_id == job_id
                            )
                        )
                        await session.flush()
                        await generate_question_bank(session, job)
                    if apply:
                        await session.commit()
                    else:
                        await session.rollback()
            stats["regenerated"] += 1
        except Exception as exc:  # noqa: BLE001 — one bad job must not stop the sweep
            stats["failed"] += 1
            print(f"FAILED job_id={job_id} {type(exc).__name__}: {exc}"[:300])
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="persist the rebuilt banks")
    parser.add_argument(
        "--deterministic",
        action="store_true",
        help="build from the JD without calling an LLM (use when quota is exhausted)",
    )
    parser.add_argument(
        "--only-stale",
        action="store_true",
        help="only rebuild banks that violate the current skill-label contract",
    )
    args = parser.parse_args()
    stats = asyncio.run(
        run(apply=args.apply, deterministic=args.deterministic, only_stale=args.only_stale)
    )
    mode = "applied" if args.apply else "dry run"
    print(f"[{mode}] " + ", ".join(f"{k}={v}" for k, v in stats.items()))
    return 1 if stats["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
