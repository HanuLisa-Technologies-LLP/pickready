"""Calibration records gain a source, so a Team Review divergence can be one.

Revision ID: 0069_calibration_divergence
Revises: 0065_prescreen_grade

CHAINING NOTE. Written while `0065_prescreen_grade` was the highest revision
on this branch. Phase 8 was allocated slot 0069, so the number is deliberate
and the down_revision points at whatever head existed when it was authored. If
0066 to 0068 land from other work and chain forward from 0065, this revision
must be re-pointed at the new head before `alembic upgrade head` will run
without a merge. That is stated rather than papered over, because a merge
revision written blind reorders somebody else's migration.

WHY THIS EXISTS
---------------
spec-doc6 §8.2: "when Team Review disagrees with the Ready Pick Score, raise a
`CalibrationRecord` (the Runbook object) and surface it in the Super Admin
activity view."

`calibration_records` already exists and already means "did the grade turn out
to be right?" -- Runbook §59's fifth object. A reviewer whose verdict differs
from the machine grade is answering exactly that question, months earlier and
from a different vantage point, so it is the same object and not a new one.
Two tables both holding "somebody thinks this grade was wrong" would be two
places a calibration analysis has to look and two things to keep in step.

WHAT MADE IT NOT FIT, AND WHAT CHANGES
--------------------------------------
`uq_calibration_evaluation` is UNIQUE(evaluation_id): exactly one calibration
row per evaluation. That held while the table had one meaning. It cannot hold
now, because a single evaluation can carry an eventual hiring outcome AND a
divergence from every reviewer who looked at the candidate, and RBAC §29 makes
each of those reviewers' contributions their own.

So the constraint is REPLACED by two PARTIAL unique indexes, one per meaning:

  * one `outcome` row per evaluation, which is the original rule, narrowed to
    the rows it was written for;
  * one `team_review_divergence` row per Team Review, which is what makes
    re-recording a reviewer's changed verdict an upsert rather than a second
    row. Without it, a reviewer who changed their mind twice would appear
    three times in the Standards Board's queue holding one opinion.

A CHECK ties `team_review_id` to `source` in both directions. Without the
second half ("an outcome row has no team review"), a mis-set source would
produce a row that is neither, and a queue that silently under-reports is the
failure mode this whole surface is trying to avoid.

SAFETY UNDER A ROLLING DEPLOY
-----------------------------
`source` is NOT NULL with `server_default='outcome'`, so a writer that predates
this migration inserts a valid outcome row without knowing the column exists.
`team_review_id` is nullable with no backfill. Every existing row is an outcome
row by definition, so the new partial index over `source = 'outcome'` is
exactly the constraint those rows were already satisfying and cannot fail on
existing data.
"""
import sqlalchemy as sa
from alembic import op

revision = "0069_calibration_divergence"
down_revision = "0065_prescreen_grade"
branch_labels = None
depends_on = None

SOURCE_OUTCOME = "outcome"
SOURCE_DIVERGENCE = "team_review_divergence"


def upgrade() -> None:
    op.add_column(
        "calibration_records",
        sa.Column(
            "source",
            sa.String(30),
            nullable=False,
            server_default=SOURCE_OUTCOME,
        ),
    )
    op.add_column(
        "calibration_records",
        sa.Column(
            "team_review_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("candidate_team_reviews.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_calibration_source",
        "calibration_records",
        f"source IN ('{SOURCE_OUTCOME}', '{SOURCE_DIVERGENCE}')",
    )
    op.create_check_constraint(
        "ck_calibration_divergence_has_review",
        "calibration_records",
        f"(source = '{SOURCE_DIVERGENCE}' AND team_review_id IS NOT NULL) "
        f"OR (source = '{SOURCE_OUTCOME}' AND team_review_id IS NULL)",
    )

    op.drop_constraint(
        "uq_calibration_evaluation", "calibration_records", type_="unique"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_calibration_outcome_per_evaluation "
        "ON calibration_records (evaluation_id) "
        f"WHERE source = '{SOURCE_OUTCOME}'"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_calibration_divergence_per_review "
        "ON calibration_records (team_review_id) "
        f"WHERE source = '{SOURCE_DIVERGENCE}'"
    )
    op.create_index(
        "ix_calibration_source",
        "calibration_records",
        ["tenant_id", "source", "created_at"],
    )


def downgrade() -> None:
    # Divergence rows are DELETED on the way down, and deliberately: they
    # cannot satisfy UNIQUE(evaluation_id) alongside an outcome row for the
    # same evaluation, and silently keeping whichever one won a race would
    # leave the table asserting something nobody decided. They are derived
    # from `candidate_team_reviews`, which is untouched, so the signal is
    # recomputable; the outcome rows, which are not derivable from anything,
    # are preserved.
    op.execute(
        f"DELETE FROM calibration_records WHERE source = '{SOURCE_DIVERGENCE}'"
    )
    op.drop_index("ix_calibration_source", table_name="calibration_records")
    op.execute("DROP INDEX IF EXISTS uq_calibration_divergence_per_review")
    op.execute("DROP INDEX IF EXISTS uq_calibration_outcome_per_evaluation")
    op.create_unique_constraint(
        "uq_calibration_evaluation", "calibration_records", ["evaluation_id"]
    )
    op.drop_constraint(
        "ck_calibration_divergence_has_review",
        "calibration_records",
        type_="check",
    )
    op.drop_constraint("ck_calibration_source", "calibration_records", type_="check")
    op.drop_column("calibration_records", "team_review_id")
    op.drop_column("calibration_records", "source")
