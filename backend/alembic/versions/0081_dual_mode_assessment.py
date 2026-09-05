"""Dual-mode assessment: conversation mode, per-session consent, video assets.

Implements the 2026-09-05 Dual-Mode Assessment Specification (sections 3, 5,
16 and the video/dashboard spec's storage model):

  `assessment_conversations.mode`  which input mechanism this session uses.
      'conversational' is the server default so every row written before this
      migration keeps its truthful mode: those sessions WERE conversational.

  `assessment_consents`  the consent audit record (spec 3.4). One row per
      (conversation, mode): consent is per assessment SESSION, and a candidate
      who switches mode before starting consents again to the other mode's
      collection terms. Versions are recorded because the wording is
      configurable and a dispute is settled by which wording was accepted.

  `video_recordings`  the recording lifecycle record (spec 5, 16 and the
      video spec's sections 7-10). The S3 keys carry IDs ONLY, never a
      candidate name or email (spec section 5 / video spec section 9):

        assessment-raw/{conversation_id}/{recording_id}/...
        assessment-compressed/{conversation_id}/{recording_id}/assessment.mp4

      `raw_deleted` + `raw_delete_failures` follow the project-intake
      deletion contract: a raw object is recorded deleted only after a HEAD
      confirms it is gone, and a failed deletion is counted for retry rather
      than blocking the recording from reaching `ready`.

PROCTORING IS UNTOUCHED. An assessment video is a separately CONSENTED
artifact of the video-interview mode; nothing here is readable or writable by
anything under `services/proctoring/`, which still stores no media.

RLS: both new tables are tenant-scoped with the standard tenant-isolation
policy (0076 pattern). The candidate portal writes them through the bypass
scope (`get_candidate_db`), exactly as it writes `proctoring_sessions`.

Revision ID: 0081_dual_mode_assessment
Revises: 0080_client_email_senders
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0081_dual_mode_assessment"
down_revision = "0080_client_email_senders"
branch_labels = None
depends_on = None

MODES = ("conversational", "video_interview")

#: The recording lifecycle (spec 16, video spec 8). Working states in order,
#: then the named failure states -- each failure names the step that failed so
#: an operator retries the right thing rather than "processing".
RECORDING_STATUSES = (
    "recording",
    "uploading",
    "uploaded",
    "processing",
    "compressing",
    "storing",
    "ready",
    "upload_failed",
    "processing_failed",
    "transcription_failed",
    "compression_failed",
    "storage_failed",
)


def _in_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _tenant_rls(table: str) -> None:
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO pickready_app")
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {table}_tenant_isolation ON {table}
        USING (
            tenant_id = current_setting('app.tenant_id', true)::uuid
            OR current_setting('app.bypass_rls', true) = 'on'
        )
        WITH CHECK (
            tenant_id = current_setting('app.tenant_id', true)::uuid
            OR current_setting('app.bypass_rls', true) = 'on'
        )
        """
    )


def upgrade() -> None:
    # ── The conversation's input mechanism ──────────────────────────────────
    op.add_column(
        "assessment_conversations",
        sa.Column(
            "mode",
            sa.String(20),
            nullable=False,
            server_default="conversational",
        ),
    )
    op.create_check_constraint(
        "ck_assessment_conversations_mode",
        "assessment_conversations",
        f"mode IN ({_in_list(MODES)})",
    )

    # ── Consent audit (spec 3.4) ────────────────────────────────────────────
    op.create_table(
        "assessment_consents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "candidate_id",
            UUID(as_uuid=True),
            sa.ForeignKey("candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("assessment_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_candidate_link_id",
            UUID(as_uuid=True),
            sa.ForeignKey("job_candidate_links.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("assessment_mode", sa.String(20), nullable=False),
        # Only a granted consent is ever stored: a declined screen collects
        # nothing, so there is nothing to audit. The CHECK keeps a future
        # writer from inventing a 'declined' row that would imply collection
        # happened without agreement.
        sa.Column("consent_status", sa.String(20), nullable=False),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consent_version", sa.String(40), nullable=False),
        sa.Column("privacy_policy_version", sa.String(40), nullable=False),
        sa.Column("terms_version", sa.String(40), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"assessment_mode IN ({_in_list(MODES)})",
            name="ck_assessment_consents_mode",
        ),
        sa.CheckConstraint(
            "consent_status = 'granted'",
            name="ck_assessment_consents_granted_only",
        ),
        sa.UniqueConstraint(
            "conversation_id",
            "assessment_mode",
            name="uq_assessment_consent_session_mode",
        ),
    )
    op.create_index(
        "ix_assessment_consents_conversation",
        "assessment_consents",
        ["conversation_id"],
    )
    _tenant_rls("assessment_consents")

    # ── Video recordings (spec 5, 16; video spec 7-10) ──────────────────────
    op.create_table(
        "video_recordings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("assessment_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "candidate_id",
            UUID(as_uuid=True),
            sa.ForeignKey("candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_candidate_link_id",
            UUID(as_uuid=True),
            sa.ForeignKey("job_candidate_links.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("source_format", sa.String(60)),
        sa.Column("stored_format", sa.String(20)),
        sa.Column("duration_seconds", sa.Float()),
        sa.Column("file_size_bytes", sa.BigInteger()),
        sa.Column("compressed_size_bytes", sa.BigInteger()),
        sa.Column("s3_raw_key", sa.String(500)),
        sa.Column("s3_compressed_key", sa.String(500)),
        sa.Column(
            "raw_deleted", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column(
            "raw_delete_failures",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        # Server-stamped question display marks: [{question_id, question_type,
        # offset_seconds}]. What the transcript is segmented by; a
        # client-reported timeline would be a timeline the client chose.
        sa.Column(
            "question_marks_json",
            JSONB(),
            nullable=False,
            server_default="[]",
        ),
        # An honest, operator-facing description of the failure the status
        # names. Never candidate content, never a transcript excerpt.
        sa.Column("error_detail", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"status IN ({_in_list(RECORDING_STATUSES)})",
            name="ck_video_recordings_status",
        ),
    )
    op.create_index(
        "ix_video_recordings_conversation",
        "video_recordings",
        ["conversation_id", "created_at"],
    )
    op.create_index(
        "ix_video_recordings_link",
        "video_recordings",
        ["job_candidate_link_id"],
    )
    _tenant_rls("video_recordings")


def downgrade() -> None:
    for table in ("video_recordings", "assessment_consents"):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
    op.drop_index("ix_video_recordings_link", table_name="video_recordings")
    op.drop_index(
        "ix_video_recordings_conversation", table_name="video_recordings"
    )
    op.drop_table("video_recordings")
    op.drop_index(
        "ix_assessment_consents_conversation", table_name="assessment_consents"
    )
    op.drop_table("assessment_consents")
    op.drop_constraint(
        "ck_assessment_conversations_mode",
        "assessment_conversations",
        type_="check",
    )
    op.drop_column("assessment_conversations", "mode")
