import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean, Computed, DateTime, Enum, Float, ForeignKey, Index, Integer,
    Numeric, String, Text,
)
from sqlalchemy import event, inspect, text
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
    # `posting_end_date` / `grace_period_end_date` are Postgres GENERATED
    # columns (below). SQLAlchemy omits them from the INSERT, and without
    # eager_defaults it leaves them EXPIRED on the instance — so the first
    # attribute read after a flush issues a lazy SELECT. Under asyncpg that
    # lazy load happens outside the greenlet and raises MissingGreenlet, which
    # surfaced as a hard 500 on POST /jobs ("2 validation errors for JobOut",
    # posting_end_date + grace_period_end_date) and made it impossible to
    # create a job at all. eager_defaults adds RETURNING to the INSERT/UPDATE
    # so the generated values come back with the write. Same reasoning as
    # models/bd.py, which already carries this flag.
    __mapper_args__ = {"eager_defaults": True}

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

    # ── Draft v4 job setup (migration 0048, 0049) ────────────────────────────
    # The total number of questions this job's candidates are asked, resolved
    # ONCE at setup from the grade's range and the size of the saved matrix
    # (spec §5.4). Stored rather than recomputed per candidate: the count is
    # part of what makes two reports on one job comparable, exactly as the
    # matrix is, so it must not move when the matrix is edited later.
    question_target: Mapped[int | None] = mapped_column(Integer)
    #: Stamped when the reporting authority finishes the SWOT intake. The PPI
    #: matrix is generated from the JD AND this, so it waits behind the stamp.
    swot_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Stamped when the recruiter finalises the job's Matching category list.
    matching_categories_finalized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    #: RBAC 17's eight-state job lifecycle, added by migration 0061 and MAPPED
    #: here by 0064. It was read through raw SQL by `rbac.load_job_resource`
    #: from the day it landed, which was enough to AUTHORIZE against but not to
    #: advance: nothing could write it without a mapped attribute, so every job
    #: sat in the state 0061's backfill left it in. Nullable, because 0061 makes
    #: it nullable and a row inserted by a pod that predates the mapping still
    #: has to be legal.
    lifecycle_state: Mapped[str | None] = mapped_column(String(40))
    #: RBAC 20: "Finalization MUST record: user who finalized it, timestamp,
    #: relevant version, relevant hiring criteria version." Added by 0061 and
    #: mapped here by 0064, for the same reason `lifecycle_state` was: 0061
    #: could authorize against them and nothing could write them.
    finalized_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: RBAC 22 and 35: the version of the hiring criteria in force. Incremented
    #: by an explicit revision, never by an edit.
    criteria_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    #: spec-doc6 4.1's one correlation id per flow, issued at job creation and
    #: never rewritten. Copied onto every audit row and artifact envelope from
    #: Bodha through Siddhi, so the whole flow is one query rather than a
    #: reconstruction from timestamps.
    correlation_id: Mapped[str | None] = mapped_column(String(64))

    # ── STEM / Non-STEM classification (Master Directive Part 3 §5.1) ───────
    # System-determined at JD generation, locked to the RAW AI-generated JD,
    # never client-editable. `credit_cost_per_report` is stored explicitly —
    # not derived at deduction time — so the ledger's audit trail matches what
    # the job said when the report completed. Jobs that predate the feature
    # carry the deployment default: NON_STEM, confidence 0.00 (Part 3 §11).
    role_classification: Mapped[str] = mapped_column(
        String(10), nullable=False, default="NON_STEM", server_default="NON_STEM"
    )
    classification_confidence: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0"
    )
    classification_signals: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    #: True once any candidate has COMPLETED an assessment against this job.
    #: A locked classification can never be changed, only compensated with a
    #: credit adjustment (Part 3 §8).
    classification_locked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    credit_cost_per_report: Mapped[Decimal] = mapped_column(
        Numeric(3, 1), nullable=False, default=Decimal("1.0"), server_default="1.0"
    )
    #: True when the engine's stem-score fell in the §4.4 review band
    #: (0.30–0.79) or the engine errored — the Provider Portal's
    #: Classification Review Queue lists exactly these (Part 3 §9).
    classification_tentative: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    classification_overridden: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    classification_override_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    #: The AI-generated JD text BEFORE recruiter editing — what the engine
    #: classified. Stored separately from `jd_markdown` (the edited, canonical
    #: document) purely for classification audit (Part 3 Rule 3).
    raw_jd_text: Mapped[str | None] = mapped_column(Text)

    # ── Proctoring (migration 0076, proctoring-spec-doc section 6) ───────────
    # The ONE recruiter setting: what happens when a candidate crosses the
    # warning limit during their assessment. `terminate` stops it;
    # `continue_and_note` lets them finish and says so in the report. The
    # default is continue-and-note: the product never terminates by default
    # without an explicit choice. Vocabulary in `models/proctoring.py`, pinned
    # by a database CHECK. It is read for every candidate on the job and moves
    # no score, no grade and no ranking.
    proctoring_warning_policy: Mapped[str] = mapped_column(
        String(20), nullable=False, default="continue_and_note",
        server_default="continue_and_note",
    )


class JDDraft(Base, UUIDPKMixin, CreatedAtMixin):
    """One AI-generated JD draft, classified the moment it was generated.

    Part 3 Rule 3 requires classification to run on the RAW AI-generated JD
    before the recruiter sees or edits it, and Rule 2 requires that the client
    cannot influence the result. `/jobs/generate-jd` is stateless from the
    client's point of view, so trusting the browser to hand the raw text back
    unmodified at job creation would hand the client exactly the influence
    Rule 2 forbids. Instead the draft is persisted server-side here at
    generation time, and job creation references it by id: the classification
    the job gets is the one this row already holds.
    """
    __tablename__ = "jd_drafts"
    __table_args__ = (Index("ix_jd_drafts_tenant", "tenant_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_jd_text: Mapped[str] = mapped_column(Text, nullable=False)
    role_classification: Mapped[str] = mapped_column(String(10), nullable=False)
    classification_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    stem_score: Mapped[float] = mapped_column(Float, nullable=False)
    classification_signals: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list
    )
    tentative: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    engine_error: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


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


# ── Derived-column invalidation (2026-08-11) ─────────────────────────────────
#
# `jobs.embedding` is DERIVED from the JD, and until now nothing said so.
# Editing a job description updated `jd_markdown` and `jd_json` and left the
# vector alone, which is invisible in the matching pipeline (it re-embeds the
# JD on every run, so it never reads the stale value) and NOT invisible on the
# candidate's New Jobs board: `services/job_relevance` reads `jobs.embedding`
# directly and never recomputes it. So after a JD edit, every candidate kept
# being ranked against a job description nobody can read any more, and there
# was no point at which that would correct itself.
#
# A listener rather than six lines in four handlers. There are already six
# sites that write these fields; the seventh is the one that forgets, and
# forgetting produces no error, just a quietly wrong ranking.
#
# NULL is the right invalidation, not a recompute: `job_relevance` documents
# that a job without an embedding falls back to keyword scoring, which ranks
# against the CURRENT text. Ranking on current keywords beats ranking on a
# stale vector, and `matching.run_matching` writes a fresh vector on its next
# run.

#: Every column `matching._jd_text` reads. Change one and the vector is stale.
_EMBEDDING_SOURCE_FIELDS = ("title", "department", "level", "jd_json")


@event.listens_for(Job, "after_update")
def _invalidate_job_embedding(mapper, connection, target: "Job") -> None:  # noqa: ARG001
    """NULL the vector when the text it was built from changes.

    `after_update`, and a statement of its own, because `jobs.embedding` is a
    pgvector column that is NOT mapped on this class -- it is written with raw
    SQL by `matching.run_matching`. Setting an attribute here would change a
    Python object and never reach the database, which is the quietest possible
    way for this fix to not work.

    Runs on the flush's own connection, so it is inside the caller's
    transaction: a rolled-back JD edit does not lose the embedding with it.
    """
    state = inspect(target)
    changed = any(
        state.attrs[field].history.has_changes()
        for field in _EMBEDDING_SOURCE_FIELDS
    )
    if not changed:
        return
    connection.execute(
        text("UPDATE jobs SET embedding = NULL WHERE id = :id"), {"id": target.id}
    )
