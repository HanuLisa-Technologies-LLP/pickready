"""A stretch of time during which an assessment's clock does not run
(migration 0125, PLAN-p3 3.4 and 3.7).

ONE TABLE FOR EVERY REASON THE CLOCK STOPS
------------------------------------------
Three things stop a candidate's clock, and they are owned by three different
parts of the product:

    device_loss     the camera or microphone stopped (services/proctoring)
    transcription   a spoken answer is being transcribed, or its transcription
                    failed and the candidate is switching to typing
                    (the voice answer route)
    warning         a blocking proctoring warning is on the screen

They share this table because the question the turn timer asks is the same
for all three ("how much of this turn was the clock stopped for"), and three
tables would be three places for the timer to forget one. The reason is a
CHECK-constrained column, not a free string.

WHY THE STATE IS THE ROW
------------------------
A device pause is open exactly when a `device_loss` row has no `ended_at` and
its `expires_at` has not passed. There is no `paused` flag on the proctoring
session beside it: a flag and a row that must agree are two records of one
fact, and the day they disagree the candidate is either locked out of an
assessment that is running or answering one that is paused. The number of
device pauses a session has used is the COUNT of its `device_loss` rows, for
the same reason.

`expires_at` IS A CAP, NOT A DEADLINE SOMEBODY MUST ENFORCE. A pause whose
`expires_at` has passed is over for the clock at that instant whether or not a
request ever came back to close it, so a warning nobody acknowledged stops the
clock for at most `assessment_warning_pause_max_seconds`, and a device loss
for at most the grace. `services/assessment_conversation/pauses.py` is the one
reader that applies it.

NOTHING HERE IS MEDIA OR ANSWER TEXT. Identifiers and times only.
"""
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin

PAUSE_DEVICE_LOSS = "device_loss"
PAUSE_TRANSCRIPTION = "transcription"
PAUSE_WARNING = "warning"
PAUSE_REASONS: tuple[str, ...] = (PAUSE_DEVICE_LOSS, PAUSE_TRANSCRIPTION, PAUSE_WARNING)


class AssessmentPause(Base, UUIDPKMixin, CreatedAtMixin):
    __tablename__ = "assessment_pauses"
    __table_args__ = (
        CheckConstraint(
            "reason IN ('device_loss', 'transcription', 'warning')",
            name="ck_assessment_pauses_reason",
        ),
        CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at",
            name="ck_assessment_pauses_ended_after_start",
        ),
        CheckConstraint(
            "expires_at IS NULL OR expires_at >= started_at",
            name="ck_assessment_pauses_expires_after_start",
        ),
        Index("ix_assessment_pauses_conversation", "conversation_id", "started_at"),
        # At most one OPEN pause per reason per conversation. The service
        # closes an expired row before opening the next one, and a concurrent
        # second open lands on this index and reads the first instead.
        Index(
            "uq_assessment_pauses_open",
            "conversation_id",
            "reason",
            unique=True,
            postgresql_where=text("ended_at IS NULL"),
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
    reason: Mapped[str] = mapped_column(String(20), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: When the pause was closed by the thing that opened it (a recovery, an
    #: acknowledgement, a transcript). NULL while open.
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: The latest instant the pause can count for, whatever else happens.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: What opened it, when that is a row: the proctoring event for a device
    #: loss or a warning, the voice answer for a transcription. Not a foreign
    #: key, because the three reasons point at three different tables.
    ref_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
