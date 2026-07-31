"""End-to-end validation of the PPI release (spec 2026-07-30).

Runs against the live database, inside the backend container:

    docker compose -f infra/docker-compose.yml exec backend \\
        python -m app.scripts.validate_ppi

Everything it creates is rolled back. It exercises the paths that only break
against a real Postgres: the RLS policies and CHECK constraints on the two new
tables, the review gate's state machine, and report synthesis producing the
report the API actually serialises.
"""
from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select, text

from app.core.db import get_session_factory
from app.models.assessment import (
    AssessmentConversation,
    AssessmentMessage,
    CandidateQuestion,
    FunctionalSkillsReport,
    JobCompetency,
    ReportDimension,
)
from app.models.candidate import Candidate, JobCandidateLink, Profile
from app.models.enums import LinkSource
from app.models.job import Job
from app.models.tenant import Tenant
from app.services import ppi
from app.services.application_validation import MANDATORY_KEYS
from app.services.functional_assessment import build_radar_charts, run_assessment
from app.services.rating import GRADES

CHECKS: list[tuple[str, bool]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((label, ok))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}{(' -> ' + detail) if detail and not ok else ''}")


async def main() -> int:
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(text("SELECT set_config('app.bypass_rls', 'on', false)"))

        tenant = (await session.execute(select(Tenant).limit(1))).scalars().first()
        if tenant is None:
            print("No tenant in the database; seed dev data first.")
            return 2

        # ── A new job enters the review gate ─────────────────────────────────
        job = Job(
            tenant_id=tenant.id,
            title="PPI validation engineer",
            assessment_grade="managerial",
            jd_json={
                "skills": ["Python", "PostgreSQL", "Kafka", "Terraform"],
                "responsibilities": ["Own the ingestion pipeline end to end"],
            },
            jd_markdown="## Role\nOwn the ingestion pipeline.",
        )
        session.add(job)
        await session.flush()
        check(
            "a new job starts in questions_pending_review",
            job.assessment_status == "questions_pending_review",
            job.assessment_status,
        )

        # ── The framework generates and meets the minimum ────────────────────
        framework = await ppi.generate_framework(session, job)
        counts = {
            category: sum(1 for row in framework if row.category == category)
            for category in ppi.CATEGORIES
        }
        check(
            "framework meets the per-category minimum",
            all(count >= ppi.MINIMUM_PER_CATEGORY for count in counts.values()),
            str(counts),
        )
        check(
            "generation never approves the framework",
            job.framework_approved_at is None and job.assessment_status == "questions_pending_review",
        )

        # ── Postgres refuses a Culture competency, whatever the app does ─────
        # Inside a SAVEPOINT: the constraint violation poisons its transaction,
        # and rolling the whole session back would expire every object loaded so
        # far (including `tenant`) and take the rest of the run with it.
        tenant_id, job_id = tenant.id, job.id
        try:
            async with session.begin_nested():
                await session.execute(
                    text(
                        "INSERT INTO job_competencies "
                        "(id, tenant_id, job_id, category, name, required_level, ordinal) "
                        "VALUES (gen_random_uuid(), :t, :j, 'behavioural', 'Culture fit', 82, 99)"
                    ),
                    {"t": str(tenant_id), "j": str(job_id)},
                )
            refused = False
        except Exception:
            refused = True
        check("the database itself refuses a Culture competency", refused)

        # ── The save gate ────────────────────────────────────────────────────
        ok, reason = ppi.framework_is_complete(framework)
        check("a generated framework is saveable", ok, str(reason))

        # ── An application carries the mandatory fields ──────────────────────
        candidate = Candidate(
            tenant_id=tenant.id,
            email=f"ppi-validation-{uuid.uuid4().hex[:8]}@candidates.pickready.test",
            full_name="Validation Candidate",
        )
        session.add(candidate)
        await session.flush()
        profile = Profile(
            candidate_id=candidate.id,
            source_tenant_id=tenant.id,
            resume_text="Built Kafka ingestion on PostgreSQL, ran Terraform for the estate.",
            parsed_fields_json={"skills": ["Python", "Kafka"], "total_experience_years": 8},
        )
        session.add(profile)
        await session.flush()
        validation = {key: f"value for {key}" for key in MANDATORY_KEYS}
        validation["role_interest"] = (
            "I have followed this team's ingestion work for two years and want to own it."
        )
        link = JobCandidateLink(
            tenant_id=tenant.id, job_id=job.id, candidate_id=candidate.id,
            profile_id=profile.id, source=LinkSource.fresh,
            validation_json=validation,
            match_breakdown_json={
                "skills_match": {"score": 8, "comment": "c"},
                "experience_relevance": {"score": 7, "comment": "c"},
                "role_alignment": {"score": 6, "comment": "c"},
                "education_fit": {"score": 5, "comment": "c"},
            },
        )
        session.add(link)
        await session.flush()

        # ── Per-candidate questions ─────────────────────────────────────────
        questions = await ppi.generate_candidate_questions(session, job, link)
        expected = ppi.ppi_question_count(job.assessment_grade)
        check(
            f"the candidate gets exactly {expected} PPI questions",
            len(questions) == expected,
            str(len(questions)),
        )
        probed = {row.competency_id for row in questions}
        check(
            "every framework entry is probed at least once",
            probed >= {row.id for row in framework},
            f"{len(probed)} of {len(framework)}",
        )
        check(
            "no two questions are identical",
            len({row.prompt for row in questions}) == len(questions),
        )

        # ── A transcript, then synthesis ─────────────────────────────────────
        conversation = AssessmentConversation(
            tenant_id=tenant.id, job_id=job.id, job_candidate_link_id=link.id,
            grade=job.assessment_grade, status="completed",
        )
        session.add(conversation)
        await session.flush()
        transcript = []
        for ordinal, row in enumerate(questions, 1):
            transcript.append({
                "speaker": "candidate", "domain": "ppi",
                "question_key": str(row.competency_id),
                "content": "I owned the Kafka consumer rewrite, cut lag by half, and wrote the runbook.",
            })
            session.add(
                AssessmentMessage(
                    tenant_id=tenant.id, conversation_id=conversation.id, ordinal=ordinal,
                    speaker="candidate", domain="ppi",
                    question_key=str(row.competency_id),
                    content=transcript[-1]["content"],
                )
            )
        await session.flush()

        report_id = await run_assessment(session, job, link, transcript)
        report = await session.get(FunctionalSkillsReport, uuid.UUID(report_id))
        rows = (
            await session.execute(
                select(ReportDimension).where(ReportDimension.report_id == report.id)
            )
        ).scalars().all()
        by_category = {
            category: [row for row in rows if row.category == category]
            for category in ("matching", *ppi.CATEGORIES, "technical")
        }
        check("the report has four AI Score parameters", len(by_category["matching"]) == 4)
        for category in ppi.CATEGORIES:
            check(
                f"the report has a {ppi.CATEGORY_LABELS[category]} section",
                len(by_category[category]) >= ppi.MINIMUM_PER_CATEGORY,
                str(len(by_category[category])),
            )
        check(
            "every PPI row carries the job's required level",
            all(
                row.required_level
                for category in ppi.CATEGORIES
                for row in by_category[category]
            ),
        )
        check(
            "8 to 10 suggested interview questions",
            8 <= len(report.suggested_probes_json) <= 10,
            str(len(report.suggested_probes_json)),
        )
        check("the overall score is stored for projection", report.overall_score is not None)
        check(
            "validation reaches the report unrated",
            report.validation_json.get("captured") is True
            and report.validation_json.get("role_interest") == validation["role_interest"]
            and "score" not in report.validation_json,
        )

        charts = build_radar_charts([
            {
                "category": row.category, "name": row.name, "score": row.score,
                "required_level": row.required_level, "ordinal": row.ordinal,
            }
            for row in rows
        ])
        check("four radar charts", len(charts) == 4, str(len(charts)))
        check(
            "every axis plots both shapes, as words",
            all(
                axis["requirement_band"] in GRADES and axis["candidate_band"] in GRADES
                for chart in charts for axis in chart["axes"]
            ),
        )
        check(
            "no chart carries a number beyond its rendering radius",
            all(
                set(axis) == {
                    "axis", "requirement_band", "requirement_index",
                    "candidate_band", "candidate_index",
                }
                for chart in charts for axis in chart["axes"]
            ),
        )

        # ── The gate opens only when BOTH halves are approved ────────────────
        from datetime import datetime, timezone

        job.questions_approved_at = datetime.now(timezone.utc)
        check(
            "approving one half does not open the job",
            job.assessment_status == "questions_pending_review",
        )

        # Nothing here is kept: this is a validation run, not a seeder.
        await session.execute(
            text("DELETE FROM candidate_questions WHERE job_id = :j"), {"j": str(job.id)}
        )
        await session.rollback()

    failed = [label for label, ok in CHECKS if not ok]
    print()
    print(f"{len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed")
    if failed:
        print("FAILED:")
        for label in failed:
            print(f"  - {label}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
