"""Spoken assessment answers (migration 0123, `voice_answers`).

One spoken answer to one prose turn, from capture to transcript, per
assessment conversation and tenant-scoped. The AUDIO is transient: it is
deleted, HEAD-confirmed, once it is transcribed. The TRANSCRIPT is final and is
the answer that is evaluated (Appendix B section 3).

The clock is paused while a spoken answer is transcribed. That pause is an
`assessment_pauses` row (`models/assessment_pause.py`, migration 0125), the
one record of every reason the turn clock stops.
"""
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin

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
