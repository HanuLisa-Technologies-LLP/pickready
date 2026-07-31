import uuid
from datetime import datetime

from sqlalchemy import (
    Computed, DateTime, Enum, ForeignKey, Index, Integer, String, Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin
from app.models.enums import ApprovalDecision, JobStatus


# ── Reporting-to dropdown (2026-07-28) ───────────────────────────────────────
# The Create Job form offers this fixed ordered list plus a free-text "Others"
# path. The COLUMN stays a plain string: the stored value is whatever the
# recruiter picked or typed, so a company with an unusual title is never forced
# into someone else's taxonomy. Defined here rather than in models/enums.py
# because it is job-local presentation data, not a cross-cutting enum.
REPORTING_TO_OPTIONS: tuple[str, ...] = (
    "Team Lead",
    "Engineering Manager",
    "Project Manager",
    "Product Manager",
    "Head of Department",
    "Director",
    "VP",
    "CTO",
    "CEO",
    "Founder",
    "Others",
)

#: The sentinel the UI sends when the recruiter chooses to type their own
#: value. It is never itself stored as the reporting line.
REPORTING_TO_OTHER = "Others"


class Job(Base, UUIDPKMixin, CreatedAtMixin):
    """`jd_markdown` is the CANONICAL candidate-facing job description as of
    2026-07-28: one AI-drafted, recruiter-edited Markdown document with seven
    fixed sections. `jd_json` keeps the per-section projection (role,
    responsibilities, accountabilities, education, skills, experience_years,
    reporting_to), derived by parsing that document, so every existing reader
    keeps working. `reportees` was removed entirely (migration 0022).
    compensation_json is added by HR post-ratification (FR-4.1)."""
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_tenant_status", "tenant_id", "status"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    department: Mapped[str | None] = mapped_column(String(255))
    level: Mapped[str | None] = mapped_column(String(100))
    jd_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    #: The unified JD document (migration 0022). Nullable so jobs created
    #: before this release keep loading; api/jobs backfills it on read from
    #: `jd_json` rather than leaving the candidate an empty page.
    jd_markdown: Mapped[str | None] = mapped_column(Text)
    #: The experience band that replaced the free-text `level` on the Create
    #: Job form. Both nullable at the DB layer (legacy rows have neither); the
    #: schema enforces min <= max whenever either is supplied.
    experience_min_years: Mapped[int | None] = mapped_column(Integer)
    experience_max_years: Mapped[int | None] = mapped_column(Integer)
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
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    # ── The one manual step in the pipeline (spec §11, reinstated 2026-07-30) ─
    # `questions_pending_review` -> `ready_for_candidates`. A new job enters
    # review as soon as BOTH generators have run (the technical bank and the
    # PPI framework) and leaves it only when a recruiter has finalised BOTH.
    # No candidate may open the unified conversation before then, so the two
    # things every applicant is compared on are human-approved exactly once per
    # job. The gate was removed on 2026-07-25 and restored by client decision.
    assessment_status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="questions_pending_review",
        server_default="questions_pending_review",
    )
    # Canonical store for the REQUIRED Create Job `grade` field (spec §5/§6):
    # non_managerial | managerial | leadership | cxo. NOT NULL as of 0014.
    assessment_grade: Mapped[str] = mapped_column(
        String(40), nullable=False, default="non_managerial",
        server_default="non_managerial",
    )
    # ── Fixed 30-day posting window (migration 0018) ─────────────────────────
    # `posting_start_date` is the only writable one — stamped when the job
    # publishes. The other two are Postgres GENERATED columns, so the database
    # itself rejects any UPDATE against them: a recruiter cannot move or extend
    # the window even with hand-written SQL. Mapped read-only here so SQLAlchemy
    # never attempts to include them in an INSERT or UPDATE.
    posting_start_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    # `Computed` is what tells SQLAlchemy these are generated: it omits them
    # from every INSERT and UPDATE and reads them back after a flush. Without
    # it, creating a job would fail with "can only be updated to DEFAULT".
    #
    # The expression mirrors migration 0018 exactly. `timestamptz + interval`
    # is STABLE (DST-dependent) and Postgres requires IMMUTABLE here, hence the
    # round-trip through a naive UTC timestamp.
    posting_end_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        Computed(
            "timezone('UTC', timezone('UTC', posting_start_date) "
            "+ INTERVAL '30 days')",
            persisted=True,
        ),
        nullable=True,
    )
    grace_period_end_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        Computed(
            "timezone('UTC', timezone('UTC', posting_start_date) "
            "+ INTERVAL '35 days')",
            persisted=True,
        ),
        nullable=True,
    )

    # ── Per-job JD sections (migration 0016) ─────────────────────────────────
    # Seeded from the company profile when the job is created. Editing them on
    # the job is a PER-JOB OVERRIDE that never writes back to the company, and
    # a later company-profile edit never rewrites an existing job (spec §3.2).
    # NULL means "never set" — jobs created before 0016 fall back to the live
    # company profile at read time (see api/jobs.resolve_jd_sections).
    about_company: Mapped[str | None] = mapped_column(Text)
    work_life: Mapped[str | None] = mapped_column(Text)
    benefits: Mapped[str | None] = mapped_column(Text)

    questions_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    questions_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    question_reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # ── PPI framework review (migration 0030) ────────────────────────────────
    # Tracked separately from the technical bank because the two are generated
    # in PARALLEL and approved independently; the job reaches
    # `ready_for_candidates` only when both `*_approved_at` are stamped.
    framework_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    framework_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
