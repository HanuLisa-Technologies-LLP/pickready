"""Link the 30 resume-corpus candidates to every mock-company job.

Revision ID: 0010_mock_candidate_matrix
Revises: 0009_tenant_company_profile
"""
from alembic import op
import sqlalchemy as sa


revision = "0010_mock_candidate_matrix"
down_revision = "0009_tenant_company_profile"
branch_labels = None
depends_on = None


MOCK_TENANT_DOMAINS = (
    "sarkar-corp.local",
    "acrm-corp.local",
    "specter-co.local",
)


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO job_candidate_links (
                id, tenant_id, job_id, candidate_id, profile_id, source,
                hm_access_granted, created_at
            )
            SELECT
                gen_random_uuid(), j.tenant_id, j.id, c.id, profile.id,
                'databank', false, now()
            FROM jobs j
            JOIN tenants t ON t.id = j.tenant_id
            CROSS JOIN candidates c
            JOIN LATERAL (
                SELECT p.id
                FROM profiles p
                WHERE p.candidate_id = c.id
                ORDER BY p.created_at DESC, p.id DESC
                LIMIT 1
            ) profile ON true
            WHERE t.domain IN (
                'sarkar-corp.local',
                'acrm-corp.local',
                'specter-co.local'
            )
              AND c.email LIKE '%@candidates.pickready.test'
            ON CONFLICT (job_id, candidate_id) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    # Seed links may already have acquired scoring, outreach, or report history.
    # Preserve them instead of deleting operational data during downgrade.
    pass
