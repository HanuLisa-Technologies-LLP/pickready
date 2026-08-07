import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean, DateTime, Enum, Float, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint, event,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin
from app.models.enums import (
    LinkSource, PipelineStatus, SubmittedVia, Tier, VerificationStatus,
)


class Candidate(Base, UUIDPKMixin, CreatedAtMixin):
    """tenant_id NULL: a candidate profile can be shared across tenants via
    the Databank (ESD §4). consent_databank mirrors Aspect 40 (FR-4.2)."""
    __tablename__ = "candidates"
    __table_args__ = (Index("ix_candidates_email", "email"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    full_name: Mapped[str | None] = mapped_column(String(255))  # as per PF records / Class X memorandum
    # Nullable: a phone-only candidate (Firebase phone provider) has no email
    # (migration 0004).
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20))
    city: Mapped[str | None] = mapped_column(String(120))
    age: Mapped[int | None] = mapped_column(Integer)
    gender: Mapped[str | None] = mapped_column(String(30))
    consent_databank: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # ── Unified candidate profile (migration 0015) ───────────────────────────
    # The 40 validation aspects, answered ONCE here as a structured form rather
    # than re-asked inside every job's assessment conversation (client decision,
    # 2026-07-27). Shape is defined by services/candidate_profile_form.py; each
    # application snapshots this onto its own Profile.aspects_json.
    profile_form_json: Mapped[dict | None] = mapped_column(JSONB)
    profile_form_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: The candidate's MAIN resume — the one they upload and re-upload on their
    #: profile and reuse across applications (FR-6.2). Points at the Profile row
    #: that owns the file metadata, so nothing about resume storage changes.
    main_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True
    )


class Profile(Base, UUIDPKMixin, CreatedAtMixin):
    """The Profile (PRD glossary): resume + 40-aspect responses + employer
    verification for one candidate. Embedding powers the semantic stage."""
    __tablename__ = "profiles"
    __table_args__ = (Index("ix_profiles_candidate", "candidate_id"),)

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False
    )
    source_tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # Every binary lives in the private GCS bucket. These fields remain on the
    # profile so one application is an immutable snapshot of its resume.
    resume_url: Mapped[str | None] = mapped_column(String(1000))
    resume_public_id: Mapped[str | None] = mapped_column(String(512), index=True)
    resume_storage_provider: Mapped[str] = mapped_column(
        String(20), nullable=False, default="gcs"
    )
    resume_legacy_public_id: Mapped[str | None] = mapped_column(String(512))
    resume_original_filename: Mapped[str | None] = mapped_column(String(255))
    resume_mime_type: Mapped[str | None] = mapped_column(String(255))
    resume_size_bytes: Mapped[int | None] = mapped_column(Integer)
    resume_uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resume_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    resume_metadata_json: Mapped[dict | None] = mapped_column(JSONB)
    resume_text: Mapped[str | None] = mapped_column(Text)  # extracted; tsvector col in migration
    aspects_json: Mapped[dict | None] = mapped_column(JSONB)  # {"1": {...}, ..., "40": {...}}
    parsed_fields_json: Mapped[dict | None] = mapped_column(JSONB)  # skills, experience, education, employment_history
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024))  # BGE-M3
    aspects_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ── Type of procurement (2026-07-28) ─────────────────────────────────────────
# Every candidate on a job arrived one of exactly three ways:
#
#   applied  : they found the role through PickReady and applied themselves.
#   sourced  : they arrived through a third-party link (LinkedIn, Naukri, a
#              forwarded post) and applied through the public /apply page.
#   databank : the recruitment team bulk-uploaded their resume.
#
# This is DISPLAY AND FILTER DATA ONLY. All three types go through identical
# AI parsing, embedding, matching, ranking and assessment; nothing in this
# codebase may branch on `source_type` to change how a candidate is processed.
#
# claude.md rule 7 ("databank candidates never re-enter the verification /
# 40-aspect flow") is untouched by this: it is about the EMPLOYER VERIFICATION
# flow, which keys off `source` (databank | fresh), and the 40 aspects are a
# profile form now rather than an outreach step. Two different columns, two
# different questions.
#
# Defined here rather than in models/enums.py so the enum stays local to the
# model that owns it.
SOURCE_TYPE_APPLIED = "applied"
SOURCE_TYPE_SOURCED = "sourced"
SOURCE_TYPE_DATABANK = "databank"

SOURCE_TYPES: tuple[str, ...] = (
    SOURCE_TYPE_APPLIED,
    SOURCE_TYPE_SOURCED,
    SOURCE_TYPE_DATABANK,
)

#: Human labels for the "Type of Procurement" column.
SOURCE_TYPE_LABELS: dict[str, str] = {
    SOURCE_TYPE_APPLIED: "Applied",
    SOURCE_TYPE_SOURCED: "Sourced",
    SOURCE_TYPE_DATABANK: "Databank",
}


def source_type_label(value: str | None) -> str:
    """Display label for a procurement type. Unknown/NULL reads as Applied,
    matching the NOT NULL default on the column."""
    return SOURCE_TYPE_LABELS.get(value or SOURCE_TYPE_APPLIED, SOURCE_TYPE_LABELS[SOURCE_TYPE_APPLIED])


class JobCandidateLink(Base, UUIDPKMixin, CreatedAtMixin):
    __tablename__ = "job_candidate_links"
    __table_args__ = (
        UniqueConstraint("job_id", "candidate_id", name="uq_jcl_job_candidate"),
        Index("ix_jcl_job", "job_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False
    )
    profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL")
    )
    source: Mapped[LinkSource] = mapped_column(
        Enum(LinkSource, native_enum=False, length=10), nullable=False
    )
    match_score: Mapped[float | None] = mapped_column(Float)  # overall × 10 (0–100), for sorting/tiers
    match_rationale: Mapped[str | None] = mapped_column(Text)  # HR-visible, never candidate-visible
    # 4-parameter weighted breakdown (rev 2): skills_match/experience_relevance/
    # role_alignment/education_fit + overall, each {score 1-10, comment}.
    match_breakdown_json: Mapped[dict | None] = mapped_column(JSONB)
    tier: Mapped[Tier | None] = mapped_column(Enum(Tier, native_enum=False, length=25))
    # ── Hiring pipeline (migration 0018) ─────────────────────────────────────
    # `application_source` records WHERE the candidate came from (their own
    # dashboard vs an external job link); the older `source` above records HOW
    # they reached the job (databank match vs fresh application). They answer
    # different questions, so both are kept.
    application_source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="direct", server_default="direct"
    )
    #: Type of procurement (migration 0022): applied | sourced | databank.
    #: NOT NULL with a DB-level CHECK, defaulting to `applied` so a link
    #: created by any older code path is still a valid row.
    source_type: Mapped[str] = mapped_column(
        String(20), nullable=False,
        default=SOURCE_TYPE_APPLIED, server_default=SOURCE_TYPE_APPLIED,
    )
    #: Denormalised current stage of the 10-step pipeline. `pipeline_status`
    #: remains the authoritative append-only history; this column exists so the
    #: candidate table can be sorted and filtered without a correlated
    #: subquery per row. The transition service writes both together.
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="applied", server_default="applied"
    )
    status_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Human-readable stage label shown to the candidate ("Technical
    #: Assessment"), kept beside the machine value so the two cannot drift.
    current_stage: Mapped[str | None] = mapped_column(String(100))

    # ── Mandatory application fields (migration 0030, spec §7) ───────────────
    # Current CTC, expected CTC, notice period, joining date, document
    # readiness and the candidate's own words on why the role interests them.
    # Captured on the application form, NEVER scored or interpreted, and shown
    # to the recruiter exactly as submitted. Nullable because applications
    # created before 2026-07-30 predate the fields; every new application is
    # refused without them (services/application_validation).
    validation_json: Mapped[dict | None] = mapped_column(JSONB)

    # HR grants Hiring Manager access per profile (FR-8.1)
    hm_access_granted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )


@event.listens_for(JobCandidateLink, "before_insert")
def _derive_source_type(mapper, connection, target: "JobCandidateLink") -> None:
    """Fill `source_type` from `application_source` when nobody set it.

    The candidate portal's apply handler already records `application_source =
    'sourced'` when the applicant arrived through an externally shared job
    link, and that flag is exactly the signal the "via external link" marker on
    the job page is derived from. Reusing it here means a public-link
    application is tagged `sourced` without every call site having to remember
    a second field, and it is the same rule migration 0022 backfills history
    with, so old and new rows agree.

    Only ever UPGRADES the default: an explicit `databank` or `sourced` from a
    caller is left exactly as given.
    """
    if getattr(target, "source_type", None) not in (None, SOURCE_TYPE_APPLIED):
        return
    # A link the matching pipeline minted from the shared Databank pool is a
    # databank procurement, whatever else is on the row.
    if getattr(target, "source", None) == LinkSource.databank:
        target.source_type = SOURCE_TYPE_DATABANK
    elif getattr(target, "application_source", None) == "sourced":
        target.source_type = SOURCE_TYPE_SOURCED
    else:
        target.source_type = SOURCE_TYPE_APPLIED


class VerificationRequest(Base, UUIDPKMixin, CreatedAtMixin):
    """One per previous employer (max 3, employer_seq 1–3). Fresh candidates
    only — Databank profiles never re-enter this flow (claude.md rule 7)."""
    __tablename__ = "verification_requests"
    __table_args__ = (
        UniqueConstraint("profile_id", "employer_seq", name="uq_verification_employer_seq"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    employer_seq: Mapped[int] = mapped_column(Integer, nullable=False)  # 1..3
    employer_email: Mapped[str] = mapped_column(String(320), nullable=False)
    employer_name: Mapped[str | None] = mapped_column(String(255))
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)  # single-use, signed
    status: Mapped[VerificationStatus] = mapped_column(
        Enum(VerificationStatus, native_enum=False, length=20),
        nullable=False, default=VerificationStatus.pending,
    )
    submitted_via: Mapped[SubmittedVia | None] = mapped_column(
        Enum(SubmittedVia, native_enum=False, length=15)
    )
    # Designation, DOJ, DOE, CTC, gross, NOC, exit formalities, BGV, proofs…
    response_json: Mapped[dict | None] = mapped_column(JSONB)
    override_reason: Mapped[str | None] = mapped_column(Text)  # HR override path, audit-logged
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Interview(Base, UUIDPKMixin, CreatedAtMixin):
    __tablename__ = "interviews"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    job_candidate_link_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_candidate_links.id", ondelete="CASCADE"), nullable=False
    )
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_from_email: Mapped[str | None] = mapped_column(String(320))  # tenant's verified domain only
    ics_uid: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)


class PipelineStatusEntry(Base, UUIDPKMixin):
    """Mandatory status history (FR-8.4). Latest row per link is the current
    status; hold requires remarks (enforced in service layer)."""
    __tablename__ = "pipeline_status"
    __table_args__ = (Index("ix_pipeline_status_link", "job_candidate_link_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    job_candidate_link_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_candidate_links.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[PipelineStatus] = mapped_column(
        # length matches the varchar(30) migration 0018 actually created. The
        # old 15 predates the 10-stage vocabulary and is too short for
        # "assessment_in_progress" (22), so it would have started truncating on
        # any database created from the models rather than from the migrations.
        Enum(PipelineStatus, native_enum=False, length=30), nullable=False
    )
    remarks: Mapped[str | None] = mapped_column(Text)
    set_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default="now()", nullable=False
    )


class CandidateTeamReview(Base, UUIDPKMixin, CreatedAtMixin):
    """One hiring-team member's durable rating and remarks for a candidate.

    A reviewer owns one row per job/candidate link and may refine it. Other
    reviewers' rows remain visible so the decision reflects the whole panel,
    not whichever note happened to be written last.
    """
    __tablename__ = "candidate_team_reviews"
    __table_args__ = (
        UniqueConstraint(
            "job_candidate_link_id",
            "reviewer_user_id",
            name="uq_candidate_team_review_reviewer",
        ),
        Index("ix_candidate_team_reviews_link", "job_candidate_link_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    job_candidate_link_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_candidate_links.id", ondelete="CASCADE"),
        nullable=False,
    )
    reviewer_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    rating: Mapped[str] = mapped_column(String(20), nullable=False)
    remarks: Mapped[str] = mapped_column(Text, nullable=False)
    ai_rewritten_remarks: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default="now()", onupdate=datetime.utcnow, nullable=False
    )
