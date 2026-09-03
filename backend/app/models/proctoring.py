"""Proctoring: sessions, events and reports (migration 0076).

THE FOUR RULES THIS SCHEMA EXISTS TO KEEP
-----------------------------------------
1. NO MEDIA IS EVER STORED. Not a frame, not an image, not an audio buffer, not
   in any column here, not in object storage, not on disk. Camera frames are
   inferred over in the candidate's browser and discarded; audio chunks are
   analysed in memory by the analysis service and destroyed. What reaches these
   tables is identifiers, timings, aggregates and model confidences.
   `tests/test_proctoring_no_media.py` fails the build if a write path for a
   frame, an image or an audio file appears anywhere in the proctoring module.
2. A FACE DESCRIPTOR IS NOT AN IMAGE. `face_descriptor_baseline` is a
   128-float vector produced by a recognition network. It cannot be inverted
   into a photograph and is stored only so a later descriptor can be compared
   against it by Euclidean distance.
3. PROCTORING NEVER TOUCHES A SCORE. Nothing here is read by any scorer, by the
   Tatva matrix, by Miti, by the dashboard's triage number or by any ranking
   query. The report is appended to the PRISM Report as its final,
   informational section and nowhere else.
4. THE SERVER DECIDES. `warnings_used` is written only by the ingestion service
   from the shared counter it holds; the browser requests a warning and the
   server decides whether one is issued.
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin

# ── Session outcomes (proctoring section 5) ──────────────────────────────────
OUTCOME_ACTIVE = "active"
OUTCOME_COMPLETED = "completed"
OUTCOME_TERMINATED_INTEGRITY = "terminated_integrity"
OUTCOME_TERMINATED_WARNINGS = "terminated_warnings"
OUTCOME_ABANDONED = "abandoned"
OUTCOME_TECHNICAL_FAILURE = "technical_failure"
SESSION_OUTCOMES: tuple[str, ...] = (
    OUTCOME_ACTIVE,
    OUTCOME_COMPLETED,
    OUTCOME_TERMINATED_INTEGRITY,
    OUTCOME_TERMINATED_WARNINGS,
    OUTCOME_ABANDONED,
    OUTCOME_TECHNICAL_FAILURE,
)
#: The outcomes in which the session is over and no further event is accepted.
ENDED_OUTCOMES: frozenset[str] = frozenset(SESSION_OUTCOMES) - {OUTCOME_ACTIVE}

QUALITY_GOOD = "good"
QUALITY_DEGRADED = "degraded"
QUALITY_POOR = "poor"
SESSION_QUALITIES: tuple[str, ...] = (QUALITY_GOOD, QUALITY_DEGRADED, QUALITY_POOR)

# ── The recruiter's one setting (proctoring section 6) ───────────────────────
POLICY_TERMINATE = "terminate"
POLICY_CONTINUE_AND_NOTE = "continue_and_note"
WARNING_POLICIES: tuple[str, ...] = (POLICY_TERMINATE, POLICY_CONTINUE_AND_NOTE)
#: Never terminate by default without an explicit choice.
DEFAULT_WARNING_POLICY = POLICY_CONTINUE_AND_NOTE

#: The width of a face-api.js recognition descriptor. Pinned by a database
#: CHECK as well, so a client sending a different network's vector is refused.
FACE_DESCRIPTOR_WIDTH = 128

REPORT_VERSION = "1"


class ProctoringSession(Base, UUIDPKMixin, CreatedAtMixin):
    __tablename__ = "proctoring_sessions"
    __table_args__ = (
        UniqueConstraint("conversation_id", name="uq_proctoring_session_conversation"),
        Index("ix_proctoring_sessions_link", "job_candidate_link_id"),
        Index("ix_proctoring_sessions_job", "job_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    conversation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("assessment_conversations.id", ondelete="CASCADE"), nullable=False)
    job_candidate_link_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("job_candidate_links.id", ondelete="CASCADE"), nullable=False)
    candidate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    #: The explicit "I understand and agree" action, with its timestamp. A
    #: session row cannot exist without it: consent is NOT NULL.
    consented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str] = mapped_column(String(30), nullable=False, default=OUTCOME_ACTIVE, server_default=OUTCOME_ACTIVE)
    #: Internal reason code (the catalog event type that ended the session).
    #: Never shown to a recruiter as-is; the report phrases it.
    termination_reason: Mapped[str | None] = mapped_column(Text)
    warnings_used: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    face_descriptor_baseline: Mapped[list[float] | None] = mapped_column(ARRAY(Float))
    device_context: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    system_check: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    session_quality: Mapped[str] = mapped_column(String(10), nullable=False, default=QUALITY_GOOD, server_default=QUALITY_GOOD)
    behaviour_profile_json: Mapped[dict | None] = mapped_column(JSONB)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProctoringEvent(Base, UUIDPKMixin, CreatedAtMixin):
    """One detected thing. `event_type` is a `services/proctoring/catalog.py`
    identifier and `path` is the consequence path the SERVER assigned to it.
    `metadata_json` carries event-specific aggregates (a face count, an
    object class, a typing-rate ratio); it never carries media and never
    carries answer text."""

    __tablename__ = "proctoring_events"
    __table_args__ = (
        Index("ix_proctoring_events_session", "proctoring_session_id", "occurred_at"),
        Index("ix_proctoring_events_occurred", "occurred_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    proctoring_session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("proctoring_sessions.id", ondelete="CASCADE"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    path: Mapped[str] = mapped_column(String(1), nullable=False)
    warning_issued: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    warning_number: Mapped[int | None] = mapped_column(SmallInteger)
    confidence: Mapped[float | None] = mapped_column(Float)
    question_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("candidate_questions.id", ondelete="SET NULL"))
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")


class ProctoringReport(Base, UUIDPKMixin, CreatedAtMixin):
    """The recruiter-facing report, generated once after the session ends.

    `report_content` is words only: sentences, lists of sentences and a table
    of sentences. It carries no numeric field so it can be embedded in the
    delivered PRISM payload under `schemas/reports.NumberFreeDelivery`. Counts
    and durations are spelled out by `services/proctoring/report.py`.
    """

    __tablename__ = "proctoring_reports"
    __table_args__ = (
        UniqueConstraint("proctoring_session_id", name="uq_proctoring_report_session"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    proctoring_session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("proctoring_sessions.id", ondelete="CASCADE"), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    report_content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    report_version: Mapped[str] = mapped_column(String(10), nullable=False, default=REPORT_VERSION)
