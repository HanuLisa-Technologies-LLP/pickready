"""The assessment conversation engine: server turns, drafts, pauses, voice answers.

Revision ID: 0123_assessment_conversation
Revises: 0121_candidate_comms

PLAN-p3 WP3 (the orchestrator re-chains 0122 to 0126 at integration). Every
step is additive except one guarded data UPDATE.

1. `assessment_conversations` gains the TURN the server times:
   `turn_seq` (0 until the first turn is opened, then one per prompt the
   candidate is shown, so a stale or replayed answer names a turn that is no
   longer current and is refused), `turn_allocation_seconds` (the allocation
   SNAPSHOTTED when the turn opened, so a settings change never moves the
   deadline of a question somebody is answering), and the draft
   (`draft_answer_json`, `draft_turn_seq`, `draft_saved_at`) the expiry path
   submits when time runs out. The client's `paused_ms` is gone: every second
   of pause is a row below, written by the server.
2. `assessment_conversations` gains what the questions were written AGAINST:
   `questions_contract_digest` and `questions_contract_version`, stamped by the
   generator, compared by the start against the contract it locks (a
   mismatch, NULL included, regenerates before the first answer), plus
   `composition_json` (the planned and served mix and every recorded
   degradation) and `questions_requested_at`, which bounds how often a start
   may re-dispatch generation: generation runs on its own Fargate task, and a
   start polled every few seconds must not start one each time.
3. `assessment_pauses`: every interval the turn clock stops for, by reason
   (`device_loss`, `transcription`, `warning`). An open pause has no
   `ended_at`; an `ended_at` in the future is a pause that is already
   scheduled to end (a failed transcription stays paused for a short window
   so the candidate can read what happened). Tenant RLS with the bypass
   clause: the candidate's session runs in the bypass scope, the recruiter's
   under their tenant.
4. `voice_answers`: one spoken answer, from capture to transcript. The AUDIO
   is transient (deleted, HEAD-confirmed, once transcribed); the transcript is
   final and is the answer. `failure_reason` is operator wording and never
   candidate content. One capture in flight per turn, by partial UNIQUE.
5. DATA: an UNSTARTED video-interview conversation becomes conversational.
   The video interview mode is retired; a session that never began has
   written nothing in that mode, so relabelling it claims nothing false. A
   STARTED one is left exactly as it is (the release gate counts them, and
   pilot had none on 2026-09-24). The count is logged either way.

No capability, so no seeding. The downgrade drops the two tables and the
columns; it does not put a converted row back into the retired mode, because
nothing records which rows were converted and guessing would relabel a
session the candidate took conversationally.
"""
from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0123_assessment_conversation"
down_revision = "0121_candidate_comms"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration.0123_assessment_conversation")

TENANT = "nullif(current_setting('app.tenant_id', true), '')::uuid"
BYPASS = "current_setting('app.bypass_rls', true) = 'on'"

PAUSE_REASONS: tuple[str, ...] = ("device_loss", "transcription", "warning")
VOICE_STATUSES: tuple[str, ...] = (
    "recording",
    "uploaded",
    "transcribing",
    "transcribed",
    "failed",
    "consumed",
)
#: A capture is IN FLIGHT from begin until a transcript or a failure exists.
VOICE_IN_FLIGHT: tuple[str, ...] = ("recording", "uploaded", "transcribing")


def _in_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _tenant_rls(table: str) -> None:
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO pickready_app")
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_tenant_isolation ON {table} "
        f"USING ((tenant_id = {TENANT}) OR ({BYPASS})) "
        f"WITH CHECK ((tenant_id = {TENANT}) OR ({BYPASS}))"
    )


def upgrade() -> None:
    # ── 1 and 2. The turn, the draft, and the contract the questions carry ──
    op.add_column(
        "assessment_conversations",
        sa.Column("turn_seq", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "assessment_conversations",
        sa.Column("turn_allocation_seconds", sa.Integer()),
    )
    op.add_column("assessment_conversations", sa.Column("draft_answer_json", JSONB()))
    op.add_column("assessment_conversations", sa.Column("draft_turn_seq", sa.Integer()))
    op.add_column(
        "assessment_conversations",
        sa.Column("draft_saved_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "assessment_conversations",
        sa.Column("questions_contract_digest", sa.String(64)),
    )
    op.add_column(
        "assessment_conversations",
        sa.Column("questions_contract_version", sa.Integer()),
    )
    op.add_column("assessment_conversations", sa.Column("composition_json", JSONB()))
    op.add_column(
        "assessment_conversations",
        sa.Column("questions_requested_at", sa.DateTime(timezone=True)),
    )
    op.create_check_constraint(
        "ck_assessment_conversations_turn_seq",
        "assessment_conversations",
        "turn_seq >= 0",
    )
    op.create_check_constraint(
        "ck_assessment_conversations_turn_allocation",
        "assessment_conversations",
        "turn_allocation_seconds IS NULL OR turn_allocation_seconds > 0",
    )

    # ── 3. Pauses ────────────────────────────────────────────────────────────
    op.create_table(
        "assessment_pauses",
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
        sa.Column("reason", sa.String(20), nullable=False),
        # What the pause is ABOUT, where there is a row to name: the voice
        # answer being transcribed, the proctoring event that lost a device.
        sa.Column("ref_id", UUID(as_uuid=True)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"reason IN ({_in_list(PAUSE_REASONS)})",
            name="ck_assessment_pauses_reason",
        ),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at",
            name="ck_assessment_pauses_interval",
        ),
    )
    op.create_index(
        "ix_assessment_pauses_conversation",
        "assessment_pauses",
        ["conversation_id", "started_at"],
    )
    _tenant_rls("assessment_pauses")

    # ── 4. Voice answers ─────────────────────────────────────────────────────
    op.create_table(
        "voice_answers",
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
            "question_id",
            UUID(as_uuid=True),
            sa.ForeignKey("candidate_questions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("turn_seq", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("s3_key", sa.String(500)),
        sa.Column("content_type", sa.String(60)),
        sa.Column("size_bytes", sa.Integer()),
        sa.Column("audio_deleted_at", sa.DateTime(timezone=True)),
        sa.Column(
            "audio_delete_failures", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("transcribe_job_name", sa.String(200)),
        sa.Column("transcript_text", sa.Text()),
        sa.Column("failure_reason", sa.Text()),
        sa.Column("failed_at", sa.DateTime(timezone=True)),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("capture_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True)),
        sa.Column("transcribed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "consumed_message_id",
            UUID(as_uuid=True),
            sa.ForeignKey("assessment_messages.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"status IN ({_in_list(VOICE_STATUSES)})",
            name="ck_voice_answers_status",
        ),
        sa.CheckConstraint(
            "audio_delete_failures >= 0", name="ck_voice_answers_delete_failures"
        ),
    )
    op.create_index(
        "ix_voice_answers_conversation",
        "voice_answers",
        ["conversation_id", "created_at"],
    )
    # The retry sweep's read: audio that still exists.
    op.create_index(
        "ix_voice_answers_audio_pending",
        "voice_answers",
        ["created_at"],
        postgresql_where=sa.text("audio_deleted_at IS NULL AND s3_key IS NOT NULL"),
    )
    # ONE capture in flight per turn. A second begin returns the first; the
    # database refuses the race two tabs would otherwise win together.
    op.create_index(
        "ux_voice_answers_turn_in_flight",
        "voice_answers",
        ["conversation_id", "turn_seq"],
        unique=True,
        postgresql_where=sa.text(f"status IN ({_in_list(VOICE_IN_FLIGHT)})"),
    )
    _tenant_rls("voice_answers")

    # ── 5. The retired mode, for sessions that never began ──────────────────
    bind = op.get_bind()
    converted = bind.execute(
        sa.text(
            "UPDATE assessment_conversations SET mode = 'conversational' "
            "WHERE mode = 'video_interview' AND started_at IS NULL"
        )
    ).rowcount
    started = bind.execute(
        sa.text(
            "SELECT count(*) FROM assessment_conversations "
            "WHERE mode = 'video_interview' AND started_at IS NOT NULL"
        )
    ).scalar_one()
    logger.info(
        "0123 converted %d unstarted video interview conversation(s) to "
        "conversational; %d started video interview conversation(s) were left "
        "as they are",
        converted, started,
    )


def downgrade() -> None:
    op.drop_index("ux_voice_answers_turn_in_flight", table_name="voice_answers")
    op.drop_index("ix_voice_answers_audio_pending", table_name="voice_answers")
    op.drop_index("ix_voice_answers_conversation", table_name="voice_answers")
    op.execute("DROP POLICY IF EXISTS voice_answers_tenant_isolation ON voice_answers")
    op.drop_table("voice_answers")
    op.drop_index("ix_assessment_pauses_conversation", table_name="assessment_pauses")
    op.execute(
        "DROP POLICY IF EXISTS assessment_pauses_tenant_isolation ON assessment_pauses"
    )
    op.drop_table("assessment_pauses")
    op.drop_constraint(
        "ck_assessment_conversations_turn_allocation",
        "assessment_conversations",
        type_="check",
    )
    op.drop_constraint(
        "ck_assessment_conversations_turn_seq",
        "assessment_conversations",
        type_="check",
    )
    for column in (
        "questions_requested_at",
        "composition_json",
        "questions_contract_version",
        "questions_contract_digest",
        "draft_saved_at",
        "draft_turn_seq",
        "draft_answer_json",
        "turn_allocation_seconds",
        "turn_seq",
    ):
        op.drop_column("assessment_conversations", column)
