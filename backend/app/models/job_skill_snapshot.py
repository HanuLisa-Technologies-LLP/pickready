"""The immutable skills contract a job's candidates are assessed against.

WHY A ROW, AND WHY INSERT-ONLY
------------------------------
The skills of a job (`job_competencies`) stay editable until the first
candidate STARTS the assessment. At that instant `assessment_contract.
lock_contract` writes one of these rows: the full skill list with each skill's
hidden priority and evidence line, the hidden role summary, the grade, and a
sha256 digest over that content. From then on Vaada (the conversation) and Miti
(the grade) read THIS row for every conversation bound to it
(`assessment_conversations.skill_snapshot_id`), never the live rows.

It replaces two things that were each true in prose only. The scorecard
binding recorded a version NUMBER and a timestamp while the rows it named went
on being mutated in place, so "what was this candidate graded against" had no
answer; and a versioning resolver (deleted in the Vivekium release) described
the right property and had no production caller. A snapshot carries the CONTENT,
so the answer no longer depends on rows nobody froze.

IMMUTABILITY IS IN THE DATABASE, NOT IN THIS FILE. Migration 0118 revokes
UPDATE from the application role and installs `job_skill_snapshot_is_immutable`,
a BEFORE UPDATE OR DELETE trigger: an UPDATE always raises, and a DELETE raises
unless it is the cascade of the job or tenant being deleted (a nested trigger).
A rule enforced only in a service is a rule the next writer does not know
about; the BGV employment table taught that and this follows it.

INTERNAL. Nothing here is serialised: priorities and evidence lines are the
contract's working, and the recruitment team never sees or edits them.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin

__all__ = [
    "JobSkillSnapshot",
    "SNAPSHOT_SOURCE_LOCK",
    "SNAPSHOT_SOURCE_MIGRATION",
    "SNAPSHOT_SOURCES",
]

#: Written by `assessment_contract.lock_contract` at a candidate's start.
SNAPSHOT_SOURCE_LOCK = "lock"
#: Written by migration 0118 for a job that already had a started assessment,
#: from the exact rows those candidates were questioned against.
SNAPSHOT_SOURCE_MIGRATION = "migration"
#: Mirrored by `ck_job_skill_snapshots_source`.
SNAPSHOT_SOURCES: tuple[str, ...] = (SNAPSHOT_SOURCE_LOCK, SNAPSHOT_SOURCE_MIGRATION)


class JobSkillSnapshot(Base, UUIDPKMixin, CreatedAtMixin):
    """One locked version of one job's skills contract."""

    __tablename__ = "job_skill_snapshots"
    __table_args__ = (
        UniqueConstraint("job_id", "version", name="uq_job_skill_snapshots_version"),
        Index("ix_job_skill_snapshots_tenant", "tenant_id"),
        Index("ix_job_skill_snapshots_job", "job_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    #: 1, 2, ... per job. The contract in force is the highest.
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    #: `[{id, name, bucket, priority, evidence_line}, ...]` in canonical order.
    skills_json: Mapped[list] = mapped_column(JSONB, nullable=False)
    role_summary: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    #: Provenance of the hidden context: who wrote it, with which model and
    #: prompt version. `{"generated_by": "migration"}` for a migrated snapshot.
    context_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    #: `jobs.assessment_grade` at the lock. The grade decides the question
    #: budget, so it is locked with the skills (D5) and covered by the digest.
    grade: Mapped[str] = mapped_column(String(40), nullable=False)
    #: sha256 hex over the CONTENT only (skills, role summary, grade), never
    #: over version or lock state, so the live digest at the instant of lock
    #: equals this one. `assessment_contract.compute_digest` is the formula.
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    #: One of `SNAPSHOT_SOURCES`.
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    #: The conversation whose start took the lock. NULL for a migrated
    #: snapshot, which several started conversations may share.
    locked_by_conversation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    locked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
