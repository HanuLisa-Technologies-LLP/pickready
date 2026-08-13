"""Backfill demo question banks, PPI frameworks and PPI Assessment Reports.

The command is idempotent. It only auto-finalizes jobs and creates synthetic
reports for the seeded `@candidates.pickready.test` corpus; real candidates
always use the approved live conversation and LangGraph pipeline.
"""
from __future__ import annotations

import argparse
import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select, text

from app.core.db import get_session_factory
from app.models.assessment import (
    FunctionalSkillsReport,
    JobCompetency,
    ReportDimension,
)
from app.models.candidate import Candidate, JobCandidateLink, Profile
from app.models.job import Job
from app.services import ppi
from app.services.application_validation import VALIDATION_FIELDS
from app.services.functional_assessment import (
    MATCHING_DIMENSIONS,
    _fallback_remark_25,
    _fallback_remark_45,
    _stable_score,
    infer_grade_fallback,
)

MOCK_EMAIL_SUFFIX = "@candidates.pickready.test"


async def backfill(apply: bool) -> dict[str, int]:
    counts = {"jobs": 0, "questions": 0, "reports": 0, "dimensions": 0}
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(text("SELECT set_config('app.bypass_rls', 'on', false)"))
        jobs = (await session.execute(select(Job).where(Job.archived_at.is_(None)))).scalars().all()
        mock_candidates = (
            await session.execute(
                select(Candidate).where(Candidate.email.ilike(f"%{MOCK_EMAIL_SUFFIX}"))
            )
        ).scalars().all()
        mock_ids = {candidate.id for candidate in mock_candidates}

        for job in jobs:
            links = (
                await session.execute(
                    select(JobCandidateLink).where(
                        JobCandidateLink.job_id == job.id,
                        JobCandidateLink.candidate_id.in_(mock_ids),
                        JobCandidateLink.archived_at.is_(None),
                    )
                )
            ).scalars().all()
            if not links:
                continue
            counts["jobs"] += 1
            # Technical questions are per CANDIDATE as of 2026-08-06, so they
            # are created per link below rather than once per job here. The
            # per-job block that used to live at this point built preset
            # `TechnicalQuestion` rows from a JD-derived fallback bank; there is
            # no such bank any more.

            grade = job.assessment_grade or infer_grade_fallback(job)
            # A demo job is finalised outright: the review gate exists so a
            # HUMAN approves what real candidates are asked, and there is no
            # human in a seed run. Real jobs still sit in
            # `questions_pending_review` until a recruiter approves both halves.
            if apply:
                job.assessment_grade = grade
                job.assessment_status = "ready_for_candidates"
                job.questions_generated_at = job.questions_generated_at or datetime.now(timezone.utc)
                job.questions_approved_at = job.questions_approved_at or datetime.now(timezone.utc)
                job.framework_generated_at = job.framework_generated_at or datetime.now(timezone.utc)
                job.framework_approved_at = job.framework_approved_at or datetime.now(timezone.utc)

            framework = (
                await session.execute(
                    select(JobCompetency).where(
                        JobCompetency.job_id == job.id, JobCompetency.is_active.is_(True)
                    )
                )
            ).scalars().all()
            if not framework:
                seeded = [
                    JobCompetency(
                        tenant_id=job.tenant_id, job_id=job.id, category=row["category"],
                        name=row["name"], description=row["description"],
                        ordinal=index,
                        required_level=ppi.required_level_score(row["required_level"]),
                    )
                    for index, row in enumerate(ppi._fallback_framework(job), 1)
                ]
                if apply:
                    session.add_all(seeded)
                    await session.flush()
                framework = seeded
            framework = sorted(
                framework, key=lambda row: (ppi.CATEGORIES.index(row.category), row.ordinal)
            )

            for link in links:
                report = (
                    await session.execute(
                        select(FunctionalSkillsReport).where(
                            FunctionalSkillsReport.job_candidate_link_id == link.id
                        )
                    )
                ).scalars().first()
                if report is not None:
                    continue
                profile = await session.get(Profile, link.profile_id) if link.profile_id else None
                aspects = (profile.aspects_json if profile else None) or {}
                # The six mandatory application fields (spec §7). The seed
                # corpus predates them on the link, so the demo report falls
                # back to whatever the profile snapshot happens to carry.
                submitted = dict(link.validation_json or {})
                submitted.setdefault("current_ctc", aspects.get("current_ctc") or aspects.get("32"))
                submitted.setdefault("expected_ctc", aspects.get("expected_ctc") or aspects.get("33"))
                submitted.setdefault("notice_period", aspects.get("notice_period") or aspects.get("34"))
                submitted.setdefault("joining_date", aspects.get("joining_date") or aspects.get("35"))
                submitted.setdefault("document_readiness", aspects.get("document_readiness") or aspects.get("39"))
                submitted.setdefault(
                    "role_interest",
                    f"I am interested in applying my experience to the {job.title} role and learning more about the team.",
                )
                validation = {
                    "captured": True,
                    **submitted,
                    "fields": [
                        {"key": field["key"], "label": field["label"], "value": submitted.get(field["key"])}
                        for field in VALIDATION_FIELDS
                    ],
                }
                dimensions: list[dict] = []
                breakdown = link.match_breakdown_json or {}
                # No weights: the four AI Score parameters are each judged
                # on their own terms (spec 2026-07-30).
                for ordinal, (name, key, description) in enumerate(MATCHING_DIMENSIONS, 1):
                    item = breakdown.get(key) or {}
                    dimensions.append({
                        "category": "matching",
                        "name": name,
                        "description": description,
                        "score": int(float(item.get("score", 5)) * 10),
                        "required_level": None,
                        "remark": _fallback_remark_25(name),
                        "ordinal": ordinal,
                    })
                ordinal_by_category: dict[str, int] = {}
                for competency in framework:
                    ordinal_by_category[competency.category] = (
                        ordinal_by_category.get(competency.category, 0) + 1
                    )
                    dimensions.append({
                        "category": competency.category,
                        "name": competency.name,
                        "description": competency.description,
                        "score": _stable_score(f"mock:{link.id}:{competency.name}"),
                        "required_level": competency.required_level,
                        "remark": _fallback_remark_45(competency.name),
                        "ordinal": ordinal_by_category[competency.category],
                    })
                # NO SEPARATE TECHNICAL ROWS. There was a block here that
                # emitted one dimension per distinct skill in the job's
                # technical coverage plan. Draft v4 folded technical depth into
                # the matrix's Must-have items, which the loop above already
                # covers, and a backfill that invented a parallel set of
                # `technical` rows would write a report shape nothing renders
                # any more.
                assessed = [row for row in dimensions if row["category"] != "matching"]
                overall_score = (
                    round(sum(row["score"] for row in assessed) / len(assessed))
                    if assessed else 0
                )
                probes = [
                    f"Can you share a recent example that demonstrates stronger depth in {item['name']}?"
                    for item in sorted(assessed, key=lambda row: row["score"])[:10]
                ]
                if apply:
                    report = FunctionalSkillsReport(
                        tenant_id=job.tenant_id,
                        job_id=job.id,
                        job_candidate_link_id=link.id,
                        grade=grade,
                        overall_summary=_fallback_remark_45("this candidate's overall suitability"),
                        overall_score=overall_score,
                        scoring_mode="deterministic_fallback",
                        validation_json=validation,
                        suggested_probes_json=probes[:10],
                        synthesized_at=datetime.now(timezone.utc),
                    )
                    session.add(report)
                    await session.flush()
                    session.add_all([
                        ReportDimension(tenant_id=job.tenant_id, report_id=report.id, **item)
                        for item in dimensions
                    ])
                counts["reports"] += 1
                counts["dimensions"] += len(dimensions)
            if apply:
                await session.commit()
        if not apply:
            await session.rollback()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Persist the idempotent backfill")
    args = parser.parse_args()
    result = asyncio.run(backfill(args.apply))
    print(result)


if __name__ == "__main__":
    main()
