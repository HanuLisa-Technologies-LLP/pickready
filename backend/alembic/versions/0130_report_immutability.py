"""Phase 5 grading: the report is insert-only, the evaluation is one live row.

Revision ID: 0130_report_immutability
Revises: 0129_route_scrap

PLAN-p5 section 3.10, WP5-D. Expand-only for the application's reads, fully
reversible, and it drops nothing.

1. `report_dimensions`
   * `score` DROP NOT NULL. A skill Miti could not assess carries NO score:
     the final attempt writes it "Not assessed" (P5-D4), and a number there
     would be a grade nobody made.
   * `assessment_status` (graded | unanswered | not_assessed), NOT NULL
     DEFAULT 'graded', plus the CHECK that ties it to the score: NULL exactly
     when not assessed. Existing rows are `graded`, because history is not
     reinterpreted.
   * `remark_provenance` (model | template | catalogue), NULL on every row
     written before this revision: how an older remark was written was never
     recorded, and inventing it would be provenance nobody observed.
2. `functional_skills_reports`
   * `overall_status` (graded | not_assessed), `ai_score_json`,
     `generation_provenance_json`, `contract_version`, `contract_digest`,
     `category_grades_json`. `must_have_failed` already exists (0122).
3. IMMUTABILITY IN THE DATABASE (P5-D12). A BEFORE UPDATE trigger on both
   tables raises, and UPDATE is revoked from `pickready_app`. DELETE stays
   permitted: the job-closure purge and candidate erasure need it. A rule
   enforced in a service is a rule the next writer does not know about.
4. `evaluations` becomes ONE LIVE ROW per application (P5-D13):
   `superseded_at`, `status` (complete | not_assessed), `attempts` (at least
   one) and `contract_digest`. Every older duplicate is stamped superseded,
   NEVER deleted (review dispositions keep their `evaluation_id`), and the
   partial unique index `uq_evaluations_live_link` makes the writer's
   `INSERT ... ON CONFLICT ... DO UPDATE` the only way a second run lands.

No table is created, so no RLS policy is added: the existing tenant policies
cover the new columns.

DOWNGRADE drops what this adds and re-grants UPDATE. It REFUSES while any
`report_dimensions.score` is NULL, because restoring NOT NULL would need a
number for a skill nobody graded.
"""
from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0130_report_immutability"
down_revision = "0129_route_scrap"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

#: Restated as of this revision; `miti.grades` owns the live vocabulary and
#: `tests/test_report_insert_only.py` pins the two together.
DIMENSION_STATUSES: tuple[str, ...] = ("graded", "unanswered", "not_assessed")
REMARK_SOURCES: tuple[str, ...] = ("model", "template", "catalogue")
OVERALL_STATUSES: tuple[str, ...] = ("graded", "not_assessed")
EVALUATION_STATUSES: tuple[str, ...] = ("complete", "not_assessed")

IMMUTABLE_TABLES: tuple[str, ...] = ("functional_skills_reports", "report_dimensions")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def upgrade() -> None:
    # ── 1. report_dimensions ────────────────────────────────────────────────
    op.alter_column("report_dimensions", "score", nullable=True)
    op.add_column(
        "report_dimensions",
        sa.Column(
            "assessment_status",
            sa.String(16),
            nullable=False,
            server_default="graded",
        ),
    )
    op.create_check_constraint(
        "ck_report_dimensions_assessment_status",
        "report_dimensions",
        _in("assessment_status", DIMENSION_STATUSES),
    )
    op.create_check_constraint(
        "ck_report_dimensions_score_iff_assessed",
        "report_dimensions",
        "(assessment_status = 'not_assessed') = (score IS NULL)",
    )
    op.add_column(
        "report_dimensions", sa.Column("remark_provenance", sa.String(12))
    )
    op.create_check_constraint(
        "ck_report_dimensions_remark_provenance",
        "report_dimensions",
        f"remark_provenance IS NULL OR {_in('remark_provenance', REMARK_SOURCES)}",
    )

    # ── 2. functional_skills_reports ────────────────────────────────────────
    op.add_column(
        "functional_skills_reports", sa.Column("overall_status", sa.String(16))
    )
    op.create_check_constraint(
        "ck_functional_reports_overall_status",
        "functional_skills_reports",
        f"overall_status IS NULL OR {_in('overall_status', OVERALL_STATUSES)}",
    )
    for name in ("ai_score_json", "generation_provenance_json", "category_grades_json"):
        op.add_column("functional_skills_reports", sa.Column(name, JSONB))
    op.add_column(
        "functional_skills_reports", sa.Column("contract_version", sa.Integer())
    )
    op.add_column(
        "functional_skills_reports", sa.Column("contract_digest", sa.String(64))
    )

    # ── 3. Immutability ─────────────────────────────────────────────────────
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prism_report_is_immutable()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'PRISM reports are immutable: % on % is refused',
                lower(TG_OP), TG_TABLE_NAME
                USING ERRCODE = 'raise_exception';
        END;
        $$
        """
    )
    for table in IMMUTABLE_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_immutable
            BEFORE UPDATE ON {table}
            FOR EACH ROW EXECUTE FUNCTION prism_report_is_immutable()
            """
        )
        op.execute(f"REVOKE UPDATE ON {table} FROM pickready_app")

    # ── 4. evaluations: one live row per application ────────────────────────
    op.add_column(
        "evaluations", sa.Column("superseded_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "evaluations",
        sa.Column("status", sa.String(16), nullable=False, server_default="complete"),
    )
    op.create_check_constraint(
        "ck_evaluations_status", "evaluations", _in("status", EVALUATION_STATUSES)
    )
    op.add_column(
        "evaluations",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_check_constraint("ck_evaluations_attempts", "evaluations", "attempts >= 1")
    op.add_column("evaluations", sa.Column("contract_digest", sa.String(64)))

    connection = op.get_bind()
    superseded = connection.execute(
        sa.text(
            """
            UPDATE evaluations e
               SET superseded_at = now()
             WHERE e.superseded_at IS NULL
               AND EXISTS (
                     SELECT 1 FROM evaluations n
                      WHERE n.link_id = e.link_id
                        AND (n.created_at, n.id) > (e.created_at, e.id)
                   )
            """
        )
    ).rowcount
    logger.info("0130 evaluations superseded=%d (none deleted)", superseded or 0)
    op.create_index(
        "uq_evaluations_live_link",
        "evaluations",
        ["link_id"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
    )


def downgrade() -> None:
    connection = op.get_bind()
    unscored = connection.execute(
        sa.text("SELECT count(*) FROM report_dimensions WHERE score IS NULL")
    ).scalar_one()
    if unscored:
        raise RuntimeError(
            f"0130 downgrade refused: {unscored} report_dimensions rows carry no "
            "score (skills written Not assessed), and restoring NOT NULL would "
            "need a number nobody graded"
        )

    op.drop_index("uq_evaluations_live_link", table_name="evaluations")
    op.drop_constraint("ck_evaluations_attempts", "evaluations", type_="check")
    op.drop_constraint("ck_evaluations_status", "evaluations", type_="check")
    for name in ("contract_digest", "attempts", "status", "superseded_at"):
        op.drop_column("evaluations", name)

    for table in IMMUTABLE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_immutable ON {table}")
        op.execute(f"GRANT UPDATE ON {table} TO pickready_app")
    op.execute("DROP FUNCTION IF EXISTS prism_report_is_immutable()")

    op.drop_constraint(
        "ck_functional_reports_overall_status", "functional_skills_reports", type_="check"
    )
    for name in (
        "contract_digest",
        "contract_version",
        "category_grades_json",
        "generation_provenance_json",
        "ai_score_json",
        "overall_status",
    ):
        op.drop_column("functional_skills_reports", name)

    op.drop_constraint(
        "ck_report_dimensions_remark_provenance", "report_dimensions", type_="check"
    )
    op.drop_column("report_dimensions", "remark_provenance")
    op.drop_constraint(
        "ck_report_dimensions_score_iff_assessed", "report_dimensions", type_="check"
    )
    op.drop_constraint(
        "ck_report_dimensions_assessment_status", "report_dimensions", type_="check"
    )
    op.drop_column("report_dimensions", "assessment_status")
    op.alter_column("report_dimensions", "score", nullable=False)
