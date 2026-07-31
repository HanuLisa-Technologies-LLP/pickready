"""Read-only production validation for the Functional Skills release."""
import asyncio

import httpx
from sqlalchemy import func, select, text

from app.core.db import get_session_factory
from app.core.security import AUDIENCE_ORG, create_access_token
from app.models.assessment import FunctionalSkillsReport, ReportDimension, TechnicalQuestion
from app.models.candidate import Candidate, JobCandidateLink
from app.models.enums import Role
from app.models.job import Job
from app.models.user import User
from app.services.functional_assessment import word_count


async def main() -> None:
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(text("SELECT set_config('app.bypass_rls', 'on', false)"))
        report = (await session.execute(select(FunctionalSkillsReport))).scalars().first()
        user = (
            await session.execute(
                select(User).where(
                    User.tenant_id == report.tenant_id,
                    User.role.in_((Role.client, Role.hr_manager, Role.recruiter, Role.hiring_manager)),
                )
            )
        ).scalars().first() if report is not None else None
        if user is None or report is None:
            raise RuntimeError("validation fixtures are missing")
        link = await session.get(JobCandidateLink, report.job_candidate_link_id)
        job = await session.get(Job, report.job_id)
        dimensions = (
            await session.execute(select(ReportDimension).where(ReportDimension.report_id == report.id))
        ).scalars().all()
        report_count = (await session.execute(select(func.count(FunctionalSkillsReport.id)))).scalar_one()
        question_count = (await session.execute(select(func.count(TechnicalQuestion.id)))).scalar_one()
        mock_links = (
            await session.execute(
                select(func.count(JobCandidateLink.id))
                .join(Candidate, Candidate.id == JobCandidateLink.candidate_id)
                .where(Candidate.email.ilike("%@candidates.pickready.test"))
            )
        ).scalar_one()

        assert job.assessment_status == "ready_for_candidates"
        assert 45 <= word_count(report.overall_summary) <= 50
        assert dimensions and all(25 <= word_count(row.remark) <= 30 for row in dimensions)
        assert 8 <= len(report.suggested_probes_json) <= 10

        token = create_access_token(user.id, user.role.value, user.tenant_id, AUDIENCE_ORG)
        async with httpx.AsyncClient(base_url="http://localhost:8000") as client:
            response = await client.get(
                f"/api/v2/assessments/reports/links/{link.id}",
                headers={"Authorization": f"Bearer {token}"},
            )
        response.raise_for_status()
        payload = response.json()
        assert "score" not in str(payload).lower()
        assert set(("matching", "behavioral", "technical", "validation", "suggested_interview_probes")).issubset(payload)
        assert all("rating" in item for section in ("matching", "behavioral", "technical") for item in payload[section])
        print({
            "status": "PASS",
            "reports": report_count,
            "questions": question_count,
            "mock_links": mock_links,
            "api_status": response.status_code,
            "numeric_scores_exposed": False,
        })


if __name__ == "__main__":
    asyncio.run(main())
