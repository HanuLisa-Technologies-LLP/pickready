"""Assessment session records: consent, the recording and its segments.

The module keeps its 2026-09-05 name because migrations and stored rows cite
it, but there is ONE assessment mode now (Vivekium release, 2026-09-24): the
video-interview mode, its mode-selection screen, its answer transcription and
the `assessment_canonical` adapter are DELETED. What survives is the half the
master prompt keeps: the proctored SESSION recording.

Three tables, all per assessment SESSION:

  `assessment_consents`       the consent audit record. Consent is a hard
                              prerequisite for starting an assessment; the
                              gate in `services/assessment_consent` reads this
                              table, never a stamp elsewhere.
  `video_recordings`          the session recording and its lifecycle
                              (`services/video/lifecycle.py`), including the
                              STORED purge date of owner decision D4.
  `video_recording_segments`  the multipart uploads the recording arrives in
                              (migration 0126). The only rows that name a
                              segment's object, so every deletion path reads
                              them before any row is deleted.

READ-ONLY VOCABULARY FOR OLD ROWS. `MODE_VIDEO_INTERVIEW` and
`RECORDING_VIDEO_INTERVIEW` stay so a row written before the removal still
reads (the conversation's `mode` column and the recording's `kind` column
keep their CHECK constraints). Nothing writes either value any more, and
`tests/test_video_interview_mode_removed.py` allowlists this module and no
other for the names.

WHAT HAS NOT CHANGED since the 2026-09-22 owner ruling that assessment media
is stored: nothing under `services/proctoring/` reads or writes these rows,
and no scorer imports `services/video` (`tests/test_session_recording.py` and
`tests/test_proctoring_scoring_isolation.py` pin both directions). Proctoring
decides nothing about the media, and the media pipeline reads no proctoring
state; they meet only at the assessment session both are attached to.
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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin

MODE_CONVERSATIONAL = "conversational"
#: READ-ONLY. The video-interview mode is deleted; this value survives only
#: so a conversation row written before the removal still reads.
MODE_VIDEO_INTERVIEW = "video_interview"
ASSESSMENT_MODES = (MODE_CONVERSATIONAL, MODE_VIDEO_INTERVIEW)

# ── Recording kinds (owner ruling, 2026-09-22) ───────────────────────────────
#: READ-ONLY. A recording that WAS the candidate's answer, from the deleted
#: video-interview mode. Nothing writes this kind any more; the delivery layer
#: still labels an old row that carries it.
RECORDING_VIDEO_INTERVIEW = "video_interview"
#: The recording is the monitoring record of a proctored assessment:
#: compressed, stored and served, and transcribed, segmented and scored by
#: nothing, which is what keeps principle P3 structural.
RECORDING_PROCTORED_SESSION = "proctored_session"
RECORDING_KINDS = (RECORDING_VIDEO_INTERVIEW, RECORDING_PROCTORED_SESSION)
#: The only kind a NEW row can be (migration 0126 moved the server default
#: with it). Rows written before 0110 were backfilled to the interview kind
#: by that migration and keep it.
DEFAULT_RECORDING_KIND = RECORDING_PROCTORED_SESSION

# ── Segment statuses (migration 0126) ────────────────────────────────────────
#: A multipart upload is accepting parts.
SEGMENT_OPEN = "open"
#: CompleteMultipartUpload succeeded; the segment is one S3 object.
SEGMENT_COMPLETED = "completed"
#: AbortMultipartUpload ran (no part ever arrived); there is no object.
SEGMENT_ABORTED = "aborted"
SEGMENT_STATUSES = (SEGMENT_OPEN, SEGMENT_COMPLETED, SEGMENT_ABORTED)


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
    #: ON DELETE SET NULL, and NULLABLE, since migration 0112. It was CASCADE,
    #: and that made this row die with the thing it authorised: job closure
    #: deletes `assessment_conversations`, so the consent record that made the
    #: deletion lawful was destroyed by the deletion itself, leaving nothing
    #: to answer "what did this candidate agree to". Found by
    #: `tests/test_job_closure_erasure.py` when it first asserted the survival
    #: that `erasure.job_closure_erasure` had been claiming in prose since the
    #: day it was written.
    #:
    #: NULL here means the session is gone and the consent stands. The row
    #: still carries the candidate, the application, the mode, the moment and
    #: the three versions, which is everything the dispute it exists for
    #: actually needs. The UNIQUE on (conversation_id, assessment_mode) is
    #: unaffected because NULLS DISTINCT keeps every orphaned row legal.
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("assessment_conversations.id", ondelete="SET NULL"), nullable=True)
    job_candidate_link_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("job_candidate_links.id", ondelete="CASCADE"), nullable=False)
    assessment_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    consent_status: Mapped[str] = mapped_column(String(20), nullable=False, default="granted")
    consented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consent_version: Mapped[str] = mapped_column(String(40), nullable=False)
    privacy_policy_version: Mapped[str] = mapped_column(String(40), nullable=False)
    terms_version: Mapped[str] = mapped_column(String(40), nullable=False)


class VideoRecording(Base, UUIDPKMixin, CreatedAtMixin):
    """The proctored session's recording and its processing lifecycle.

    ONE KIND IS WRITTEN: `proctored_session`, the monitoring record of a
    proctored assessment. It goes `uploaded -> compressing` and never through
    a transcription step, because there is none: the video-interview mode that
    had one is deleted, and a monitoring record reaching a scorer's input path
    is exactly what principle P3 forbids.

    STATUS is one of `services/video/lifecycle.STATUSES`, moved only through
    `lifecycle.advance` so an illegal jump raises instead of persisting. Each
    failure state names the STEP that failed.

    THE BYTES ARRIVE IN SEGMENTS (`VideoRecordingSegment`, migration 0126):
    one S3 multipart upload per segment, a new segment after every device
    recovery. `s3_raw_key` is set only on rows written before 0126, when the
    whole recording was one object; the pipeline and every deletion path read
    both.

    THE S3 KEYS CARRY IDS ONLY: a conversation id and this row's id, never a
    candidate name or email. Built by `services/video/keys.py` and nowhere
    else.

    RAW LIFECYCLE: the segment objects are a processing artifact. They are
    deleted only after the compressed object is uploaded AND verified (HEAD,
    size, duration within tolerance), each deletion is HEAD-confirmed, and a
    failed deletion increments `raw_delete_failures` and never blocks
    `ready`; `pickready.reconcile_assessment_recordings` retries it hourly.

    RETENTION (owner decision D4): `media_purge_due_at` is stamped ONCE when
    the recording is finalized, as the earlier of the session end plus
    `assessment_media_retention_days` and the job's closure purge date if the
    job is already closed. The sweep reads the LEAST of this and
    `jobs.assessment_purge_due_at`, so a later closure still wins, and a
    settings change never moves a stored deadline.

    `question_marks_json` is HISTORY ONLY: the deleted interview mode stamped
    its question timeline there. The column is kept (no drop of a column that
    may hold rows, S4) and nothing writes it.
    """

    __tablename__ = "video_recordings"
    __table_args__ = (
        Index("ix_video_recordings_conversation", "conversation_id", "created_at"),
        Index("ix_video_recordings_link", "job_candidate_link_id"),
        CheckConstraint(
            "kind IN ('video_interview', 'proctored_session')",
            name="ck_video_recordings_kind",
        ),
        #: The retention sweep reads due dates of media not yet deleted.
        #: Indexed so a purge that finds nothing costs nothing.
        Index(
            "ix_video_recordings_purge_due",
            "media_purge_due_at",
            postgresql_where=text("media_deleted_at IS NULL"),
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    conversation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("assessment_conversations.id", ondelete="CASCADE"), nullable=False)
    candidate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False)
    job_candidate_link_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("job_candidate_links.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    #: One of `RECORDING_KINDS`, pinned by a database CHECK so a third kind is
    #: a migration rather than a string somebody typed at a call site.
    kind: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=DEFAULT_RECORDING_KIND,
        server_default=RECORDING_PROCTORED_SESSION,
    )
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
    #: When the COMPRESSED object was verified present in object storage. The
    #: retention clock runs from here and not from `created_at`, because a
    #: recording that failed before `storing` has no stored media to age out
    #: and a row is not the thing retention is about.
    stored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: When the compressed object was verifiably deleted (HEAD-confirmed) by
    #: erasure, job closure or the retention sweep. Set means the row survives
    #: as provenance and the media is gone; the row is never quietly rewritten
    #: to look like a recording that never happened.
    media_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: D4's session half, STORED at finalize (see the class docstring). NULL
    #: only while the session is still being recorded.
    media_purge_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Counted, never swallowed. The object store fails independently of the
    #: database, so a deletion that did not confirm is swept again rather than
    #: reported as done, exactly as project-intake originals are.
    media_delete_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )


class VideoRecordingSegment(Base, UUIDPKMixin, CreatedAtMixin):
    """One S3 multipart upload of the session recording (migration 0126).

    A segment is ONE continuous MediaRecorder run: the browser starts a new
    one after every camera or microphone recovery, because a recorder that
    lost its device does not resume the same stream. Its parts are uploaded
    through the API, at most `video_part_max_bytes` each, straight into the
    multipart upload; the API never holds more than one part.

    `parts_json` is `{"<part number>": {"etag": ..., "bytes": ...}}`, exactly
    what `CompleteMultipartUpload` needs, so a segment abandoned by a closed
    tab can be completed by the sweep without the browser.

    THE ROW IS THE ONLY NAME FOR THE OBJECT. It cascades with the recording,
    so every enumerator (`assessment_media_retention`) reads it before any
    recording row is deleted.
    """

    __tablename__ = "video_recording_segments"
    __table_args__ = (
        UniqueConstraint(
            "recording_id", "ordinal", name="uq_video_recording_segment_ordinal"
        ),
        CheckConstraint(
            "status IN ('open', 'completed', 'aborted')",
            name="ck_video_recording_segments_status",
        ),
        CheckConstraint("ordinal >= 0", name="ck_video_recording_segments_ordinal"),
        CheckConstraint("bytes >= 0", name="ck_video_recording_segments_bytes"),
        Index("ix_video_recording_segments_recording", "recording_id", "ordinal"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    recording_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("video_recordings.id", ondelete="CASCADE"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    s3_key: Mapped[str] = mapped_column(String(500), nullable=False)
    source_format: Mapped[str | None] = mapped_column(String(60))
    multipart_upload_id: Mapped[str] = mapped_column(String(1024), nullable=False)
    parts_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    #: The part the browser declared last; only it may be under S3's minimum.
    final_part_number: Mapped[int | None] = mapped_column(Integer)
    bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=SEGMENT_OPEN, server_default=SEGMENT_OPEN)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    last_part_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: HEAD-confirmed deletion of this segment's object.
    raw_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
