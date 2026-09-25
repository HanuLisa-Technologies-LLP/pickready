"""Yukti reads resumes against the saved skills: the link columns, the ratio,
the Must-have fact, the legacy carry-over and the non-applicant correction.

Revision ID: 0122_yukti
Revises: 0121_candidate_comms

Phase 2 WP-B (PLAN-p2 section 3.10, CONTRACT v2 "P2"). Additive except step 5,
which corrects a status and leaves a history row for every row it corrects.

1. `job_candidate_links` gains the seven Yukti columns `yukti.scoring.
   apply_outcome` writes and nothing else writes: the pre-assessment score
   (0..100, INTERNAL, never serialized), the status (`pending | scored |
   not_assessed | legacy`), the failure reason, the evidence tags, the
   provenance, the time and the profile that was read. `yukti_profile_id` is
   ON DELETE SET NULL: a deleted profile makes the reading stale, which the
   ranked table derives from `yukti_profile_id <> profile_id`, and must never
   delete the application.
2. `tenants.yukti_assessment_weight_pct`, SMALLINT NOT NULL DEFAULT 70, CHECK
   0..100. THE SERVER DEFAULT IS THE SEEDING: every existing tenant reads 70
   the moment the column exists and every future tenant is created with it.
   There is no route and no capability that writes it (PLAN-p2 Q4); a change
   is operator SQL until the owner rules otherwise.
3. `functional_skills_reports.must_have_failed`, BOOLEAN NOT NULL DEFAULT
   false, backfilled from the report's own immutable rows: true when any
   `must_have` dimension scored below 60, the Not Matching boundary of
   `services/rating` (`tests/test_yukti_migration.py` pins the literal against
   the live constant, because a migration must not import application code
   that later changes). No report content is touched: the column records a
   fact the rows already state. Phase 5 writes it on every new report.
4. THE LEGACY CARRY-OVER. Every link the retired matcher scored keeps its old
   number as `yukti_pre_score` with status `legacy`, so a live job does not
   fall back to arrival order on deploy day (PLAN-p2 R1). Its tags stay `[]`
   and the table says it was checked before evidence tags existed.
   `match_score`, `match_rationale`, `match_breakdown_json` and `tier` are
   NOT touched: they are history (S4).
5. THE NON-APPLICANT CORRECTION (audit Part 1 #5). A link the matching run
   minted from the databank, or a single recruiter upload, was written as
   `applied` with no history row, which claims a person read the job and
   applied. Corrected ONLY where every one of these holds, so a real applicant
   can never be touched: the status is `applied`; the link was never invited
   (no `assessment_conversations` row); it never answered the application
   questions (`validation_json IS NULL`); and its provenance is a recruiter's
   or the matcher's (`source = 'databank'`, or `source_type = 'sourced'` from
   the portal's own `direct` door). Each corrected row gets exactly one
   `pipeline_status` row saying why, with no actor. The count is logged. NOT
   reversed by the downgrade: the history rows are the trail, and `sourced` is
   the correct reading under both versions of the code.

No new table, so no new RLS policy: the columns inherit their tables'
policies and grants.
"""
from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0122_yukti"
down_revision = "0121_candidate_comms"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

#: `services/rating`'s Not Matching boundary, written here as a literal
#: because a migration must describe the schema as it was on the day it ran.
#: `tests/test_yukti_migration.py` compares it with the live constant.
NOT_MATCHING_BELOW = 60

#: The one sentence every corrected row's history carries.
CORRECTION_REMARK = (
    "Status corrected: added by AI Matching or a recruiter upload, never applied"
)

#: The label `hiring_pipeline.STAGE_LABELS["sourced"]` renders, for the same
#: reason as the literal above.
SOURCED_STAGE_LABEL = "Sourced, not yet applied"

#: The rows step 5 corrects. Written once, read twice (the count and the
#: UPDATE), so the logged number and the rows changed cannot disagree.
_CORRECTABLE = """
    SELECT l.id, l.tenant_id
      FROM job_candidate_links l
     WHERE l.status = 'applied'
       AND l.validation_json IS NULL
       AND NOT EXISTS (
             SELECT 1 FROM assessment_conversations c
              WHERE c.job_candidate_link_id = l.id
           )
       AND (
             l.source = 'databank'
             OR (l.source_type = 'sourced' AND l.application_source = 'direct')
           )
"""


def upgrade() -> None:
    # ── 1. The link's Yukti columns ─────────────────────────────────────────
    op.add_column(
        "job_candidate_links", sa.Column("yukti_pre_score", sa.Float(), nullable=True)
    )
    op.add_column(
        "job_candidate_links",
        sa.Column(
            "yukti_status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column(
        "job_candidate_links",
        sa.Column("yukti_failure_reason", sa.String(40), nullable=True),
    )
    op.add_column(
        "job_candidate_links",
        sa.Column(
            "evidence_tags_json",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "job_candidate_links",
        sa.Column("yukti_provenance_json", JSONB(), nullable=True),
    )
    op.add_column(
        "job_candidate_links",
        sa.Column("yukti_scored_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "job_candidate_links",
        sa.Column(
            "yukti_profile_id",
            UUID(as_uuid=True),
            sa.ForeignKey("profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_jcl_yukti_pre_score_range",
        "job_candidate_links",
        "yukti_pre_score IS NULL OR (yukti_pre_score >= 0 AND yukti_pre_score <= 100)",
    )
    op.create_check_constraint(
        "ck_jcl_yukti_status",
        "job_candidate_links",
        "yukti_status IN ('pending', 'scored', 'not_assessed', 'legacy')",
    )
    # A scored or legacy reading always has its number, and nothing else does.
    # The ranked table orders on this column; a `scored` row with no score
    # would sort as unranked while claiming to be read.
    op.create_check_constraint(
        "ck_jcl_yukti_score_matches_status",
        "job_candidate_links",
        "(yukti_status IN ('scored', 'legacy')) = (yukti_pre_score IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_jcl_yukti_failure_reason",
        "job_candidate_links",
        "(yukti_status = 'not_assessed') = (yukti_failure_reason IS NOT NULL)",
    )

    # ── 2. The tenant's assessment weight ───────────────────────────────────
    op.add_column(
        "tenants",
        sa.Column(
            "yukti_assessment_weight_pct",
            sa.SmallInteger(),
            nullable=False,
            server_default="70",
        ),
    )
    op.create_check_constraint(
        "ck_tenants_yukti_assessment_weight_pct",
        "tenants",
        "yukti_assessment_weight_pct >= 0 AND yukti_assessment_weight_pct <= 100",
    )

    # ── 3. The Must-have fact on every report ───────────────────────────────
    op.add_column(
        "functional_skills_reports",
        sa.Column(
            "must_have_failed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.execute(
        f"""
        UPDATE functional_skills_reports r
           SET must_have_failed = true
         WHERE EXISTS (
                 SELECT 1 FROM report_dimensions d
                  WHERE d.report_id = r.id
                    AND d.category = 'must_have'
                    AND d.score < {NOT_MATCHING_BELOW}
               )
        """
    )

    # ── 4. The legacy carry-over ────────────────────────────────────────────
    op.execute(
        """
        UPDATE job_candidate_links
           SET yukti_pre_score = LEAST(100, GREATEST(0, match_score)),
               yukti_status = 'legacy',
               yukti_profile_id = profile_id
         WHERE match_score IS NOT NULL
        """
    )

    # ── 5. The non-applicant correction ─────────────────────────────────────
    bind = op.get_bind()
    corrected = bind.execute(sa.text(f"SELECT count(*) FROM ({_CORRECTABLE}) c")).scalar()
    op.execute(
        sa.text(
            f"""
            WITH fixed AS (
                UPDATE job_candidate_links l
                   SET status = 'sourced',
                       current_stage = :label,
                       status_updated_at = now()
                  FROM ({_CORRECTABLE}) c
                 WHERE l.id = c.id
             RETURNING l.id, l.tenant_id
            )
            INSERT INTO pipeline_status
                (id, tenant_id, job_candidate_link_id, status, remarks, set_by, at)
            SELECT gen_random_uuid(), fixed.tenant_id, fixed.id, 'sourced',
                   :remark, NULL, now()
              FROM fixed
            """
        ).bindparams(label=SOURCED_STAGE_LABEL, remark=CORRECTION_REMARK)
    )
    logger.info("0122_yukti: corrected %s link(s) from applied to sourced", corrected)


def downgrade() -> None:
    # Step 5 is deliberately NOT reversed (see the module docstring).
    op.drop_column("functional_skills_reports", "must_have_failed")
    op.drop_constraint(
        "ck_tenants_yukti_assessment_weight_pct", "tenants", type_="check"
    )
    op.drop_column("tenants", "yukti_assessment_weight_pct")
    for name in (
        "ck_jcl_yukti_failure_reason",
        "ck_jcl_yukti_score_matches_status",
        "ck_jcl_yukti_status",
        "ck_jcl_yukti_pre_score_range",
    ):
        op.drop_constraint(name, "job_candidate_links", type_="check")
    for column in (
        "yukti_profile_id",
        "yukti_scored_at",
        "yukti_provenance_json",
        "evidence_tags_json",
        "yukti_failure_reason",
        "yukti_status",
        "yukti_pre_score",
    ):
        op.drop_column("job_candidate_links", column)
