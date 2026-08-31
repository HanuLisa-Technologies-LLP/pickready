"""Yukti's pre-screen grade lands on the application, as a WORD and a NUMBER.

Revision ID: 0065_prescreen_grade
Revises: 0064_sutra_seven_stage
Create Date: 2026-08-29

THREE COLUMNS, AND THE SPLIT BETWEEN TWO OF THEM IS THE POINT
--------------------------------------------------------------
`prescreen_grade` holds A / B / C / Hold, which is what a person is allowed to
see. `prescreen_score` holds the 0 to 100 internal figure, which spec-doc6 D8
permits on the Candidate Dashboard and forbids in the delivered PRISM Report.
`prescreen_json` holds the evidence ledger the grade was written from.

They are separate columns rather than two keys of one JSON blob because that is
what makes D8 enforceable rather than merely stated. A serialiser building a
report selects `prescreen_grade`; there is no number in the value it gets back,
so there is nothing for it to leak. A single `prescreen_json` carrying both
would put the number one `.get("score")` away from every consumer, and the rule
would be a convention somebody has to remember at every call site instead of a
property of the schema.

WHY NOT ON THE SQLALCHEMY MODEL
---------------------------------
Same reason `job_candidate_links.match_breakdown_json` and `jobs.embedding` are
not: this is the established pattern in this schema for a column whose only
writer is one service. `services/hiring/prescreen.store` is that writer, it uses
raw SQL, and the read side is a projection the dashboard asks for by name.

WHY NULLABLE, AND WHAT A NULL MEANS
-------------------------------------
Every existing application predates this grader, and a backfill inside a
migration would have to run the grader against every stored resume inside a DDL
transaction. A NULL therefore means exactly one thing and it is honest: this
application has not been pre-screened. It does not mean Hold, which is a graded
outcome saying a person should look, and the dashboard must not render the two
the same way. Re-grading the backlog is the regrade script's job, not this
file's.

WHY THERE IS A CHECK ON THE GRADE AND NOT ON THE SCORE
--------------------------------------------------------
The grade vocabulary is closed and is a product surface: four values, from the
Candidate Dashboard Specification's column 3, and in particular NO REJECTING
VALUE. The constraint is what makes "no pre-screen has ever rejected anybody" a
fact about the database rather than a claim about the code, and it is the same
enforcement-by-absence the review-disposition table already uses.

The score is deliberately unconstrained beyond its range. It is engine state,
its arithmetic comes from `runbook_data/`, and a CHECK encoding today's band
edges would have to be migrated every time the Runbook moved a tier strength,
which is precisely the second source of truth the parity test exists to prevent.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "0065_prescreen_grade"
down_revision = "0064_sutra_seven_stage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "job_candidate_links",
        sa.Column("prescreen_grade", sa.String(length=8), nullable=True),
    )
    op.add_column(
        "job_candidate_links",
        sa.Column("prescreen_score", sa.Float(), nullable=True),
    )
    op.add_column(
        "job_candidate_links",
        sa.Column(
            "prescreen_json",
            JSONB(),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_jcl_prescreen_grade",
        "job_candidate_links",
        "prescreen_grade IS NULL OR prescreen_grade IN ('A', 'B', 'C', 'Hold')",
    )
    op.create_check_constraint(
        "ck_jcl_prescreen_score_range",
        "job_candidate_links",
        "prescreen_score IS NULL OR (prescreen_score >= 0 AND prescreen_score <= 100)",
    )
    # The dashboard's fast-triage workflow sorts and filters a job's candidate
    # list on this column, and the list is paginated, so the sort has to happen
    # in SQL. Partial, because a NULL means "not pre-screened" and is never what
    # a triage filter is looking for.
    op.create_index(
        "ix_jcl_job_prescreen",
        "job_candidate_links",
        ["job_id", "prescreen_grade"],
        postgresql_where=sa.text("prescreen_grade IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_jcl_job_prescreen", table_name="job_candidate_links")
    op.drop_constraint(
        "ck_jcl_prescreen_score_range", "job_candidate_links", type_="check"
    )
    op.drop_constraint("ck_jcl_prescreen_grade", "job_candidate_links", type_="check")
    op.drop_column("job_candidate_links", "prescreen_json")
    op.drop_column("job_candidate_links", "prescreen_score")
    op.drop_column("job_candidate_links", "prescreen_grade")
