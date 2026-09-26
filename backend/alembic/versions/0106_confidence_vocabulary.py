"""One confidence vocabulary, and a column wide enough to hold it.

Revision ID: 0106_confidence_vocabulary
Revises: 0105_drishti_profiles

`evaluations.confidence` was created by 0059 as varchar(10) with
CHECK (confidence IN ('high', 'medium', 'low')). The aggregator that WRITES it
produces four values and two of them fit neither half of that declaration:
`miti.aggregation` emits 'high', 'moderate', 'low' and 'insufficient'
(CONFIDENCE_HIGH and its three siblings), so 'moderate' violates the CHECK and
'insufficient' violates the CHECK and the width at twelve characters.

Nothing caught it because the only deployed environment holds zero candidates,
so no real evaluation has ever been written there, and 'medium' is a value this
codebase has never produced from any path. The failure is a write that raises
inside `run_functional_assessment` after Miti's five evaluators and Siddhi's
synthesis have already been paid for.

`calibration_records.predicted_confidence` carries the same value from the same
source (`services/calibration` copies the evaluation's confidence onto the
divergence record) at the same width, with no CHECK at all, so it fails on
'insufficient' by length alone.

This migration makes the column match its one writer. The vocabulary is the
aggregator's, because the aggregator is the only thing that produces it, and a
second vocabulary maintained by hand in a constraint is the shape rule 5 exists
to forbid. Any historical 'medium' is rewritten to 'moderate' rather than
carried alongside it, for the same reason.
"""
from __future__ import annotations

from alembic import op

revision = "0106_confidence_vocabulary"
down_revision = "0105_drishti_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The CHECK goes first. Postgres validates a surviving constraint against
    # the altered column, and 'medium' has to stop being legal before the rows
    # holding it are rewritten.
    op.execute("ALTER TABLE evaluations DROP CONSTRAINT ck_evaluations_confidence")
    op.execute("ALTER TABLE evaluations ALTER COLUMN confidence TYPE varchar(12)")
    op.execute("UPDATE evaluations SET confidence = 'moderate' WHERE confidence = 'medium'")
    op.execute(
        """
        ALTER TABLE evaluations ADD CONSTRAINT ck_evaluations_confidence
            CHECK (confidence IS NULL OR confidence IN
                   ('high', 'moderate', 'low', 'insufficient'))
        """
    )

    op.execute(
        "ALTER TABLE calibration_records "
        "ALTER COLUMN predicted_confidence TYPE varchar(12)"
    )
    op.execute(
        "UPDATE calibration_records SET predicted_confidence = 'moderate' "
        "WHERE predicted_confidence = 'medium'"
    )
    # A CHECK here too, so the copy cannot drift from the source it is copied
    # from. It was absent in 0059, which is why the width was the only thing
    # standing between this column and a value nothing could interpret.
    op.execute(
        """
        ALTER TABLE calibration_records
            ADD CONSTRAINT ck_calibration_predicted_confidence
            CHECK (predicted_confidence IS NULL OR predicted_confidence IN
                   ('high', 'moderate', 'low', 'insufficient'))
        """
    )


def downgrade() -> None:
    # Narrowing cannot be lossless: 'insufficient' does not fit in ten
    # characters and has no pre-0106 spelling. It becomes NULL, which the
    # column has always permitted and which reads as "not recorded" rather
    # than as a confidence nobody computed. Stated here because a downgrade
    # that quietly rewrites a measurement is worse than one that admits it.
    op.execute(
        "ALTER TABLE calibration_records "
        "DROP CONSTRAINT ck_calibration_predicted_confidence"
    )
    op.execute(
        "UPDATE calibration_records SET predicted_confidence = 'medium' "
        "WHERE predicted_confidence = 'moderate'"
    )
    op.execute(
        "UPDATE calibration_records SET predicted_confidence = NULL "
        "WHERE predicted_confidence = 'insufficient'"
    )
    op.execute(
        "ALTER TABLE calibration_records "
        "ALTER COLUMN predicted_confidence TYPE varchar(10)"
    )

    op.execute("ALTER TABLE evaluations DROP CONSTRAINT ck_evaluations_confidence")
    op.execute("UPDATE evaluations SET confidence = 'medium' WHERE confidence = 'moderate'")
    op.execute("UPDATE evaluations SET confidence = NULL WHERE confidence = 'insufficient'")
    op.execute("ALTER TABLE evaluations ALTER COLUMN confidence TYPE varchar(10)")
    op.execute(
        """
        ALTER TABLE evaluations ADD CONSTRAINT ck_evaluations_confidence
            CHECK (confidence IS NULL OR confidence IN ('high', 'medium', 'low'))
        """
    )
