"""Coding execution: the answer key, the Run history, the final submission.

Revision ID: 0124_coding_execution
Revises: 0123_assessment_conversation

Phase 4 WP-4B1 (Vivekium release). Additive only. `models/coding.py` is the
reading guide; this is the schema it describes.

1. `coding_question_keys`, one row per v2 coding question: the hidden tests,
   the reference solution the question was validated with, the reviewer's
   approach notes and a content-free validation record. INSERT-ONLY, twice
   over: UPDATE is REVOKED from `pickready_app` (0014's ALTER DEFAULT
   PRIVILEGES grants it to every new table, so omitting it from a grant list
   omits nothing), and `coding_question_key_is_immutable` refuses an UPDATE
   from ANY role, the owner included. A grade is computed against this row,
   and a key rewritten after it was used would make that grade unreproducible
   with nothing recording why. DELETE stays with the app role: job closure
   erasure and candidate erasure delete `candidate_questions`, and the key
   goes with its question.
2. `coding_runs`, one row per press of Run (visible tests only). UNIQUE on
   (conversation, client token), so a double click is one run; the row count
   per question is the hard cap on runs, which a Redis window cannot be
   because it fails open by design.
3. `coding_submissions`, one row per final coding answer (UNIQUE on the
   answer). The code stays in `assessment_answers.answer_json`, bound here by
   `source_sha256`. CHECK constraints make the states honest: a `complete`
   execution carries its counts and outcomes, a `submitted` one its committed
   ticket, an empty answer is `no_code` with a `not_applicable` review and
   nothing else is. Two partial indexes serve the reconcile sweep.
4. `ck_candidate_questions_coding_key_private` on `candidate_questions`: a
   coding payload may never carry hidden tests or a reference solution, and a
   v2 coding payload may not carry the reviewer's approach notes either (a v1
   payload did, by design, and old rows stay readable). The allowlist in
   `candidate_view` is the first fence; this makes the database the second, so
   a future writer that puts the key in the payload fails at INSERT rather
   than in a leak report.

All three tables: `tenant_id` with ON DELETE CASCADE, ENABLE + FORCE ROW LEVEL
SECURITY, and the `<table>_tenant_isolation` policy (tenant equality or the
explicit bypass flag, USING and WITH CHECK), the 0118 pattern. Every other
foreign key CASCADEs from the conversation, the question or the answer, so the
existing erasure paths remove these rows without naming them.

The downgrade drops the constraint, the trigger, its function and the three
tables. Nothing else existed before this revision, so nothing is restored.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0124_coding_execution"
down_revision = "0123_assessment_conversation"
branch_labels = None
depends_on = None

TENANT = "nullif(current_setting('app.tenant_id', true), '')::uuid"
BYPASS = "current_setting('app.bypass_rls', true) = 'on'"

#: Literals, not imports: a migration applies to the schema as it stood, not
#: to whatever `models/coding.py` says later. `tests/test_coding_tables.py`
#: reads the constraints back and compares them with the model's constants.
_SOURCE_MAX = 20_000
_EXCERPT_MAX = 4_000
_HIDDEN_MAX = 30

#: The payload keys that ARE the answer key. Never in any coding payload.
_KEY_FIELDS = ("hidden_tests", "reference_solution", "reference_source", "reference")

_KEY_IMMUTABILITY = """
CREATE OR REPLACE FUNCTION coding_question_key_is_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'coding_question_keys is insert-only: the key of question % cannot be rewritten',
        OLD.question_id
        USING ERRCODE = 'restrict_violation';
END;
$$
"""


def _array(values: tuple[str, ...]) -> str:
    return "ARRAY[" + ", ".join(f"'{value}'" for value in values) + "]"


_PAYLOAD_PRIVATE = (
    "question_type <> 'coding' OR ("
    f"NOT (payload_json ?| {_array(_KEY_FIELDS)}) "
    "AND (payload_json->>'payload_version' IS NULL "
    "OR NOT (payload_json ? 'expected_approach')))"
)


def _tenant_table(name: str) -> None:
    op.execute(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {name}_tenant_isolation ON {name} "
        f"USING ((tenant_id = {TENANT}) OR ({BYPASS})) "
        f"WITH CHECK ((tenant_id = {TENANT}) OR ({BYPASS}))"
    )


def _tenant_column() -> sa.Column:
    return sa.Column(
        "tenant_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


def upgrade() -> None:
    # ── 1. The answer key ────────────────────────────────────────────────────
    op.create_table(
        "coding_question_keys",
        sa.Column(
            "question_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("candidate_questions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        _tenant_column(),
        sa.Column("hidden_tests_json", postgresql.JSONB(), nullable=False),
        sa.Column("reference_language", sa.String(20), nullable=False),
        sa.Column("reference_source", sa.Text(), nullable=False),
        sa.Column("expected_approach", sa.Text(), nullable=False),
        sa.Column("validation_json", postgresql.JSONB(), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            "jsonb_typeof(hidden_tests_json) = 'array' "
            f"AND jsonb_array_length(hidden_tests_json) BETWEEN 1 AND {_HIDDEN_MAX}",
            name="ck_coding_question_keys_hidden_tests",
        ),
        sa.CheckConstraint(
            f"char_length(reference_source) BETWEEN 1 AND {_SOURCE_MAX}",
            name="ck_coding_question_keys_reference_source",
        ),
        sa.CheckConstraint(
            "char_length(reference_language) > 0",
            name="ck_coding_question_keys_reference_language",
        ),
        sa.CheckConstraint(
            "char_length(expected_approach) BETWEEN 1 AND 4000",
            name="ck_coding_question_keys_expected_approach",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(validation_json) = 'object'",
            name="ck_coding_question_keys_validation",
        ),
    )
    op.create_index("ix_coding_question_keys_tenant", "coding_question_keys", ["tenant_id"])
    op.execute(_KEY_IMMUTABILITY)
    op.execute(
        """
        CREATE TRIGGER trg_coding_question_keys_immutable
        BEFORE UPDATE ON coding_question_keys
        FOR EACH ROW EXECUTE FUNCTION coding_question_key_is_immutable()
        """
    )
    op.execute("GRANT SELECT, INSERT, DELETE ON coding_question_keys TO pickready_app")
    op.execute("REVOKE UPDATE ON coding_question_keys FROM pickready_app")
    _tenant_table("coding_question_keys")

    # ── 2. The Run history ───────────────────────────────────────────────────
    op.create_table(
        "coding_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _tenant_column(),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assessment_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "question_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("candidate_questions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("client_token", sa.String(64), nullable=False),
        sa.Column("language", sa.String(20), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("provider_ref_json", postgresql.JSONB()),
        sa.Column("results_json", postgresql.JSONB()),
        sa.Column("error_class", sa.String(80)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        _created_at(),
        sa.UniqueConstraint(
            "conversation_id", "client_token", name="uq_coding_runs_client_token"
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'complete', 'unavailable', 'failed')",
            name="ck_coding_runs_status",
        ),
        sa.CheckConstraint(
            f"char_length(source) <= {_SOURCE_MAX}", name="ck_coding_runs_source"
        ),
        sa.CheckConstraint(
            "char_length(client_token) BETWEEN 1 AND 64", name="ck_coding_runs_client_token"
        ),
        sa.CheckConstraint("char_length(language) > 0", name="ck_coding_runs_language"),
        sa.CheckConstraint(
            "(status = 'queued') = (completed_at IS NULL)", name="ck_coding_runs_completion"
        ),
        sa.CheckConstraint(
            "status <> 'complete' OR results_json IS NOT NULL", name="ck_coding_runs_results"
        ),
    )
    op.create_index(
        "ix_coding_runs_question",
        "coding_runs",
        ["conversation_id", "question_id", "created_at"],
    )
    op.create_index("ix_coding_runs_tenant", "coding_runs", ["tenant_id"])
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON coding_runs TO pickready_app")
    _tenant_table("coding_runs")

    # ── 3. The final submission ──────────────────────────────────────────────
    op.create_table(
        "coding_submissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _tenant_column(),
        sa.Column(
            "answer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assessment_answers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assessment_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "question_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("candidate_questions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "auto_submitted", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("execution_status", sa.String(20), nullable=False),
        sa.Column(
            "execution_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("last_error_class", sa.String(80)),
        sa.Column("provider_ref_json", postgresql.JSONB()),
        sa.Column("executed_at", sa.DateTime(timezone=True)),
        sa.Column("tests_total", sa.Integer()),
        sa.Column("tests_passed", sa.Integer()),
        sa.Column("test_results_json", postgresql.JSONB()),
        sa.Column("compile_output", sa.Text()),
        sa.Column("visible_error_output", sa.Text()),
        sa.Column("review_status", sa.String(20), nullable=False),
        sa.Column("review_json", postgresql.JSONB()),
        sa.Column("review_model_id", sa.String(80)),
        sa.Column("review_prompt_version", sa.String(80)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        _created_at(),
        sa.UniqueConstraint("answer_id", name="uq_coding_submissions_answer"),
        sa.CheckConstraint(
            "execution_status IN ('pending', 'submitted', 'complete', 'no_code')",
            name="ck_coding_submissions_execution_status",
        ),
        sa.CheckConstraint(
            "review_status IN ('pending', 'complete', 'failed', 'not_applicable')",
            name="ck_coding_submissions_review_status",
        ),
        sa.CheckConstraint(
            "char_length(source_sha256) = 64", name="ck_coding_submissions_digest"
        ),
        sa.CheckConstraint(
            "execution_attempts >= 0", name="ck_coding_submissions_attempts"
        ),
        sa.CheckConstraint(
            "tests_total IS NULL OR (tests_total BETWEEN 1 AND "
            f"{_HIDDEN_MAX} AND tests_passed BETWEEN 0 AND tests_total)",
            name="ck_coding_submissions_counts",
        ),
        sa.CheckConstraint(
            "execution_status <> 'complete' OR (tests_total IS NOT NULL AND "
            "tests_passed IS NOT NULL AND test_results_json IS NOT NULL AND "
            "executed_at IS NOT NULL)",
            name="ck_coding_submissions_complete",
        ),
        sa.CheckConstraint(
            "execution_status <> 'submitted' OR provider_ref_json IS NOT NULL",
            name="ck_coding_submissions_ticket",
        ),
        sa.CheckConstraint(
            "(execution_status = 'no_code') = (review_status = 'not_applicable')",
            name="ck_coding_submissions_no_code",
        ),
        sa.CheckConstraint(
            "review_status <> 'complete' OR "
            "(review_json IS NOT NULL AND reviewed_at IS NOT NULL)",
            name="ck_coding_submissions_review",
        ),
        sa.CheckConstraint(
            f"char_length(compile_output) <= {_EXCERPT_MAX}",
            name="ck_coding_submissions_compile_output",
        ),
        sa.CheckConstraint(
            f"char_length(visible_error_output) <= {_EXCERPT_MAX}",
            name="ck_coding_submissions_visible_error_output",
        ),
    )
    op.create_index(
        "ix_coding_submissions_conversation", "coding_submissions", ["conversation_id"]
    )
    op.create_index("ix_coding_submissions_tenant", "coding_submissions", ["tenant_id"])
    op.create_index(
        "ix_coding_submissions_execution_open",
        "coding_submissions",
        ["execution_status", "created_at"],
        postgresql_where=sa.text("execution_status IN ('pending', 'submitted')"),
    )
    op.create_index(
        "ix_coding_submissions_review_open",
        "coding_submissions",
        ["review_status", "created_at"],
        postgresql_where=sa.text("review_status IN ('pending', 'failed')"),
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON coding_submissions TO pickready_app"
    )
    _tenant_table("coding_submissions")

    # ── 4. The payload may never carry the key ───────────────────────────────
    # Refuse with a count rather than an opaque CHECK failure halfway through.
    # CONTRACT v3 counted no candidate questions on pilot; a database that has
    # rows violating the rule holds an answer key in a candidate-facing column
    # and needs a person, not a migration.
    op.execute(
        f"""
        DO $$
        DECLARE
            affected bigint;
        BEGIN
            SELECT count(*) INTO affected FROM candidate_questions
             WHERE NOT ({_PAYLOAD_PRIVATE});
            IF affected > 0 THEN
                RAISE EXCEPTION
                    'candidate_questions holds % coding payload(s) carrying answer-key '
                    'fields; they must be moved out by hand before this constraint can hold',
                    affected;
            END IF;
        END
        $$;
        """
    )
    op.create_check_constraint(
        "ck_candidate_questions_coding_key_private",
        "candidate_questions",
        _PAYLOAD_PRIVATE,
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_candidate_questions_coding_key_private", "candidate_questions", type_="check"
    )

    for table in ("coding_submissions", "coding_runs", "coding_question_keys"):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")

    op.drop_index("ix_coding_submissions_review_open", table_name="coding_submissions")
    op.drop_index("ix_coding_submissions_execution_open", table_name="coding_submissions")
    op.drop_index("ix_coding_submissions_tenant", table_name="coding_submissions")
    op.drop_index("ix_coding_submissions_conversation", table_name="coding_submissions")
    op.drop_table("coding_submissions")

    op.drop_index("ix_coding_runs_tenant", table_name="coding_runs")
    op.drop_index("ix_coding_runs_question", table_name="coding_runs")
    op.drop_table("coding_runs")

    op.execute(
        "DROP TRIGGER IF EXISTS trg_coding_question_keys_immutable ON coding_question_keys"
    )
    op.execute("DROP FUNCTION IF EXISTS coding_question_key_is_immutable()")
    op.drop_index("ix_coding_question_keys_tenant", table_name="coding_question_keys")
    op.drop_table("coding_question_keys")
