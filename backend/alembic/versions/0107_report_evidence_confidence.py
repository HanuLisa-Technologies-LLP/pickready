"""Evidence Confidence per rated line, and the two new PRISM sections.

Revision ID: 0107_report_evidence_confidence
Revises: 0106_confidence_vocabulary

WHAT THIS ADDS
----------------
Two columns on `report_dimensions`, so every rated line records how well
corroborated it was and which kinds of source it rested on, and two JSONB
columns on `functional_skills_reports`, so the Recommended Human Validation
Points and the Evidence vs Claim Summary survive with the document they belong
to.

NULLABLE, AND NEVER BACKFILLED
--------------------------------
Every report already written predates all four columns, and a report is
immutable. There is no honest value to backfill: the evidence set a 2026-08
report was written from is not reconstructable now, and writing `low` into
those rows would state a finding about an evidence base nobody assembled.
NULL means "written before this was recorded", which is exactly what the
renderers read it as: an older report shows its grades without a confidence
word beside them and without the two new sections, rather than raising.

THE CHECK USES THE AGGREGATOR'S VOCABULARY
--------------------------------------------
`high | moderate | low | insufficient`, the same four words migration 0106 put
on `evaluations.confidence` and the same four `miti.aggregation` emits. A fifth
spelling maintained by hand in a constraint is the shape rule 5 forbids, and
0106 exists because that had already happened once: `evaluations.confidence`
was created accepting 'medium', a value this codebase has never produced.

varchar(12) for the same reason 0106 chose it: 'insufficient' is twelve
characters, and a width that cannot hold the longest legal value is a write
that raises after the whole assessment has been paid for.

THE SOURCE LIST IS KEYS, NOT LABELS
-------------------------------------
`evidence_sources` stores `resume`, `answer`, `bgv` and their siblings, never
"Resume" or "Employer Confirmed". The words are copy and copy gets corrected;
a label frozen into an immutable row could not be. `evidence_confidence.py`
owns the key-to-word mapping and is read at render time.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0107_report_evidence_confidence"
down_revision = "0106_confidence_vocabulary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "report_dimensions",
        sa.Column("evidence_confidence", sa.String(length=12), nullable=True),
    )
    op.add_column(
        "report_dimensions",
        sa.Column("evidence_sources", postgresql.JSONB(), nullable=True),
    )
    op.execute(
        """
        ALTER TABLE report_dimensions
            ADD CONSTRAINT ck_report_dimensions_evidence_confidence
            CHECK (evidence_confidence IS NULL OR evidence_confidence IN
                   ('high', 'moderate', 'low', 'insufficient'))
        """
    )

    op.add_column(
        "functional_skills_reports",
        sa.Column("validation_points_json", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "functional_skills_reports",
        sa.Column("claim_evidence_json", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("functional_skills_reports", "claim_evidence_json")
    op.drop_column("functional_skills_reports", "validation_points_json")
    op.execute(
        "ALTER TABLE report_dimensions "
        "DROP CONSTRAINT ck_report_dimensions_evidence_confidence"
    )
    op.drop_column("report_dimensions", "evidence_sources")
    op.drop_column("report_dimensions", "evidence_confidence")
