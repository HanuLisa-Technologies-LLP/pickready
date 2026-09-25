"""The session recording: segmented uploads and a stored purge date (D4).

Revision ID: 0126_recording_retention
Revises: 0121_candidate_comms

Two changes, both about the one recording a proctored assessment keeps.

A RECORDING ARRIVES IN SEGMENTS NOW, SO THE SEGMENTS ARE ROWS
--------------------------------------------------------------
The whole session used to be buffered in the candidate's browser and posted
once, into `await file.read()` of up to a gigabyte in the shared API task. A
session with twenty-minute coding questions passes two hours, so that was an
out-of-memory risk for every other request on the instance and a certain
refusal above the cap. The browser now streams parts of at most
`video_part_max_bytes` into an S3 multipart upload per SEGMENT, and a new
segment starts after every camera or microphone recovery.

`video_recording_segments` is the record of those uploads: which multipart
upload, which parts with which ETags, and whether it completed. It is the
only thing in the database that names a segment's object, so it is also what
every deletion path enumerates BEFORE the rows go (objects before rows, the
rule `services/job_assessment_retention` already states). ON DELETE CASCADE
from the recording is correct for that reason and no other: the enumerators
read these rows first.

THE PURGE DATE IS STORED, NEVER A MOVING CONSTANT
--------------------------------------------------
Owner decision D4: a recording is purged at whichever comes first, 90 days
after the session or the job-closure purge. `media_purge_due_at` is the first
of those, stamped once when the recording is finalized, so editing
`assessment_media_retention_days` later can never move a deadline a
candidate was already given. The closure half is `jobs.assessment_purge_due_at`
(migration 0112), also stored, and the sweep reads the LEAST of the two at
read time, so a job closed after the recording was stamped still wins.

The backfill stamps only rows whose session has ended or whose media is
stored, from the same two columns the finalize path uses, and takes the
closure date into account for a job that is already closed. Pilot held zero
recordings on 2026-09-24 (CONTRACT v3), so this is correctness for the
environments that have not been applied yet rather than a data move.

`processing_started_at` is stamped when the pipeline takes a recording out
of `uploaded`, so the hourly repair can tell a run that is slow from one whose
Fargate task was killed and will never move the row again.

`ix_video_recordings_retention` (on `stored_at`) is replaced by the index the
sweep's query actually reads. The kind's server default becomes
`proctored_session`, the only kind a new row can be now that the video
interview mode is deleted; rows already written keep the kind they have.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0126_recording_retention"
down_revision = "0121_candidate_comms"
branch_labels = None
depends_on = None

#: Mirrors `app.models.dual_mode.SEGMENT_STATUSES`.
SEGMENT_STATUSES = ("open", "completed", "aborted")

#: D4's session half, as it stood when this migration ran. The backfill needs
#: a number and the setting is not importable from a migration; the live
#: finalize path reads `assessment_media_retention_days`.
BACKFILL_RETENTION_DAYS = 90


def _tenant_rls(table: str) -> None:
    """The 0081 posture for an assessment media table: tenant equality, or
    the audited bypass the candidate and worker sessions run under."""
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
    op.add_column(
        "video_recordings",
        sa.Column("media_purge_due_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        f"""
        UPDATE video_recordings r
        SET media_purge_due_at = LEAST(
            COALESCE(r.ended_at, r.stored_at)
                + interval '{BACKFILL_RETENTION_DAYS} days',
            j.assessment_purge_due_at
        )
        FROM job_candidate_links l
        JOIN jobs j ON j.id = l.job_id
        WHERE l.id = r.job_candidate_link_id
          AND r.media_deleted_at IS NULL
          AND r.media_purge_due_at IS NULL
          AND COALESCE(r.ended_at, r.stored_at) IS NOT NULL
        """
    )
    # When processing last took the row out of `uploaded`, so the repair
    # sweep can tell a stalled run (a killed Fargate task) from a slow one.
    op.add_column(
        "video_recordings",
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_index("ix_video_recordings_retention", table_name="video_recordings")
    op.create_index(
        "ix_video_recordings_purge_due",
        "video_recordings",
        ["media_purge_due_at"],
        postgresql_where=sa.text("media_deleted_at IS NULL"),
    )
    op.alter_column(
        "video_recordings", "kind", server_default="proctored_session"
    )

    op.create_table(
        "video_recording_segments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "recording_id",
            UUID(as_uuid=True),
            sa.ForeignKey("video_recordings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("s3_key", sa.String(500), nullable=False),
        sa.Column("source_format", sa.String(60)),
        # S3 upload ids are opaque and long; kept after completion so an
        # operator can match a row to the store's own record.
        sa.Column("multipart_upload_id", sa.String(1024), nullable=False),
        # {"<part number>": {"etag": ..., "bytes": ...}}. What CompleteMultipart
        # is called with, and what a sweep completes an abandoned upload from.
        sa.Column("parts_json", JSONB(), nullable=False, server_default="{}"),
        # The part the browser declared LAST. Only the last part of a
        # multipart upload may be smaller than S3's minimum, so a small part
        # without this mark is refused at upload rather than at completion.
        sa.Column("final_part_number", sa.Integer()),
        sa.Column("bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("last_part_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        # HEAD-confirmed deletion of THIS segment's object. Per segment, so a
        # retry pass re-deletes only what is still there.
        sa.Column("raw_deleted_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "recording_id", "ordinal", name="uq_video_recording_segment_ordinal"
        ),
        sa.CheckConstraint(
            "status IN ('{}')".format("', '".join(SEGMENT_STATUSES)),
            name="ck_video_recording_segments_status",
        ),
        sa.CheckConstraint(
            "ordinal >= 0", name="ck_video_recording_segments_ordinal"
        ),
        sa.CheckConstraint(
            "bytes >= 0", name="ck_video_recording_segments_bytes"
        ),
    )
    op.create_index(
        "ix_video_recording_segments_recording",
        "video_recording_segments",
        ["recording_id", "ordinal"],
    )
    _tenant_rls("video_recording_segments")


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS video_recording_segments_tenant_isolation "
        "ON video_recording_segments"
    )
    op.drop_index(
        "ix_video_recording_segments_recording",
        table_name="video_recording_segments",
    )
    op.drop_table("video_recording_segments")
    op.alter_column("video_recordings", "kind", server_default="video_interview")
    op.drop_index("ix_video_recordings_purge_due", table_name="video_recordings")
    op.create_index(
        "ix_video_recordings_retention",
        "video_recordings",
        ["stored_at"],
        postgresql_where=sa.text("media_deleted_at IS NULL"),
    )
    op.drop_column("video_recordings", "processing_started_at")
    op.drop_column("video_recordings", "media_purge_due_at")
