"""Functional Skills assessment workflow and report storage.

Revision ID: 0012_functional_skills
Revises: 0011_safe_archive_lifecycle
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0012_functional_skills"
down_revision = "0011_safe_archive_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("assessment_status", sa.String(40), nullable=False, server_default="questions_pending_review"))
    op.add_column("jobs", sa.Column("assessment_grade", sa.String(40)))
    op.add_column("jobs", sa.Column("questions_generated_at", sa.DateTime(timezone=True)))
    op.add_column("jobs", sa.Column("questions_approved_at", sa.DateTime(timezone=True)))
    op.add_column("jobs", sa.Column("question_reminder_sent_at", sa.DateTime(timezone=True)))

    op.create_table(
        "technical_questions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("skill", sa.String(255), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("rubric_json", postgresql.JSONB(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("job_id", "ordinal", name="uq_technical_question_job_ordinal"),
    )
    op.create_index("ix_technical_questions_job", "technical_questions", ["job_id"])

    op.create_table(
        "assessment_conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_candidate_link_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("job_candidate_links.id", ondelete="CASCADE"), nullable=False),
        sa.Column("grade", sa.String(40), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="active"),
        sa.Column("next_question_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("job_candidate_link_id", name="uq_assessment_conversation_link"),
    )
    op.create_index("ix_assessment_conversations_job", "assessment_conversations", ["job_id"])

    op.create_table(
        "assessment_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("assessment_conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("speaker", sa.String(20), nullable=False),
        sa.Column("domain", sa.String(20), nullable=False),
        sa.Column("question_key", sa.String(255)),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_assessment_messages_conversation", "assessment_messages", ["conversation_id", "ordinal"])

    op.create_table(
        "functional_skills_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_candidate_link_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("job_candidate_links.id", ondelete="CASCADE"), nullable=False),
        sa.Column("grade", sa.String(40), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="ready"),
        sa.Column("overall_summary", sa.Text(), nullable=False),
        sa.Column("validation_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("suggested_probes_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("synthesized_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("job_candidate_link_id", name="uq_functional_report_link"),
    )
    op.create_index("ix_functional_reports_job", "functional_skills_reports", ["job_id"])

    op.create_table(
        "report_dimensions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("report_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("functional_skills_reports.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("remark", sa.Text(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("report_id", "category", "name", name="uq_report_dimension"),
    )
    op.create_index("ix_report_dimensions_report", "report_dimensions", ["report_id", "category", "ordinal"])


def downgrade() -> None:
    op.drop_table("report_dimensions")
    op.drop_table("functional_skills_reports")
    op.drop_table("assessment_messages")
    op.drop_table("assessment_conversations")
    op.drop_table("technical_questions")
    for name in ("question_reminder_sent_at", "questions_approved_at", "questions_generated_at", "assessment_grade", "assessment_status"):
        op.drop_column("jobs", name)
