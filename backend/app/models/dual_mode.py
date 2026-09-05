"""Dual-mode assessment records (2026-09-05 Dual-Mode Assessment Specification).

Two tables, both per assessment SESSION:

  `assessment_consents`   the consent audit record (spec 3.4). Consent is a
                          hard prerequisite for starting an assessment in
                          EITHER mode; the gate in `services/assessment_consent`
                          reads this table, never a stamp elsewhere.
  `video_recordings`      the video-interview recording lifecycle (spec 5, 16
                          and the video spec's sections 7-10). The transitions
                          live in `services/video/lifecycle.py`.

PROCTORING STORES NO MEDIA AND THAT IS UNCHANGED. An assessment video is a
separately consented artifact of the video-interview mode. Nothing under
`services/proctoring/` reads or writes these rows, and no scorer imports
`services/video` (pinned by `tests/test_dual_mode_assessment.py`).
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin

MODE_CONVERSATIONAL = "conversational"
MODE_VIDEO_INTERVIEW = "video_interview"
ASSESSMENT_MODES = (MODE_CONVERSATIONAL, MODE_VIDEO_INTERVIEW)


class AssessmentConsent(Base, UUIDPKMixin, CreatedAtMixin):
    """One granted consent, for one assessment session, in one mode (spec 3.4).

    ONLY GRANTED CONSENTS ARE STORED, by database CHECK. A declined consent
    screen collects nothing, so there is nothing to audit; a stored 'declined'
    row would imply collection happened without agreement.

    The versions are recorded because the wording is CONFIGURABLE
    (`assessment_consent_*` settings): a dispute about what a candidate agreed
    to is settled by which version they accepted, not by whatever the settings
    say today.

    UNIQUE per (conversation, mode): a candidate who switches mode before
    starting consents again, because the two modes collect different things.
    """

    __tablename__ = "assessment_consents"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "assessment_mode",
            name="uq_assessment_consent_session_mode",
        ),
        Index("ix_assessment_consents_conversation", "conversation_id"),
        CheckConstraint(
            "consent_status = 'granted'",
            name="ck_assessment_consents_granted_only",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    candidate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False)
    conversation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("assessment_conversations.id", ondelete="CASCADE"), nullable=False)
    job_candidate_link_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("job_candidate_links.id", ondelete="CASCADE"), nullable=False)
    assessment_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    consent_status: Mapped[str] = mapped_column(String(20), nullable=False, default="granted")
    consented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consent_version: Mapped[str] = mapped_column(String(40), nullable=False)
    privacy_policy_version: Mapped[str] = mapped_column(String(40), nullable=False)
    terms_version: Mapped[str] = mapped_column(String(40), nullable=False)


class VideoRecording(Base, UUIDPKMixin, CreatedAtMixin):
    """One video-interview recording and its processing lifecycle.

    STATUS is one of `services/video/lifecycle.STATUSES`, moved only through
    `lifecycle.advance` so an illegal jump raises instead of persisting. Each
    failure state names the STEP that failed (upload, processing,
    transcription, compression, storage), so the retry endpoint retries the
    right thing.

    THE S3 KEYS CARRY IDS ONLY (spec section 5 / video spec section 9): a
    conversation id and this row's id, never a candidate name or email.
    Built by `services/video/keys.py` and nowhere else.

    RAW-VIDEO LIFECYCLE (video spec section 10): the raw object is a
    processing artifact. It is deleted only after the compressed object is
    uploaded AND verified (HEAD, non-zero size, duration within tolerance),
    and the deletion itself is verified by a HEAD before `raw_deleted` is set,
    exactly as project-intake originals are. A failed deletion increments
    `raw_delete_failures` and never blocks the recording from `ready`.

    `question_marks_json` is the SERVER-stamped question timeline:
    [{question_id, question_type, offset_seconds}], stamped when each question
    is displayed. The transcript is segmented by it; a client-reported
    timeline would be a timeline the client chose.
    """

    __tablename__ = "video_recordings"
    __table_args__ = (
        Index("ix_video_recordings_conversation", "conversation_id", "created_at"),
        Index("ix_video_recordings_link", "job_candidate_link_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    conversation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("assessment_conversations.id", ondelete="CASCADE"), nullable=False)
    candidate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False)
    job_candidate_link_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("job_candidate_links.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    #: The MIME type the browser's MediaRecorder reported (e.g. video/webm).
    source_format: Mapped[str | None] = mapped_column(String(60))
    #: The long-term container after compression. 'mp4' once ready.
    stored_format: Mapped[str | None] = mapped_column(String(20))
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    compressed_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    s3_raw_key: Mapped[str | None] = mapped_column(String(500))
    s3_compressed_key: Mapped[str | None] = mapped_column(String(500))
    raw_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    raw_delete_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    question_marks_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    #: Operator-facing description of the failure `status` names. Never
    #: candidate content, never a transcript excerpt.
    error_detail: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
