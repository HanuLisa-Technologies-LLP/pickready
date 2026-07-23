import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean, DateTime, Enum, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
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
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20))
    city: Mapped[str | None] = mapped_column(String(120))
    age: Mapped[int | None] = mapped_column(Integer)
    gender: Mapped[str | None] = mapped_column(String(30))
    consent_databank: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Profile(Base, UUIDPKMixin, CreatedAtMixin):
    """The Profile (PRD glossary): resume + 40-aspect responses + employer
    verification for one candidate. Embedding powers the semantic stage."""
    __tablename__ = "profiles"
    __table_args__ = (Index("ix_profiles_candidate", "candidate_id"),)

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False
    )
    source_tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    resume_url: Mapped[str | None] = mapped_column(String(1000))  # Cloudinary
    resume_text: Mapped[str | None] = mapped_column(Text)  # extracted; tsvector col in migration
    aspects_json: Mapped[dict | None] = mapped_column(JSONB)  # {"1": {...}, ..., "40": {...}}
    parsed_fields_json: Mapped[dict | None] = mapped_column(JSONB)  # skills, experience, education, employment_history
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024))  # BGE-M3
    aspects_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
    # HR grants Hiring Manager access per profile (FR-8.1)
    hm_access_granted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


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
        Enum(PipelineStatus, native_enum=False, length=15), nullable=False
    )
    remarks: Mapped[str | None] = mapped_column(Text)
    set_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default="now()", nullable=False
    )
