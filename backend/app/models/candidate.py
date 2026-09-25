import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean, DateTime, Enum, Float, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint, event, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin
from app.models.enums import (
    LinkSource, PipelineStatus, Tier,
)


class Candidate(Base, UUIDPKMixin, CreatedAtMixin):
    """tenant_id NULL: a candidate profile can be shared across tenants via
    the Databank (ESD §4). consent_databank mirrors Aspect 40 (FR-4.2)."""
    __tablename__ = "candidates"
    __table_args__ = (
        Index("ix_candidates_email", "email"),
        Index("ix_candidates_tenant_created", "tenant_id", "created_at"),
        # Migration 0120: the case-insensitive lookup the one resolver
        # (`services/candidate_identity`) reads through, and ONE record per
        # signed-in user, enforced by the database rather than by the code.
        Index("ix_candidates_email_lower", text("lower(email)")),
        Index(
            "uq_candidates_user_id",
            "user_id",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
        ),
    )

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

    # ── Data-retention consents (migration 0083, Consent & Privacy spec
    #    2026-09-05) ─────────────────────────────────────────────────────────
    # Same boolean-with-timestamp pattern the proctoring session uses for its
    # consent stamp, stored beside `consent_databank` because they answer the
    # same class of question. Semantics of each pair:
    #   True  = retain for future jobs (client portal Download enabled)
    #   False = this job only (View-only)
    #   NULL  = never asked, treated as False, the safe direction
    # Read ONLY through services/retention_consent.py so the NULL rule cannot
    # drift between the PDF route, the serializer and video delivery.
    retain_assessment_consent: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    retain_assessment_consented_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    retain_video_consent: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    retain_video_consented_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Consent renewal and the inactivity clock (migration 0100, feature 8) ─
    # FOUR STAMPS AND NO STATUS COLUMN. The stage is derived by
    # `services/consent_lifecycle.stage_for`, because a stored stage is a
    # second copy of what these columns already determine, and the sweep would
    # then erase people on the strength of a value nothing has re-checked.
    #
    # NULL is a real state for all four and each means one thing. The two
    # "sent" stamps are what make the sweep safe to run LATE: a grace window
    # starts when a letter was actually sent, so a scheduler outage delays the
    # cycle rather than skipping somebody straight to deletion.
    consent_renewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    consent_reminder_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    consent_final_warning_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: The INACTIVITY letter (migration 0108). A fourth stamp rather than a
    #: reuse of the consent ones, because the two clocks are independent (C6)
    #: and one letter must never latch the other's stage. Before it existed the
    #: dormancy rule read one boolean and erased, with no warning at all.
    dormancy_warning_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: The one-click renewal link, stored as a SHA-256 DIGEST and never as the
    #: token itself, so a reader of this table cannot replay a link. Cleared by
    #: the renewal, which is what makes it single use.
    #: See `services/consent_renewal`.
    consent_renewal_token_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    consent_renewal_token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Anything the candidate DID, or was sent, that means the platform is
    #: still working for them. Read through
    #: `consent_lifecycle.engagement_at_for` so the NULL rule cannot drift
    #: between the sweep and anything else.
    last_engagement_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

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

    # Background verification (migration 0095). Two columns rather than a
    # table, because they are one answer and one stamp about THIS person and
    # they are read on every profile load.
    #
    # `employment_background` is the candidate's own declaration, in their own
    # words: fresher or experienced. It is never inferred from a parsed resume,
    # because a parsed history is evidence and this answer decides whether an
    # offer can be blocked. NULL means the question has not been answered yet,
    # which is distinct from either answer and never blocks anything.
    employment_background: Mapped[str | None] = mapped_column(String(20), nullable=True)
    #: THE IMMUTABILITY GATE. Once stamped, `candidate_employments` is closed
    #: to writes: the service layer refuses, and a Postgres trigger refuses
    #: independently so a future route or a psql session cannot quietly rewrite
    #: what an employer is being asked to confirm. Read through
    #: `models/employment.finalized` so no caller invents a second definition
    #: of "submitted".
    employment_history_finalized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Profile(Base, UUIDPKMixin, CreatedAtMixin):
    """The Profile (PRD glossary): one resume snapshot for one candidate,
    with the My Profile answers that were true when it was taken
    (`aspects_json`). Embedding powers the semantic stage."""
    __tablename__ = "profiles"
    __table_args__ = (
        Index("ix_profiles_candidate", "candidate_id"),
        Index("ix_profiles_source_tenant_created", "source_tenant_id", "created_at"),
    )

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
    #: The intake scan for hidden content (migration 0092, W9.2). Shape is
    #: `services/projects/invisible_text.IntakeScan.as_json()`: which techniques
    #: were found, how often, a bounded sample, and the recruiter-facing
    #: sentence. It hangs off the PROFILE and not off `candidates`, because a
    #: profile IS one resume and a candidate with three resumes needs three
    #: answers; "has this person ever submitted a flagged file" is one join.
    #: NULL means the resume predates the scan, which is a different fact from
    #: a clean scan and is deliberately not backfilled to one.
    #: IT AUTHORISES NOTHING. Nothing in the product may read it as a reason to
    #: reject, rank or filter; it exists so a human can look and so a challenged
    #: decision has a record.
    intake_scan_json: Mapped[dict | None] = mapped_column(JSONB)
    aspects_json: Mapped[dict | None] = mapped_column(JSONB)  # {"1": {...}, ..., "40": {...}}
    parsed_fields_json: Mapped[dict | None] = mapped_column(JSONB)  # skills, experience, education, employment_history
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024))  # voyage-4, EMBEDDING_DIM
    # ── Embedding provenance (migration 0062) ────────────────────────────────
    # WHICH MODEL PRODUCED THE VECTOR ABOVE, answerable by query rather than by
    # inference. The column is `vector(1024)` and so was the BGE-M3 vector that
    # preceded Voyage, so width proves nothing: a row written before the
    # single-vendor consolidation and a row written after it are
    # indistinguishable without this. NULL means "produced by an unrecorded
    # model" and is deliberately not backfilled to Voyage, because that would
    # assert something nobody can check. `app.scripts.reembed` selects on it.
    embedding_model: Mapped[str | None] = mapped_column(String(64))
    #: OUR version of how the embedded text was built (input type, output
    #: dimension, template), because the vendor does not version a model id.
    embedding_contract_version: Mapped[str | None] = mapped_column(String(32))
    embedding_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Where a re-embedding run writes before the swap, so a run that fails
    #: part way never leaves the live column serving two vector spaces.
    embedding_shadow: Mapped[list[float] | None] = mapped_column(Vector(1024))
    aspects_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ── Type of procurement (2026-07-28) ─────────────────────────────────────────
# Every candidate on a job arrived one of exactly three ways:
#
#   applied  : they applied themselves, from the portal board OR through the
#              public /apply page a third-party post linked to. Where they
#              clicked is `application_source`, a separate column.
#   sourced  : a recruiter put their resume on the job without them applying.
#              The pipeline has a `sourced` STAGE for the same fact (Gate 5).
#   databank : the recruitment team bulk-uploaded their resume.
#
# This is DISPLAY AND FILTER DATA ONLY. All three types go through identical
# AI parsing, embedding, matching, ranking and assessment; nothing in this
# codebase may branch on `source_type` to change how a candidate is processed.
#
# claude.md rule 7 (databank candidates never re-enter the verification
# flow) is untouched by this: it is about the EMPLOYER VERIFICATION flow,
# which keys off `source` (databank | fresh). Two different columns, two
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
        Index("ix_jcl_tenant_job_created", "tenant_id", "job_id", "created_at"),
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
    # ── The retired matcher's output: HISTORY, readable and frozen (S4) ──────
    # Written by nothing since migration 0122. Kept because a live row may
    # still carry them and dropping a column that may hold customer data is an
    # owner decision. The ranked table reads the Yukti columns below instead;
    # 0122 carried every scored row across as a `legacy` Yukti reading.
    match_score: Mapped[float | None] = mapped_column(Float)
    match_rationale: Mapped[str | None] = mapped_column(Text)
    match_breakdown_json: Mapped[dict | None] = mapped_column(JSONB)
    tier: Mapped[Tier | None] = mapped_column(Enum(Tier, native_enum=False, length=25))
    # ── Yukti (migration 0122) ───────────────────────────────────────────────
    # Written ONLY by `services/yukti/scoring.apply_outcome`, under the
    # per-link advisory lock. The pre-assessment score is INTERNAL: no schema
    # serializes it, and the blended ranking score is derived in SQL at read
    # time rather than stored (CONTRACT v2). CHECKs pin the vocabulary, the
    # 0..100 range, "a number exactly when scored or legacy" and "a reason
    # exactly when not assessed".
    yukti_pre_score: Mapped[float | None] = mapped_column(Float)
    yukti_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    yukti_failure_reason: Mapped[str | None] = mapped_column(String(40))
    evidence_tags_json: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    yukti_provenance_json: Mapped[dict | None] = mapped_column(JSONB)
    yukti_scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: The profile the reading was made from. Differs from `profile_id` when
    #: the application's resume was replaced after the reading, which is how
    #: the table knows the reading is stale.
    yukti_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL")
    )
    # ── Hiring pipeline (migration 0018) ─────────────────────────────────────
    # `application_source` records WHERE an applicant clicked: `direct` (the
    # portal board) or `external_link` (the public /apply page). `sourced` is
    # still admitted by the CHECK for rows migration 0018 backfilled, and is
    # never written any more. The older `source` above records HOW they
    # reached the job (databank match vs fresh application). They answer
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
    """Fill `source_type` when nobody set it: `databank` for a link minted from
    the shared Databank pool, otherwise `applied`.

    IT NO LONGER READS `application_source`, and that was the defect. It used
    to map `application_source == 'sourced'` (an applicant who arrived through
    an externally shared link) onto `source_type = sourced`, so a person who
    READ the job and APPLIED was labelled as a recruiter's upload who never
    had, the exact confusion Gate 5 exists to prevent. Where somebody clicked
    is provenance about an applicant; it never makes them a non-applicant.

    Only ever UPGRADES the default: an explicit `databank` or `sourced` from a
    caller (the recruiter upload paths) is left exactly as given.
    """
    if getattr(target, "source_type", None) not in (None, SOURCE_TYPE_APPLIED):
        return
    # A link the matching pipeline minted from the shared Databank pool is a
    # databank procurement, whatever else is on the row.
    if getattr(target, "source", None) == LinkSource.databank:
        target.source_type = SOURCE_TYPE_DATABANK
    else:
        target.source_type = SOURCE_TYPE_APPLIED




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
