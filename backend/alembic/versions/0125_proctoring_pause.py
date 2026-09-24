"""Proctoring pauses: the pause record, and a fourth consequence path.

Revision ID: 0125_proctoring_pause
Revises: 0121_candidate_comms

PLAN-p3 WP4 (proctoring). The orchestrator re-chains the stage 2 migrations
0122 to 0126 at integration; this file is written against 0121, the head it
was built on.

1. `assessment_pauses`, one row per stretch of time an assessment's clock was
   stopped: a camera or microphone loss (`device_loss`), a spoken answer being
   transcribed (`transcription`), or a blocking warning on the screen
   (`warning`). The turn timer subtracts the union of these; nothing else
   records a pause. `expires_at` caps what a row can count for, so a pause
   nobody closes stops the clock for a bounded time. At most one OPEN row per
   reason per conversation (a partial UNIQUE index), which is what makes
   `services/assessment_conversation/pauses.open_pause` idempotent under a
   retried request.

   THE DEVICE PAUSE STATE IS THESE ROWS, NOT A FLAG ON THE SESSION. PLAN-p3
   3.10 proposed `proctoring_sessions.device_pauses_used` and
   `device_paused_at`. They are deliberately NOT added: a paused flag and an
   open pause row are two records of one fact, and the number of pauses used
   is the count of `device_loss` rows. Rule 5 over the plan's sketch; the
   concurrency the counter would have needed is provided instead by a row
   lock on the proctoring session (`device_pause._lock`).

2. `ck_proctoring_events_path` admits `P`, the pause path: a camera or
   microphone loss is no longer an immediate termination (Path A) but a pause
   with a grace period (master prompt, Phase 3, Proctoring). The constraint is
   recreated `NOT VALID` and then validated, so the rewrite takes no long lock
   on a table every live assessment writes to.

RLS: tenant equality OR the bypass GUC, exactly the policy every proctoring
table carries (0076), because the candidate routes that write a pause run in
the bypass scope and the recruiter reads run tenant scoped. Grants SELECT,
INSERT, UPDATE, DELETE: a pause is closed by UPDATE, and erasure deletes.

The downgrade drops the table and restores the three-path CHECK. It REFUSES,
naming the count, if any event already sits on the pause path: rewriting a
stored event's path would change what the report says happened, and deleting
it would remove a record of what happened. Neither is a migration's call.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0125_proctoring_pause"
down_revision = "0121_candidate_comms"
branch_labels = None
depends_on = None

TENANT = "nullif(current_setting('app.tenant_id', true), '')::uuid"
BYPASS = "current_setting('app.bypass_rls', true) = 'on'"

#: Mirrors `models.assessment_pause.PAUSE_REASONS`, written as literals because
#: a migration applies to the schema as it stood, not to what the module says
#: later.
_REASONS = ("device_loss", "transcription", "warning")
_PATHS_BEFORE = ("A", "B", "C")
_PATHS_AFTER = ("A", "B", "C", "P")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    rendered = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({rendered})"


def _recreate_path_check(values: tuple[str, ...]) -> None:
    """Drop and recreate the path CHECK without a validating table scan under
    an ACCESS EXCLUSIVE lock: NOT VALID first, then VALIDATE, which takes only
    a SHARE UPDATE EXCLUSIVE lock while it reads the rows."""
    op.execute("ALTER TABLE proctoring_events DROP CONSTRAINT ck_proctoring_events_path")
    op.execute(
        "ALTER TABLE proctoring_events ADD CONSTRAINT ck_proctoring_events_path "
        f"CHECK ({_in_list('path', values)}) NOT VALID"
    )
    op.execute("ALTER TABLE proctoring_events VALIDATE CONSTRAINT ck_proctoring_events_path")


def upgrade() -> None:
    # ── 1. The pause record ─────────────────────────────────────────────────
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
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ref_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(_in_list("reason", _REASONS), name="ck_assessment_pauses_reason"),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at",
            name="ck_assessment_pauses_ended_after_start",
        ),
        sa.CheckConstraint(
            "expires_at IS NULL OR expires_at >= started_at",
            name="ck_assessment_pauses_expires_after_start",
        ),
    )
    op.create_index(
        "ix_assessment_pauses_conversation",
        "assessment_pauses",
        ["conversation_id", "started_at"],
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_assessment_pauses_open ON assessment_pauses "
        "(conversation_id, reason) WHERE ended_at IS NULL"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON assessment_pauses TO pickready_app")
    op.execute("ALTER TABLE assessment_pauses ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE assessment_pauses FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY assessment_pauses_tenant_isolation ON assessment_pauses "
        f"USING ((tenant_id = {TENANT}) OR ({BYPASS})) "
        f"WITH CHECK ((tenant_id = {TENANT}) OR ({BYPASS}))"
    )

    # ── 2. The pause path on proctoring events ───────────────────────────────
    _recreate_path_check(_PATHS_AFTER)


def downgrade() -> None:
    op.execute(
        """
        DO $$
        DECLARE
            affected bigint;
        BEGIN
            SELECT count(*) INTO affected FROM proctoring_events WHERE path = 'P';
            IF affected > 0 THEN
                RAISE EXCEPTION 'proctoring_events holds % row(s) on the pause path. '
                    'Restoring the three-path CHECK would require rewriting or '
                    'deleting a record of what happened in an assessment, which is '
                    'not a migration''s decision.', affected;
            END IF;
        END
        $$;
        """
    )
    _recreate_path_check(_PATHS_BEFORE)

    op.execute(
        "DROP POLICY IF EXISTS assessment_pauses_tenant_isolation ON assessment_pauses"
    )
    op.execute("DROP INDEX IF EXISTS uq_assessment_pauses_open")
    op.drop_index("ix_assessment_pauses_conversation", table_name="assessment_pauses")
    op.drop_table("assessment_pauses")
