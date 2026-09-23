"""Dual-mode assessment records (2026-09-05 Dual-Mode Assessment Specification).

Two tables, both per assessment SESSION:

  `assessment_consents`   the consent audit record (spec 3.4). Consent is a
                          hard prerequisite for starting an assessment in
                          EITHER mode; the gate in `services/assessment_consent`
                          reads this table, never a stamp elsewhere.
  `video_recordings`      the assessment recording lifecycle (spec 5, 16
                          and the video spec's sections 7-10). The transitions
                          live in `services/video/lifecycle.py`.

ASSESSMENT MEDIA IS STORED, AND `video_recordings` IS THE ONE TABLE THAT HOLDS
IT (owner ruling, 2026-09-22). The row's `kind` says which artifact it is:

  `video_interview`   the recording IS the answer. It is transcribed,
                      segmented by the server-stamped question marks and
                      written into the shared transcript records.
  `proctored_session` the recording is the monitoring record of a proctored
                      assessment taken in any mode. It is compressed, stored
                      and served, and it is NEVER transcribed, never scored
                      and never read by anything that grades.

This supersedes the note that stood here until 2026-09-22, which read
"PROCTORING STORES NO MEDIA AND THAT IS UNCHANGED". What IS unchanged is the
half that matters for P3: nothing under `services/proctoring/` reads or writes
these rows, and no scorer imports `services/video`
(`tests/test_dual_mode_assessment.py` and
`tests/test_proctoring_scoring_isolation.py` pin both directions). Proctoring
DECIDES nothing about the media, and the media pipeline reads no proctoring
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
MODE_VIDEO_INTERVIEW = "video_interview"
ASSESSMENT_MODES = (MODE_CONVERSATIONAL, MODE_VIDEO_INTERVIEW)

# ── Recording kinds (owner ruling, 2026-09-22) ───────────────────────────────
#: The recording IS the candidate's answer: transcribed, segmented and written
#: into the shared transcript records the scorers already read.
RECORDING_VIDEO_INTERVIEW = "video_interview"
#: The recording is the monitoring record of a proctored assessment. Stored
#: and served exactly like the interview recording; transcribed, segmented and
#: scored by nothing, which is what keeps principle P3 structural.
RECORDING_PROCTORED_SESSION = "proctored_session"
RECORDING_KINDS = (RECORDING_VIDEO_INTERVIEW, RECORDING_PROCTORED_SESSION)
#: Rows written before migration 0110 are all interview recordings, so the
#: default is the truthful one rather than a placeholder.
DEFAULT_RECORDING_KIND = RECORDING_VIDEO_INTERVIEW


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
    """One assessment recording and its processing lifecycle.

    KIND says which artifact this is, and it is the only thing that differs
    between the two (owner ruling, 2026-09-22). `video_interview` runs the
    transcription half; `proctored_session` skips straight from `uploaded` to
    `compressing`, because a monitoring record has no answers to segment and
    running it through a scorer's input path is exactly what principle P3
    forbids. Storage, verification, raw deletion, delivery, consent and
    retention are IDENTICAL for both, which is the point: there is one media
    pipeline in this product, not two.

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
        CheckConstraint(
            "kind IN ('video_interview', 'proctored_session')",
            name="ck_video_recordings_kind",
        ),
        #: The retention sweep's whole query: stored media, not yet deleted,
        #: older than the retention setting. Indexed so a purge that finds
        #: nothing costs nothing, which is the state it is in by default.
        Index(
            "ix_video_recordings_retention",
            "stored_at",
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
        server_default=DEFAULT_RECORDING_KIND,
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
    #: Counted, never swallowed. The object store fails independently of the
    #: database, so a deletion that did not confirm is swept again rather than
    #: reported as done, exactly as project-intake originals are.
    media_delete_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
