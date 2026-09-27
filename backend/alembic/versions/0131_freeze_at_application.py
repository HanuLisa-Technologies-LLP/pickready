"""The skills contract freezes at the first genuine application (CONTRACT v10).

Revision ID: 0131_freeze_at_application
Revises: 0130_report_immutability

Owner ruling v10 (2026-09-28) moves the lock point of D5. The job's JD and
skills now FREEZE when the first candidate genuinely applies (the one apply
path, portal or public `/apply`, including a sourced link the candidate
converts by applying), in the SAME transaction as the application write. The
assessment start keeps its lock as an idempotent backstop that binds the
conversation to the snapshot that already exists.

1. `job_skill_snapshots.locked_by_link_id`: the application whose write took
   the freeze. Nullable (a start-time or migrated snapshot has none), a
   foreign key to `job_candidate_links` ON DELETE SET NULL, so erasing a
   candidate's application leaves the snapshot, which is the contract every
   other candidate on the job is assessed against, exactly where it was.
2. `source` gains `'application'` (the column is widened from ten characters
   to sixteen, because the word is eleven). The CHECK is replaced, never
   dropped and left open.
3. THE IMMUTABILITY TRIGGER STAYS, with ONE narrow addition. The referential
   action of (1) is an UPDATE of the snapshot row, and the trigger refuses
   every UPDATE, so without the addition deleting an application would raise
   and a candidate's erasure would fail. The trigger now admits an UPDATE
   only when ALL of these hold: it arrives nested inside another trigger (the
   referential action runs as one, the same test the DELETE branch already
   makes), it sets `locked_by_link_id` from a value to NULL, and every other
   column of the row is unchanged (compared as the whole row, so a column
   added later is covered without editing this function). A direct UPDATE,
   including one that only clears `locked_by_link_id`, is refused exactly as
   before, and UPDATE stays revoked from `pickready_app`.

No table is created, so no RLS policy is added; the existing tenant policy
covers the new column.

DOWNGRADE restores 0118's function, the ten-character column and the old
CHECK. It REFUSES while any snapshot row carries `source = 'application'`
(the old CHECK cannot hold it and the row cannot be rewritten), and it first
proves the connection sees every row, because a count filtered by row level
security would read zero over a table full of them (the 0128 lesson).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0131_freeze_at_application"
down_revision = "0130_report_immutability"
branch_labels = None
depends_on = None

BYPASS = "current_setting('app.bypass_rls', true) = 'on'"

#: Restated as of this revision; `models.job_skill_snapshot.SNAPSHOT_SOURCES`
#: owns the live vocabulary and `tests/test_freeze_at_application.py` pins the
#: two together.
SOURCES_BEFORE: tuple[str, ...] = ("lock", "migration")
SOURCES_AFTER: tuple[str, ...] = ("lock", "migration", "application")

FK_NAME = "fk_job_skill_snapshots_locked_by_link"
INDEX_NAME = "ix_job_skill_snapshots_locked_by_link"
CHECK_NAME = "ck_job_skill_snapshots_source"

#: 0118's function body, restored verbatim by the downgrade.
_IMMUTABILITY_0118 = """
CREATE OR REPLACE FUNCTION job_skill_snapshot_is_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION
            'job_skill_snapshots is insert-only: snapshot % of job % cannot be rewritten',
            OLD.version, OLD.job_id
            USING ERRCODE = 'restrict_violation';
    END IF;
    -- A DELETE is legal only as the cascade of the job or tenant it belongs
    -- to. The referential action runs as a trigger, so it arrives here one
    -- level deeper than a DELETE statement issued directly.
    IF pg_trigger_depth() <= 1 THEN
        RAISE EXCEPTION
            'job_skill_snapshots is insert-only: snapshot % of job % cannot be deleted',
            OLD.version, OLD.job_id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN OLD;
END;
$$
"""

#: 0118's function with the one referential exception described above.
_IMMUTABILITY_0131 = """
CREATE OR REPLACE FUNCTION job_skill_snapshot_is_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        -- The ONE update a snapshot admits: `locked_by_link_id` set to NULL
        -- by the ON DELETE SET NULL action of the application it names. That
        -- action runs as a trigger, so it arrives nested; a statement issued
        -- directly does not, and is refused below whatever it changes.
        IF pg_trigger_depth() > 1
           AND OLD.locked_by_link_id IS NOT NULL
           AND NEW.locked_by_link_id IS NULL
           AND (to_jsonb(NEW) - 'locked_by_link_id') = (to_jsonb(OLD) - 'locked_by_link_id')
        THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION
            'job_skill_snapshots is insert-only: snapshot % of job % cannot be rewritten',
            OLD.version, OLD.job_id
            USING ERRCODE = 'restrict_violation';
    END IF;
    -- A DELETE is legal only as the cascade of the job or tenant it belongs
    -- to. The referential action runs as a trigger, so it arrives here one
    -- level deeper than a DELETE statement issued directly.
    IF pg_trigger_depth() <= 1 THEN
        RAISE EXCEPTION
            'job_skill_snapshots is insert-only: snapshot % of job % cannot be deleted',
            OLD.version, OLD.job_id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN OLD;
END;
$$
"""


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def upgrade() -> None:
    op.alter_column(
        "job_skill_snapshots",
        "source",
        type_=sa.String(16),
        existing_type=sa.String(10),
        existing_nullable=False,
    )
    op.drop_constraint(CHECK_NAME, "job_skill_snapshots", type_="check")
    op.create_check_constraint(CHECK_NAME, "job_skill_snapshots", _in("source", SOURCES_AFTER))

    op.add_column(
        "job_skill_snapshots",
        sa.Column("locked_by_link_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        FK_NAME,
        "job_skill_snapshots",
        "job_candidate_links",
        ["locked_by_link_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # The referential action looks the snapshot up by this column on every
    # delete of an application, so it is indexed.
    op.create_index(INDEX_NAME, "job_skill_snapshots", ["locked_by_link_id"])
    op.execute(_IMMUTABILITY_0131)


def downgrade() -> None:
    message_rls = (
        "0131 downgrade refuses to run: this connection reads row level "
        "security filtered rows, so its count of application snapshots could "
        "read zero over a table that holds them. Set app.bypass_rls as "
        "alembic/env.py does."
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT ({BYPASS})
               AND NOT (SELECT rolsuper OR rolbypassrls
                          FROM pg_roles WHERE rolname = current_user) THEN
                RAISE EXCEPTION '{message_rls}';
            END IF;
        END
        $$;
        """
    )
    op.execute(
        """
        DO $$
        DECLARE
            affected bigint;
        BEGIN
            SELECT count(*) INTO affected
              FROM job_skill_snapshots WHERE source = 'application';
            IF affected > 0 THEN
                RAISE EXCEPTION
                    '0131 downgrade refused: % job_skill_snapshots row(s) were frozen at an application, and the restored CHECK cannot hold them while the rows cannot be rewritten',
                    affected;
            END IF;
        END
        $$;
        """
    )
    op.execute(_IMMUTABILITY_0118)
    op.drop_index(INDEX_NAME, table_name="job_skill_snapshots")
    op.drop_constraint(FK_NAME, "job_skill_snapshots", type_="foreignkey")
    op.drop_column("job_skill_snapshots", "locked_by_link_id")
    op.drop_constraint(CHECK_NAME, "job_skill_snapshots", type_="check")
    op.create_check_constraint(CHECK_NAME, "job_skill_snapshots", _in("source", SOURCES_BEFORE))
    op.alter_column(
        "job_skill_snapshots",
        "source",
        type_=sa.String(10),
        existing_type=sa.String(16),
        existing_nullable=False,
    )
