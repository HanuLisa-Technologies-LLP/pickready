"""Proctoring sessions, events and reports; question formats and structured answers.

Revision ID: 0076_proctoring_formats
Revises: 0075_seed_spec_doc6_capabilities

Two specifications land in one migration because they share a boundary: the
behavioural capture of the proctoring specification attaches to the answer
fields the question-format specification introduces, and both hang off the
same assessment session (`assessment_conversations`).

PROCTORING (proctoring-spec-doc.md section 5)
---------------------------------------------
`proctoring_sessions`   one per assessment conversation. Holds the consent
                        stamp, the outcome, the warning count the SERVER
                        decided, the face-descriptor baseline (128 floats, a
                        non-reversible vector and NOT an image), device and
                        session-quality context, and the candidate's own typing
                        baseline as aggregates.
`proctoring_events`     the event stream. Identifiers, timings, aggregates and
                        model confidences only. NO media: no frame, no image,
                        no audio, ever. `tests/test_proctoring_no_media.py`
                        fails the build if a write path for one appears.
`proctoring_reports`    the recruiter-facing report, generated once after the
                        session ends, stored as words (no numeric field) so it
                        can travel inside the delivered PRISM payload under the
                        serialiser-level number ban.
`jobs.proctoring_warning_policy`
                        the one recruiter setting (section 6): what happens at
                        the third warning. Defaults to continue-and-note; the
                        product never terminates by default.

QUESTION FORMATS (assessment-spec-doc.md section 4)
---------------------------------------------------
`candidate_questions` IS the specification's `assessment_questions` table:
one row per question per candidate already existed (`ordinal` is `sequence`,
`competency_id` is `competency`, `rubric_json` is `rubric`). It gains the
format, the type-specific payload, the resume anchor, the time allocation and
the weight. Rows written before this migration are text questions with no
stored anchor and no evidence rubric, so they read as `short_answer`, which is
the honest classification rather than a relabelling.

`assessment_answers` is the structured answer record, one per (conversation,
question): the type-specific answer, when the question was opened and
submitted, the objective auto-score, the AI evaluation with its reasoning, and
the revision count. The transcript in `assessment_messages` is unchanged and
stays the conversational record every scorer already reads; this table is
what the recruiter's Q&A view and the objective scorer read.

RLS follows migration 0013: tenant isolation on every new table, with the
bypass flag for the candidate-portal and worker sessions.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision = "0076_proctoring_formats"
down_revision = "0075_seed_spec_doc6_capabilities"
branch_labels = None
depends_on = None

QUESTION_TYPES = (
    "evidence_based",
    "mcq_single",
    "mcq_multi",
    "fill_blank",
    "coding",
    "short_answer",
)

SESSION_OUTCOMES = (
    "active",
    "completed",
    "terminated_integrity",
    "terminated_warnings",
    "abandoned",
    "technical_failure",
)

SESSION_QUALITIES = ("good", "degraded", "poor")

WARNING_POLICIES = ("terminate", "continue_and_note")

NEW_TABLES = ("proctoring_sessions", "proctoring_events", "proctoring_reports", "assessment_answers")


def _rls(table: str) -> None:
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO pickready_app")
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {table}_tenant_isolation ON {table}
        USING (
            tenant_id = current_setting('app.tenant_id', true)::uuid
            OR current_setting('app.bypass_rls', true) = 'on'
        )
        WITH CHECK (
            tenant_id = current_setting('app.tenant_id', true)::uuid
            OR current_setting('app.bypass_rls', true) = 'on'
        )
        """
    )


def _in_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    # ── The recruiter's one setting (proctoring section 6) ──────────────────
    op.add_column(
        "jobs",
        sa.Column(
            "proctoring_warning_policy",
            sa.String(20),
            nullable=False,
            server_default="continue_and_note",
        ),
    )
    op.create_check_constraint(
        "ck_jobs_proctoring_warning_policy",
        "jobs",
        f"proctoring_warning_policy IN ({_in_list(WARNING_POLICIES)})",
    )

    # ── Question formats on the existing per-candidate question row ─────────
    op.add_column(
        "candidate_questions",
        sa.Column("question_type", sa.String(20), nullable=False, server_default="short_answer"),
    )
    op.add_column(
        "candidate_questions",
        sa.Column("payload_json", JSONB, nullable=False, server_default="{}"),
    )
    op.add_column("candidate_questions", sa.Column("resume_anchor", sa.Text(), nullable=True))
    op.add_column(
        "candidate_questions",
        sa.Column("time_allocation_seconds", sa.Integer(), nullable=False, server_default="180"),
    )
    op.add_column(
        "candidate_questions",
        sa.Column("weight", sa.Float(), nullable=False, server_default="1.0"),
    )
    op.create_check_constraint(
        "ck_candidate_questions_question_type",
        "candidate_questions",
        f"question_type IN ({_in_list(QUESTION_TYPES)})",
    )
    op.create_check_constraint(
        "ck_candidate_questions_weight_positive",
        "candidate_questions",
        "weight > 0",
    )

    # When the prompt on screen was delivered, so time spent per question is
    # measured by the server rather than reported by the client.
    op.add_column(
        "assessment_conversations",
        sa.Column("prompt_shown_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "assessment_answers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("assessment_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "question_id",
            UUID(as_uuid=True),
            sa.ForeignKey("candidate_questions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # The transcript line this answer was rendered into. SET NULL rather
        # than CASCADE: the structured record outlives a transcript edit.
        sa.Column(
            "message_id",
            UUID(as_uuid=True),
            sa.ForeignKey("assessment_messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("question_type", sa.String(20), nullable=False),
        sa.Column("answer_json", JSONB, nullable=False, server_default="{}"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("time_spent_seconds", sa.Integer(), nullable=True),
        # INTERNAL. 0.0 to 1.0 for the objective types; projected to a word or
        # folded into the item's internal score, never serialised as a number.
        sa.Column("auto_score", sa.Float(), nullable=True),
        sa.Column("ai_evaluation_json", JSONB, nullable=True),
        sa.Column("revision_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("conversation_id", "question_id", name="uq_assessment_answer_question"),
        sa.CheckConstraint(
            f"question_type IN ({_in_list(QUESTION_TYPES)})",
            name="ck_assessment_answers_question_type",
        ),
        sa.CheckConstraint(
            "auto_score IS NULL OR (auto_score >= 0 AND auto_score <= 1)",
            name="ck_assessment_answers_auto_score_unit",
        ),
    )
    op.create_index("ix_assessment_answers_conversation", "assessment_answers", ["conversation_id"])

    # ── Proctoring ──────────────────────────────────────────────────────────
    op.create_table(
        "proctoring_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # The assessment session. One proctoring session per conversation.
        sa.Column(
            "conversation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("assessment_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_candidate_link_id",
            UUID(as_uuid=True),
            sa.ForeignKey("job_candidate_links.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "candidate_id",
            UUID(as_uuid=True),
            sa.ForeignKey("candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(30), nullable=False, server_default="active"),
        sa.Column("termination_reason", sa.Text(), nullable=True),
        sa.Column("warnings_used", sa.SmallInteger(), nullable=False, server_default="0"),
        # A descriptor is a non-reversible vector. It is NOT an image and no
        # image can be reconstructed from it. Nullable until the baseline is
        # captured at the system check.
        sa.Column("face_descriptor_baseline", ARRAY(sa.Float()), nullable=True),
        sa.Column("device_context", JSONB, nullable=False, server_default="{}"),
        sa.Column("system_check", JSONB, nullable=False, server_default="{}"),
        sa.Column("session_quality", sa.String(10), nullable=False, server_default="good"),
        # The candidate's OWN typing baseline, as aggregates, computed from
        # their first answers and compared against their later ones. Never a
        # population norm.
        sa.Column("behaviour_profile_json", JSONB, nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("conversation_id", name="uq_proctoring_session_conversation"),
        sa.CheckConstraint(
            f"outcome IN ({_in_list(SESSION_OUTCOMES)})",
            name="ck_proctoring_sessions_outcome",
        ),
        sa.CheckConstraint(
            f"session_quality IN ({_in_list(SESSION_QUALITIES)})",
            name="ck_proctoring_sessions_quality",
        ),
        sa.CheckConstraint(
            "warnings_used >= 0 AND warnings_used <= 3",
            name="ck_proctoring_sessions_warnings_range",
        ),
        sa.CheckConstraint(
            "face_descriptor_baseline IS NULL OR array_length(face_descriptor_baseline, 1) = 128",
            name="ck_proctoring_sessions_descriptor_width",
        ),
    )
    op.create_index("ix_proctoring_sessions_link", "proctoring_sessions", ["job_candidate_link_id"])
    op.create_index("ix_proctoring_sessions_job", "proctoring_sessions", ["job_id"])

    op.create_table(
        "proctoring_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "proctoring_session_id",
            UUID(as_uuid=True),
            sa.ForeignKey("proctoring_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("path", sa.String(1), nullable=False),
        sa.Column("warning_issued", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("warning_number", sa.SmallInteger(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column(
            "question_id",
            UUID(as_uuid=True),
            sa.ForeignKey("candidate_questions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("metadata_json", JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("path IN ('A', 'B', 'C')", name="ck_proctoring_events_path"),
        sa.CheckConstraint(
            "warning_number IS NULL OR (warning_number >= 1 AND warning_number <= 3)",
            name="ck_proctoring_events_warning_number",
        ),
    )
    op.create_index(
        "ix_proctoring_events_session",
        "proctoring_events",
        ["proctoring_session_id", "occurred_at"],
    )
    op.create_index("ix_proctoring_events_occurred", "proctoring_events", ["occurred_at"])

    op.create_table(
        "proctoring_reports",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "proctoring_session_id",
            UUID(as_uuid=True),
            sa.ForeignKey("proctoring_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("report_content", JSONB, nullable=False),
        sa.Column("report_version", sa.String(10), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("proctoring_session_id", name="uq_proctoring_report_session"),
    )

    for table in NEW_TABLES:
        _rls(table)


def downgrade() -> None:
    for table in reversed(NEW_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
    op.drop_table("proctoring_reports")
    op.drop_index("ix_proctoring_events_occurred", table_name="proctoring_events")
    op.drop_index("ix_proctoring_events_session", table_name="proctoring_events")
    op.drop_table("proctoring_events")
    op.drop_index("ix_proctoring_sessions_job", table_name="proctoring_sessions")
    op.drop_index("ix_proctoring_sessions_link", table_name="proctoring_sessions")
    op.drop_table("proctoring_sessions")
    op.drop_index("ix_assessment_answers_conversation", table_name="assessment_answers")
    op.drop_table("assessment_answers")
    op.drop_column("assessment_conversations", "prompt_shown_at")
    op.drop_constraint("ck_candidate_questions_weight_positive", "candidate_questions", type_="check")
    op.drop_constraint("ck_candidate_questions_question_type", "candidate_questions", type_="check")
    op.drop_column("candidate_questions", "weight")
    op.drop_column("candidate_questions", "time_allocation_seconds")
    op.drop_column("candidate_questions", "resume_anchor")
    op.drop_column("candidate_questions", "payload_json")
    op.drop_column("candidate_questions", "question_type")
    op.drop_constraint("ck_jobs_proctoring_warning_policy", "jobs", type_="check")
    op.drop_column("jobs", "proctoring_warning_policy")
