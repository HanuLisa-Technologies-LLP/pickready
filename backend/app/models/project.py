"""Candidate project submissions and their derived evidence (migration 0074).

THE ROW IS THE INTELLIGENCE, NOT THE PROJECT. By product decision (Project
Evidence master brief, 2026-09-01) the original uploaded artifacts are
TEMPORARY: they are staged in object storage under a `project-intake/` prefix
only for the duration of processing and are deleted once the derived evidence
has been validated and persisted. Nothing on this table may ever point at an
original artifact after `original_deleted_at` is stamped, and no permanent
original-project archive exists anywhere in the product. Optional original
retention is a documented FUTURE capability, out of scope now.

"Versioned" evidence here means DECOMPOSED evidence -- the project broken into
structured dimensions (technology, architecture, implementation, testing,
infrastructure, documentation, gaps, uncertainties), each with provenance --
not V1/V2 history of the whole project. `services/projects/evidence.py` builds
that structure; this row stores it.

Candidate-scoped, not tenant-scoped, exactly like `candidates.profile_form_json`:
a project belongs to the person and is reusable evidence across every
application, so it hangs off `candidates`, which is a tenant-NULL shareable
table already.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin

# ── Processing lifecycle ─────────────────────────────────────────────────────
#
# One linear machine with explicit failure states. `processed` means derived
# evidence is persisted AND every temporary original has been confirmed
# deleted; `persisted` means the evidence is durable but at least one temporary
# object still awaits deletion (the sweeper retries it -- evidence is never
# lost to a deletion hiccup, and deletion is never claimed before it happened).

STATUS_SUBMITTED = "submitted"
STATUS_PROCESSING = "processing"
STATUS_PERSISTED = "persisted"
STATUS_PROCESSED = "processed"
STATUS_PARTIALLY_PROCESSED = "partially_processed"
STATUS_FAILED_SECURITY = "failed_security"
STATUS_FAILED_EXTRACTION = "failed_extraction"
STATUS_FAILED_EVIDENCE = "failed_evidence_generation"

#: States a candidate may retry from. `partially_processed` is retryable
#: because the missing half is the AI interpretation, which a later run can
#: supply; the two failed_* states are retryable because the failure may have
#: been transient (storage, a corrupt read).
RETRYABLE_STATUSES: frozenset[str] = frozenset(
    {
        STATUS_PARTIALLY_PROCESSED,
        STATUS_FAILED_SECURITY,
        STATUS_FAILED_EXTRACTION,
        STATUS_FAILED_EVIDENCE,
    }
)

#: States in which derived evidence exists and may be shown or consumed.
EVIDENCE_READY_STATUSES: frozenset[str] = frozenset(
    {STATUS_PERSISTED, STATUS_PROCESSED, STATUS_PARTIALLY_PROCESSED}
)

ALL_STATUSES: frozenset[str] = frozenset(
    {
        STATUS_SUBMITTED,
        STATUS_PROCESSING,
        STATUS_PERSISTED,
        STATUS_PROCESSED,
        STATUS_PARTIALLY_PROCESSED,
        STATUS_FAILED_SECURITY,
        STATUS_FAILED_EXTRACTION,
        STATUS_FAILED_EVIDENCE,
    }
)


class CandidateProject(Base, UUIDPKMixin, CreatedAtMixin):
    """One submitted project and the structured evidence derived from it."""

    __tablename__ = "candidate_projects"
    __table_args__ = (
        Index("ix_candidate_projects_candidate", "candidate_id"),
    )

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidates.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    #: Candidate-provided, hard 100-word maximum enforced at intake. Kept
    #: verbatim: this is the CLAIM side of the claim-versus-observed split and
    #: must never be rewritten by the system.
    description: Mapped[str] = mapped_column(Text, nullable=False)
    repository_url: Mapped[str | None] = mapped_column(String(1000))
    #: files | repository | mixed -- derived from what was actually submitted.
    submission_kind: Mapped[str] = mapped_column(String(20), nullable=False)

    status: Mapped[str] = mapped_column(
        String(40), nullable=False, default=STATUS_SUBMITTED
    )
    #: Machine-readable reason for a failure state; NULL otherwise.
    failure_code: Mapped[str | None] = mapped_column(String(60))
    #: Candidate-safe sentence describing the current state. Never quotes an
    #: underlying exception and never names a storage vendor.
    status_detail: Mapped[str | None] = mapped_column(Text)

    #: Submitted-file METADATA only: [{filename, size_bytes, content_type,
    #: family, supported}]. Never content, never a storage key -- keys live in
    #: `intake_objects_json` until deletion so the two lifecycles cannot mix.
    files_json: Mapped[list | None] = mapped_column(JSONB)
    #: Temporary object-store keys awaiting deletion. Emptied as deletions are
    #: CONFIRMED (head-after-delete), so a non-empty list is always an honest
    #: statement of what still exists.
    intake_objects_json: Mapped[list | None] = mapped_column(JSONB)

    #: The derived Project Evidence Record: decomposed dimensions, candidate
    #: claims vs observed evidence, gaps, uncertainties, provenance,
    #: processing limitations. Built by `services/projects/evidence.py`.
    evidence_json: Mapped[dict | None] = mapped_column(JSONB)
    #: Ranked evidence units/chunks with per-unit provenance, capped by
    #: `project_max_evidence_units`.
    evidence_units_json: Mapped[list | None] = mapped_column(JSONB)
    #: The AI interpretation, stored SEPARATELY from deterministic evidence so
    #: a model inference can never read as extracted fact: claim assessments,
    #: meaningful gaps, validation areas, synthesis. NULL when the reasoning
    #: stage has not succeeded (status `partially_processed`).
    ai_interpretation_json: Mapped[dict | None] = mapped_column(JSONB)
    #: A WORD, never a number: Strong | Moderate | Limited | Insufficient.
    evidence_strength: Mapped[str | None] = mapped_column(String(20))

    #: Processing telemetry: raw/extracted/final sizes, file counts,
    #: supported/unsupported counts, parser names, durations, retry count.
    #: Counts and labels only -- never candidate content.
    telemetry_json: Mapped[dict | None] = mapped_column(JSONB)

    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    original_deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    deletion_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
