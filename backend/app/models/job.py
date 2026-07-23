import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin
from app.models.enums import ApprovalDecision, JobStatus


class Job(Base, UUIDPKMixin, CreatedAtMixin):
    """jd_json holds the structured JD (FR-3.1): role, responsibilities,
    accountabilities, education, skills, experience_years, reporting_to,
    reportees. compensation_json is added by HR post-ratification (FR-4.1)."""
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_tenant_status", "tenant_id", "status"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    department: Mapped[str | None] = mapped_column(String(255))
    level: Mapped[str | None] = mapped_column(String(100))
    jd_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    compensation_json: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False, length=20), nullable=False, default=JobStatus.draft
    )
    requirement_period: Mapped[str | None] = mapped_column(String(100))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    # Embedding of the JD for the semantic matching stage — set by the worker.
    # Actual vector(1024) column type is applied in the migration (pgvector).
    ratified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobApproval(Base, UUIDPKMixin):
    """One row per FSM transition attempt (ESD §7). Inactive levels get an
    explicit `skipped` row — never silently auto-approved."""
    __tablename__ = "job_approvals"
    __table_args__ = (Index("ix_job_approvals_job", "job_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    level: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False, length=20), nullable=False
    )
    approver_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    decision: Mapped[ApprovalDecision] = mapped_column(
        Enum(ApprovalDecision, native_enum=False, length=20), nullable=False
    )
    remarks: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default="now()", nullable=False
    )
