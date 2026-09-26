"""One invited, proctored, consented assessment, for the recording tests.

Real rows in the migrated test database: a tenant, a job, a candidate, the
application, the conversation (the invitation), an ACTIVE proctoring session
and, unless a test says otherwise, the assessment consent. The candidate's
ownership check is replaced by a resolver over these rows, because who the
candidate is belongs to the conversation routes and their own tests; what the
recording tests exercise is everything after it.

`cleanup` deletes the tenant and the candidate, and every recording row and
segment row goes with them by cascade.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text


async def factory_or_skip():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except OSError:
        await engine.dispose()
        pytest.skip("no database reachable")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


class World:
    def __init__(self) -> None:
        self.tenant_id = uuid.uuid4()
        self.job_id = uuid.uuid4()
        self.cand_id = uuid.uuid4()
        self.user_id = uuid.uuid4()
        self.link_id = uuid.uuid4()
        self.conv_id = uuid.uuid4()


async def seed(factory, world: World, *, consent: bool = True, job_purge_due_at=None) -> None:
    from app.core.db import superadmin_scope
    from app.models import Candidate, Job, JobStatus, LinkSource, Tenant
    from app.models.assessment import AssessmentConversation
    from app.models.candidate import JobCandidateLink
    from app.models.dual_mode import MODE_CONVERSATIONAL, AssessmentConsent
    from app.models.proctoring import OUTCOME_ACTIVE, ProctoringSession

    now = datetime.now(timezone.utc)
    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                s.add(Tenant(id=world.tenant_id, name=f"Rec {world.tenant_id.hex[:6]}",
                             domain=f"{world.tenant_id}.rec.test"))
                await s.flush()
                s.add(Job(id=world.job_id, tenant_id=world.tenant_id, title="Engineer",
                          jd_json={}, status=JobStatus.ratified, ratified_at=now,
                          assessment_status="ready_for_candidates",
                          assessment_grade="non_managerial",
                          # The purge date exists only on a closed job (the
                          # 0112 CHECK), so a test that wants one closes it.
                          closed_at=now if job_purge_due_at else None,
                          assessment_purge_due_at=job_purge_due_at))
                s.add(Candidate(id=world.cand_id, email=f"r{world.cand_id.hex[:8]}@t.test",
                                full_name="Recording Candidate", consent_databank=False))
                await s.flush()
                s.add(JobCandidateLink(id=world.link_id, tenant_id=world.tenant_id,
                                       job_id=world.job_id, candidate_id=world.cand_id,
                                       source=LinkSource.fresh, status="applied"))
                await s.flush()
                s.add(AssessmentConversation(
                    id=world.conv_id, tenant_id=world.tenant_id, job_id=world.job_id,
                    job_candidate_link_id=world.link_id, grade="non_managerial",
                    status="active", next_question_index=0, invitation_sent_at=now,
                    mode=MODE_CONVERSATIONAL,
                ))
                await s.flush()
                s.add(ProctoringSession(
                    tenant_id=world.tenant_id, conversation_id=world.conv_id,
                    job_candidate_link_id=world.link_id, candidate_id=world.cand_id,
                    job_id=world.job_id, consented_at=now, started_at=now,
                    outcome=OUTCOME_ACTIVE,
                ))
                if consent:
                    s.add(AssessmentConsent(
                        tenant_id=world.tenant_id, candidate_id=world.cand_id,
                        conversation_id=world.conv_id,
                        job_candidate_link_id=world.link_id,
                        assessment_mode=MODE_CONVERSATIONAL, consent_status="granted",
                        consented_at=now, consent_version="test",
                        privacy_policy_version="test", terms_version="test",
                    ))


async def cleanup(factory, world: World) -> None:
    from app.core.db import superadmin_scope

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                await s.execute(text("DELETE FROM tenants WHERE id = :t"),
                                {"t": str(world.tenant_id)})
                await s.execute(text("DELETE FROM candidates WHERE id = :c"),
                                {"c": str(world.cand_id)})


def candidate(world: World):
    from app.api.deps import CurrentUser
    from app.core.security import AUDIENCE_CANDIDATE
    from app.models import Role

    return CurrentUser(user_id=world.user_id, tenant_id=None, role=Role.candidate,
                       audience=AUDIENCE_CANDIDATE)


def resolve_ownership(monkeypatch, world: World) -> None:
    """Stand in for the candidate-ownership check over the seeded rows."""
    from sqlalchemy import select

    from app.api import assessment_recording
    from app.models import Job
    from app.models.assessment import AssessmentConversation
    from app.models.candidate import JobCandidateLink

    async def _link(session, user, link_id):
        if link_id != world.link_id or user.user_id != world.user_id:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Application not found")
        return (
            await session.get(JobCandidateLink, world.link_id),
            await session.get(Job, world.job_id),
        )

    async def _conversation(session, user, link_id):
        link, job = await _link(session, user, link_id)
        conversation = (
            await session.execute(
                select(AssessmentConversation).where(
                    AssessmentConversation.job_candidate_link_id == link.id
                )
            )
        ).scalars().first()
        return link, job, conversation

    monkeypatch.setattr(assessment_recording, "_candidate_link", _link)
    monkeypatch.setattr(assessment_recording, "_candidate_conversation", _conversation)
