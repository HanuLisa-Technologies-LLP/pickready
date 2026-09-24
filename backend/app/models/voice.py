"""The assessment turn clock's pauses and the spoken answers (migration 0123).

Two tables, both per assessment conversation and both tenant-scoped:

  `assessment_pauses`  every interval the turn clock stops for. The clock is
                       the SERVER's (Appendix B section 3): a pause is a row
                       the server wrote, never a number the client reported.
  `voice_answers`      one spoken answer, from capture to transcript. The
                       audio is transient and deleted, HEAD-confirmed, once it
                       is transcribed; the transcript is final and is the
                       answer that is evaluated.
"""
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin

# ── Pause reasons ─────────────────────────────────────────────────────────────
#: The camera or the microphone stopped; opened and closed by proctoring.
PAUSE_DEVICE_LOSS = "device_loss"
#: A spoken answer is being transcribed, or a failed transcription is on screen.
PAUSE_TRANSCRIPTION = "transcription"
#: A blocking proctoring warning is on screen.
PAUSE_WARNING = "warning"
PAUSE_REASONS: tuple[str, ...] = (PAUSE_DEVICE_LOSS, PAUSE_TRANSCRIPTION, PAUSE_WARNING)

# ── Voice answer lifecycle ────────────────────────────────────────────────────
VOICE_RECORDING = "recording"
VOICE_UPLOADED = "uploaded"
VOICE_TRANSCRIBING = "transcribing"
VOICE_TRANSCRIBED = "transcribed"
VOICE_FAILED = "failed"
VOICE_CONSUMED = "consumed"
VOICE_STATUSES: tuple[str, ...] = (
    VOICE_RECORDING,
    VOICE_UPLOADED,
    VOICE_TRANSCRIBING,
    VOICE_TRANSCRIBED,
    VOICE_FAILED,
    VOICE_CONSUMED,
)
#: A capture is in flight until a transcript or a failure exists. At most one
#: per turn, by partial UNIQUE index.
VOICE_IN_FLIGHT: tuple[str, ...] = (VOICE_RECORDING, VOICE_UPLOADED, VOICE_TRANSCRIBING)


def _in_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class AssessmentPause(Base, UUIDPKMixin, CreatedAtMixin):
    """One interval the turn clock does not count.

    An open pause has no `ended_at`. An `ended_at` in the FUTURE is a pause
    that is already scheduled to end: a failed transcription stays paused for
    a short window so the candidate can read what happened, and needs no sweep
    to close it.
    """

    __tablename__ = "assessment_pauses"
    __table_args__ = (
        CheckConstraint(
            f"reason IN ({_in_list(PAUSE_REASONS)})", name="ck_assessment_pauses_reason"
        ),
        CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at",
            name="ck_assessment_pauses_interval",
        ),
        Index("ix_assessment_pauses_conversation", "conversation_id", "started_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assessment_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    reason: Mapped[str] = mapped_column(String(20), nullable=False)
    ref_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VoiceAnswer(Base, UUIDPKMixin, CreatedAtMixin):
    """One spoken answer to one prose turn.

    `failure_reason` is OPERATOR wording (which step failed and why) and never
    carries anything the candidate said. `transcript_text` is the answer; the
    audio key is cleared of meaning once `audio_deleted_at` is stamped.
    """

    __tablename__ = "voice_answers"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_in_list(VOICE_STATUSES)})", name="ck_voice_answers_status"
        ),
        CheckConstraint(
            "audio_delete_failures >= 0", name="ck_voice_answers_delete_failures"
        ),
        Index("ix_voice_answers_conversation", "conversation_id", "created_at"),
        Index(
            "ix_voice_answers_audio_pending",
            "created_at",
            postgresql_where=text("audio_deleted_at IS NULL AND s3_key IS NOT NULL"),
        ),
        Index(
            "ux_voice_answers_turn_in_flight",
            "conversation_id",
            "turn_seq",
            unique=True,
            postgresql_where=text(f"status IN ({_in_list(VOICE_IN_FLIGHT)})"),
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assessment_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidate_questions.id", ondelete="CASCADE"),
        nullable=False,
    )
    turn_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=VOICE_RECORDING)
    s3_key: Mapped[str | None] = mapped_column(String(500))
    content_type: Mapped[str | None] = mapped_column(String(60))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    audio_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    audio_delete_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    transcribe_job_name: Mapped[str | None] = mapped_column(String(200))
    transcript_text: Mapped[str | None] = mapped_column(Text)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    capture_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    transcribed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assessment_messages.id", ondelete="SET NULL")
    )
