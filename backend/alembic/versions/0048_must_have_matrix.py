"""Rename the PPI matrix to Must-have / Nice-to-have and fold technical into it.

Revision ID: 0048_must_have_matrix
Revises: 0047_latency_indexes

WHAT THIS IS
------------
Draft v4 of the product specification renames two of the three PPI aspects and
moves the technical track inside the first of them:

    primary_skill    -> must_have     (and this is where rubric-scored,
                                       technical questions now live)
    secondary_skill  -> nice_to_have
    behavioural      -> behavioural    (unchanged)

WHY THE VALUES MOVE RATHER THAN NEW ONES BEING ADDED ALONGSIDE
--------------------------------------------------------------
The spec calls these RENAMES, not new aspects: "Must-have (renamed from Primary
Skills)", "Nice-to-have (renamed from Secondary Skills)". A job's Primary Skills
and its Must-have items are the same criteria under a new name, so carrying both
vocabularies would mean every read path had to accept either and every report
would have to be interpreted against whichever build wrote it. The values are
migrated in place, in both tables, so there is exactly one vocabulary in the
database when this finishes.

`report_dimensions` is migrated too, and that is deliberate even though a report
is immutable. Immutability is about the GRADES and REMARKS a report states, not
about the internal key its rows are grouped by; a historic report regrades and
renders identically under the new key, and leaving it on the old one would make
the report renderer carry a compatibility branch forever.

THE `technical` CATEGORY IS LEFT ALONE
--------------------------------------
Reports written before today carry `report_dimensions.category = 'technical'`
rows scored against the old standalone technical bank. They are NOT rewritten
into must_have: those rows were graded against a different framework, with no
`required_level` and no place on a radar, and relabelling them would silently
assert they were assessed as Must-have items when they were not. They stay
readable under their own key and no new row is ever written with it.

RUBRICS MOVE ONTO THE UNIFIED QUESTION ROW
------------------------------------------
`candidate_questions` gains `rubric_json` and `generated_at`, which is what lets
one table carry the whole blended conversation. A Must-have or Nice-to-have
question is scored against its OWN rubric, so the rubric must be written with the
question and stored before the candidate reads either -- exactly the guarantee
`candidate_technical_questions` was built to give, now given by the row the
unified conversation actually asks from. `rubric_json` stays NULL on a
Behavioural row, which is not an omission: a Behavioural answer is scored by
judgement because there is no single correct answer to weigh it against.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0048_must_have_matrix"
down_revision = "0047_latency_indexes"
branch_labels = None
depends_on = None


#: (old, new). Ordered so a reader can see the whole rename at once.
RENAMES = (
    ("primary_skill", "must_have"),
    ("secondary_skill", "nice_to_have"),
)

NEW_CATEGORIES = "('must_have', 'nice_to_have', 'behavioural')"
OLD_CATEGORIES = "('primary_skill', 'secondary_skill', 'behavioural')"


def _rewrite(table: str, pairs: tuple[tuple[str, str], ...]) -> None:
    for old, new in pairs:
        op.execute(
            sa.text(
                f"UPDATE {table} SET category = :new WHERE category = :old"
            ).bindparams(new=new, old=old)
        )


def upgrade() -> None:
    # The CHECK has to come off first: the UPDATE below writes values it forbids.
    op.drop_constraint("ck_job_competencies_category", "job_competencies", type_="check")
    _rewrite("job_competencies", RENAMES)
    _rewrite("report_dimensions", RENAMES)
    op.create_check_constraint(
        "ck_job_competencies_category",
        "job_competencies",
        f"category IN {NEW_CATEGORIES}",
    )

    # ── The unified question row ─────────────────────────────────────────────
    # Nullable on purpose, both of them. A Behavioural question has no rubric,
    # and a question whose generation degraded has no `generated_at` -- that
    # NULL is the honest record that the candidate read a deterministic probe,
    # and it is what the telemetry counts to make a quiet degradation visible.
    op.add_column(
        "candidate_questions",
        sa.Column("rubric_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "candidate_questions",
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── The Gap Analysis & Action Plan (spec §9.6) ───────────────────────────
    # A new column rather than a reuse of `suggested_probes_json`, and that is
    # the point: Gap Analysis replaces that section entirely, has a different
    # shape (grouped by aspect, each gap carrying its grade, its reused remark
    # and its probes), and the old column is deliberately left in place and
    # unwritten so a rollback of this release needs no data restore. Reports
    # written before today keep theirs and still render it.
    op.add_column(
        "functional_skills_reports",
        sa.Column(
            "gap_analysis_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )

    # ── Job setup state for the new workflow ─────────────────────────────────
    # `question_target` is the total question count resolved ONCE per job at
    # setup, from the grade's range and how many items that job's matrix
    # actually holds (spec 5.4). Stored rather than recomputed so every
    # candidate on the job is asked the same NUMBER of questions even if the
    # matrix is edited later -- the count is part of what makes two reports
    # comparable, exactly as the matrix itself is.
    op.add_column("jobs", sa.Column("question_target", sa.Integer(), nullable=True))
    # Stamped when the reporting authority finishes the SWOT intake. NULL means
    # the intake has not happened, which is a gate the PPI matrix waits behind.
    op.add_column("jobs", sa.Column("swot_completed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "swot_completed_at")
    op.drop_column("jobs", "question_target")
    op.drop_column("functional_skills_reports", "gap_analysis_json")
    op.drop_column("candidate_questions", "generated_at")
    op.drop_column("candidate_questions", "rubric_json")
    op.drop_constraint("ck_job_competencies_category", "job_competencies", type_="check")
    _rewrite("job_competencies", tuple((new, old) for old, new in RENAMES))
    _rewrite("report_dimensions", tuple((new, old) for old, new in RENAMES))
    op.create_check_constraint(
        "ck_job_competencies_category",
        "job_competencies",
        f"category IN {OLD_CATEGORIES}",
    )
