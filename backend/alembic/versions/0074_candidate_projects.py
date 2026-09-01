"""Candidate projects and derived Project Evidence (Project Evidence brief).

Revision ID: 0074_candidate_projects
Revises: 0073_telemetry_events

One row per submitted project. The row persists DERIVED intelligence only:
the original uploaded artifacts are staged temporarily in object storage and
deleted after the evidence is validated and persisted, so no column here may
ever reference an original artifact once `original_deleted_at` is stamped.
`intake_objects_json` holds the temporary keys precisely so deletion is
verifiable and retryable rather than assumed.

Candidate-scoped (candidates is the tenant-NULL shareable table), CASCADE on
candidate deletion: derived project evidence is personal data and goes with
the person.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0074_candidate_projects"
down_revision = "0073_telemetry_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "candidate_projects",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "candidate_id",
            UUID(as_uuid=True),
            sa.ForeignKey("candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("repository_url", sa.String(1000)),
        sa.Column("submission_kind", sa.String(20), nullable=False),
        sa.Column(
            "status", sa.String(40), nullable=False, server_default="submitted"
        ),
        sa.Column("failure_code", sa.String(60)),
        sa.Column("status_detail", sa.Text()),
        sa.Column("files_json", JSONB),
        sa.Column("intake_objects_json", JSONB),
        sa.Column("evidence_json", JSONB),
        sa.Column("evidence_units_json", JSONB),
        sa.Column("ai_interpretation_json", JSONB),
        sa.Column("evidence_strength", sa.String(20)),
        sa.Column("telemetry_json", JSONB),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("original_deleted_at", sa.DateTime(timezone=True)),
        sa.Column(
            "deletion_attempts", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # The evidence-strength vocabulary is words only (no number ever
        # reaches a client); the CHECK keeps a drifted writer honest at the
        # database, the same discipline the G4 dispositions use.
        sa.CheckConstraint(
            "evidence_strength IS NULL OR evidence_strength IN "
            "('Strong', 'Moderate', 'Limited', 'Insufficient')",
            name="ck_candidate_projects_strength_vocabulary",
        ),
    )
    op.create_index(
        "ix_candidate_projects_candidate", "candidate_projects", ["candidate_id"]
    )
    # RLS mirrors the parent `candidates` policy: a project row is visible
    # exactly when its owning candidate row is (databank candidates carry
    # tenant_id NULL and are shareable by design). Writes come only from the
    # candidate-portal and worker sessions, both of which run with bypass_rls,
    # so the WITH CHECK admits nothing else.
    op.execute("ALTER TABLE candidate_projects ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE candidate_projects FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY candidate_projects_owner_visibility ON candidate_projects
        USING (
            current_setting('app.bypass_rls', true) = 'on'
            OR EXISTS (
                SELECT 1 FROM candidates c
                WHERE c.id = candidate_projects.candidate_id
                  AND (
                    c.tenant_id IS NULL
                    OR c.tenant_id = current_setting('app.tenant_id', true)::uuid
                  )
            )
        )
        WITH CHECK (current_setting('app.bypass_rls', true) = 'on')
        """
    )


def downgrade() -> None:
    op.drop_index("ix_candidate_projects_candidate", table_name="candidate_projects")
    op.drop_table("candidate_projects")
