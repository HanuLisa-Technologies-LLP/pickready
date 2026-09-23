"""The per-candidate question generation task.

Carved out of `workers/tasks.py` on 2026-09-24 (PLAN-p3 WP0) as a PURE MOVE:
the task name, route and retry policy are unchanged, and `workers/tasks.py`
imports this module so importing the registry still registers every task.
"""
from __future__ import annotations

import logging
import uuid

from app.workers.registry import Route, task
from app.workers.runtime import (
    worker_session as _worker_session,
    _run,
)

logger = logging.getLogger(__name__)


@task(
    name="pickready.generate_candidate_questions",
    route=Route.ECS,
    max_attempts=2,
    backoff_seconds=5.0,
)
def generate_candidate_questions(link_id: str):
    """This candidate's PPI questions, from their resume + the job's framework.

    Per candidate, unlike the technical bank. Idempotent: a candidate who
    already has questions keeps exactly those, so a redelivery cannot
    hand someone a different assessment halfway through.
    """
    from app.models.candidate import JobCandidateLink
    from app.models.job import Job
    from app.services.assessment_questions.generate import (
        generate_candidate_questions as _generate,
    )

    async def _task():
        async with _worker_session() as session:
            link = await session.get(JobCandidateLink, uuid.UUID(str(link_id)))
            if link is None:
                raise ValueError(f"Application {link_id} not found")
            job = await session.get(Job, link.job_id)
            if job is None:
                raise ValueError(f"Job {link.job_id} not found")
            rows = await _generate(session, job, link)
            await session.commit()
            logger.info(
                "ppi_questions.generated link_id=%s count=%d", link_id, len(rows)
            )
    _run(_task())
