"""Assessment media is stored: the recording kind and its retention columns.

Revision ID: 0110_assessment_media_storage
Revises: 0109_assessment_cost_records

THE OWNER RULING THIS IMPLEMENTS (2026-09-22)
----------------------------------------------
"Proctoring remains mandatory, Media storage is required. The assessment video
must be compressed and stored securely in S3, linked to the candidate
assessment." Principle P1 of the proctoring specification, which read "No
video is ever stored", is REVERSED by that sentence. Principle P4, proctoring
is mandatory with no enable flag, is RE-AFFIRMED by the same sentence and
nothing here softens it: there is no `proctoring_enabled` column in this
migration and there is not going to be one.

WHY THERE IS NO NEW TABLE
---------------------------
Because there is already a table for "assessment media, linked to the
candidate assessment, compressed and stored in S3", and it is
`video_recordings`. A second table would be a second media pipeline with its
own keys, its own verification, its own delivery routes and its own deletion
story, and four of those five would be copies. Rule 5 of claude.md exists for
this exact shape of change.

So the recording grows a KIND, and the kind is the only thing that differs:

  `video_interview`    the recording IS the answer. Transcribed, segmented by
                       the server-stamped question marks, written into the
                       shared transcript records.
  `proctored_session`  the monitoring record of a proctored assessment in any
                       mode. Compressed, stored, served, retained, deleted.
                       Never transcribed, never scored, never read by
                       anything that grades. That is principle P3, and it is
                       kept by the ABSENCE of the transcription half rather
                       than by a flag somebody has to remember.

THE DEFAULT IS THE TRUTHFUL ONE. Every row that exists before this migration
is an interview recording, so backfilling them as `video_interview` states a
fact rather than choosing a placeholder.

THE RETENTION COLUMNS, AND WHY THE CLOCK IS NOT `created_at`
--------------------------------------------------------------
`stored_at` is stamped when the COMPRESSED object is verified present. A
recording that failed before `storing` has no stored media, so ageing it out
by row age would report deletions of objects that never existed while leaving
the ones that do. `media_deleted_at` and `media_delete_failures` are the same
contract `raw_deleted` / `raw_delete_failures` already carry one step
earlier, and the same one `candidate_deletion_requests` carries for erasure:
the object store fails independently of the database, "the delete call
returned" is not evidence, and a deletion that did not HEAD-confirm is
counted and swept again rather than recorded as done.

The partial index is the retention sweep's whole query. It is partial on
`media_deleted_at IS NULL` because the rows the sweep must never look at
again are exactly the ones it has already finished with.

NO RLS CHANGE. `video_recordings` already carries its own `tenant_id` and its
own policy from migration 0081; adding columns does not touch either.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0110_assessment_media_storage"
down_revision = "0109_assessment_cost_records"
branch_labels = None
depends_on = None

#: Mirrors `app.models.dual_mode.RECORDING_KINDS`. Stated as a CHECK because a
#: third kind changes what the pipeline does with the bytes, and a kind nobody
#: planned for would take the transcription branch by falling through it.
KINDS = ("video_interview", "proctored_session")


def upgrade() -> None:
    op.add_column(
        "video_recordings",
        sa.Column(
            "kind",
            sa.String(length=30),
            nullable=False,
            server_default="video_interview",
        ),
    )
    op.create_check_constraint(
        "ck_video_recordings_kind",
        "video_recordings",
        "kind IN ('{}')".format("', '".join(KINDS)),
    )
    op.add_column(
        "video_recordings",
        sa.Column("stored_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "video_recordings",
        sa.Column("media_deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "video_recordings",
        sa.Column(
            "media_delete_failures",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    # Existing ready recordings DO have stored media, and the retention sweep
    # has to be able to see it. `processed_at` is when the pipeline finished
    # storing, which is the closest true statement available for a row written
    # before `stored_at` existed; inventing `now()` would restart every
    # historical recording's retention clock at this deploy.
    op.execute(
        "UPDATE video_recordings SET stored_at = processed_at "
        "WHERE status = 'ready' AND processed_at IS NOT NULL"
    )
    op.create_index(
        "ix_video_recordings_retention",
        "video_recordings",
        ["stored_at"],
        postgresql_where=sa.text("media_deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_video_recordings_retention", table_name="video_recordings")
    op.drop_constraint(
        "ck_video_recordings_kind", "video_recordings", type_="check"
    )
    op.drop_column("video_recordings", "media_delete_failures")
    op.drop_column("video_recordings", "media_deleted_at")
    op.drop_column("video_recordings", "stored_at")
    op.drop_column("video_recordings", "kind")
