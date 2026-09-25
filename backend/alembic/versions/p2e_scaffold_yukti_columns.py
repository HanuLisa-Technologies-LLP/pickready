"""SCAFFOLD FOR WP-E ONLY. DROP THIS FILE AT INTEGRATION; WP-B OWNS THE MIGRATION.

Revision ID: p2e_scaffold_yukti_columns
Revises: 0121_candidate_comms

Phase 2 WP-E (the Candidate Dashboard) reads four columns that WP-B's
`phase2_yukti` migration adds, and that migration had not landed on WP-E's
base. This file adds exactly those four, with the types and CHECKs PLAN-p2
section 3.10 fixes, so the dashboard's SQL runs against the real shape:

    job_candidate_links.yukti_pre_score       REAL NULL, 0..100
    job_candidate_links.yukti_status          VARCHAR(20) NOT NULL DEFAULT 'pending'
    job_candidate_links.yukti_failure_reason  VARCHAR(40) NULL
    tenants.yukti_assessment_weight_pct       SMALLINT NOT NULL DEFAULT 70, 0..100
    functional_skills_reports.must_have_failed BOOLEAN NOT NULL DEFAULT false

It is committed on its own, in a commit named for dropping. Leaving it in
beside WP-B's migration would add the same columns twice and fail the upgrade
loudly, which is the intended way for a missed drop to surface.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "p2e_scaffold_yukti_columns"
down_revision = "0121_candidate_comms"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("job_candidate_links", sa.Column("yukti_pre_score", sa.REAL(), nullable=True))
    op.create_check_constraint(
        "ck_jcl_yukti_pre_score_range",
        "job_candidate_links",
        "yukti_pre_score IS NULL OR (yukti_pre_score >= 0 AND yukti_pre_score <= 100)",
    )
    op.add_column(
        "job_candidate_links",
        sa.Column(
            "yukti_status", sa.String(20), nullable=False, server_default="pending"
        ),
    )
    op.create_check_constraint(
        "ck_jcl_yukti_status",
        "job_candidate_links",
        "yukti_status IN ('pending','scored','not_assessed','legacy')",
    )
    op.add_column(
        "job_candidate_links",
        sa.Column("yukti_failure_reason", sa.String(40), nullable=True),
    )
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
        "ck_tenants_yukti_weight_pct",
        "tenants",
        "yukti_assessment_weight_pct BETWEEN 0 AND 100",
    )
    op.add_column(
        "functional_skills_reports",
        sa.Column(
            "must_have_failed", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )


def downgrade() -> None:
    op.drop_column("functional_skills_reports", "must_have_failed")
    op.drop_constraint("ck_tenants_yukti_weight_pct", "tenants", type_="check")
    op.drop_column("tenants", "yukti_assessment_weight_pct")
    op.drop_column("job_candidate_links", "yukti_failure_reason")
    op.drop_constraint("ck_jcl_yukti_status", "job_candidate_links", type_="check")
    op.drop_column("job_candidate_links", "yukti_status")
    op.drop_constraint(
        "ck_jcl_yukti_pre_score_range", "job_candidate_links", type_="check"
    )
    op.drop_column("job_candidate_links", "yukti_pre_score")
