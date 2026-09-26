"""Shared seed for the invitation tests (PLAN-p3 WP1). Not a test module.

Builds on `comms_world` (one tenant, one recruiter, one job, one applied
candidate) and adds what inviting needs: a job ready for candidates with a
classification, more applicants, a credit balance, and optionally the demo
flag. Every write goes through a bypass session exactly as a worker writes,
and every read the tests make is from a SECOND connection.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services import credits
from tests import comms_world


@dataclass
class InviteWorld:
    base: comms_world.World
    #: Every applied application on the job, the base one first.
    links: list[uuid.UUID] = field(default_factory=list)
    #: Candidates this world created beyond the base one, deleted at cleanup.
    extra_candidates: list[uuid.UUID] = field(default_factory=list)
    #: candidate id by link id, and each candidate's address.
    candidate_for: dict[uuid.UUID, uuid.UUID] = field(default_factory=dict)
    email_for: dict[uuid.UUID, str] = field(default_factory=dict)

    @property
    def tenant(self) -> uuid.UUID:
        return self.base.tenant

    @property
    def job(self) -> uuid.UUID:
        return self.base.job

    @property
    def recruiter(self) -> uuid.UUID:
        return self.base.recruiter


async def seed(
    sessions: async_sessionmaker[AsyncSession],
    *,
    applicants: int = 3,
    role_classification: str = "STEM",
    assessment_status: str = "ready_for_candidates",
    grant_subunits: int = 0,
    demo: bool = False,
) -> InviteWorld:
    base = await comms_world.seed(sessions)
    world = InviteWorld(base=base, links=[base.link])
    world.candidate_for[base.link] = base.candidate
    world.email_for[base.link] = base.candidate_email
    async with sessions() as session:
        async with session.begin():
            await comms_world.bypass(session)
            await session.execute(
                sa.text(
                    "UPDATE jobs SET assessment_status = :status, "
                    " role_classification = :role, assessment_grade = 'managerial' "
                    "WHERE id = :id"
                ),
                {
                    "status": assessment_status,
                    "role": role_classification,
                    "id": str(base.job),
                },
            )
            for index in range(applicants - 1):
                candidate = uuid.uuid4()
                link = uuid.uuid4()
                email = f"invitee-{candidate.hex[:10]}@invite.test"
                await session.execute(
                    sa.text(
                        "INSERT INTO candidates (id, full_name, email, "
                        " consent_databank, created_at) "
                        "VALUES (:id, :name, :email, false, now())"
                    ),
                    {"id": str(candidate), "name": f"Applicant {index}", "email": email},
                )
                await session.execute(
                    sa.text(
                        "INSERT INTO job_candidate_links "
                        "(id, tenant_id, job_id, candidate_id, source, status) "
                        "VALUES (:id, :tid, :jid, :cid, 'fresh', 'applied')"
                    ),
                    {
                        "id": str(link),
                        "tid": str(base.tenant),
                        "jid": str(base.job),
                        "cid": str(candidate),
                    },
                )
                world.links.append(link)
                world.extra_candidates.append(candidate)
                world.candidate_for[link] = candidate
                world.email_for[link] = email
            if grant_subunits:
                await credits.grant(
                    session,
                    tenant_id=base.tenant,
                    subunits=grant_subunits,
                    idempotency_key=f"invite-test-grant:{base.tenant}",
                )
            if demo:
                await session.execute(
                    sa.text("UPDATE tenants SET is_demo = true WHERE id = :id"),
                    {"id": str(base.tenant)},
                )
    return world


async def cleanup(sessions: async_sessionmaker[AsyncSession], world: InviteWorld) -> None:
    async with sessions() as session:
        async with session.begin():
            await comms_world.bypass(session)
            for candidate in world.extra_candidates:
                await session.execute(
                    sa.text("DELETE FROM candidate_updates WHERE candidate_id = :cid"),
                    {"cid": str(candidate)},
                )
    await comms_world.cleanup(sessions, world.base)
    async with sessions() as session:
        async with session.begin():
            await comms_world.bypass(session)
            for candidate in world.extra_candidates:
                await session.execute(
                    sa.text("DELETE FROM candidates WHERE id = :id"),
                    {"id": str(candidate)},
                )


async def scalar(sql: str, params: dict | None = None):
    """One value, read from a SECOND connection after the fact."""
    rows = await comms_world.rows(comms_world.factory(), sql, params)
    return next(iter(rows[0].values())) if rows else None
