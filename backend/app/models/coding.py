"""The coding question's answer key, the Run history, and the final submission.

Three tables (migration 0124), one per question a coding question has to
answer about itself, and they are deliberately NOT one table:

  coding_question_keys  WHAT THE ANSWER IS. The hidden tests, the reference
                        solution the question was validated with, and the
                        approach notes for the quality reviewer. Written once,
                        in the same transaction as its `candidate_questions`
                        row, and never changed.
  coding_runs           WHAT THE CANDIDATE TRIED. One row per press of Run,
                        against the VISIBLE sample tests only. It is also the
                        per-question abuse ledger: the hard cap on runs is a
                        count over this table, not a Redis window that fails
                        open.
  coding_submissions    WHAT THE CANDIDATE HANDED IN, AND WHAT IT DID. One row
                        per final coding answer, executed against the hidden
                        tests by a dispatched task after commit, then reviewed.

THE ANSWER KEY IS A TABLE OF ITS OWN SO THAT IT CANNOT BE SERIALISED BY ACCIDENT
-------------------------------------------------------------------------------
`candidate_questions.payload_json` crosses the candidate boundary through an
allowlist (`assessment_formats.types.candidate_view`), and every other format
keeps its key inside that payload. A coding question's key is different in
kind: a hidden test's stdin is part of the answer, and a candidate program can
ECHO its stdin. So the key lives here, with no response schema anywhere, one
reader and writer module (`services/coding_assessment/keys.py`), and the
database refusing a v2 coding payload that carries any of it
(`ck_candidate_questions_coding_key_private`). The sandbox never receives an
expected output either; comparison happens in domain code.

IMMUTABLE BY PRIVILEGE AND BY TRIGGER. UPDATE is revoked from the application
role (0014's default privileges grant it to every new table, so the revoke is
what binds), and `coding_question_key_is_immutable` refuses an UPDATE from
anybody, the owner included. A candidate's grade is computed against this row;
a key that could change after it was used would make every such grade
unreproducible, and nothing would record that it had happened. DELETE is left
to the application role: job closure erasure and candidate erasure cascade
through `candidate_questions`.

NOTHING HERE IS SERIALISED AS IT STANDS. `tests_passed` and `tests_total` are
INTERNAL (a report states the result in spelled-out words, never a digit), and
hidden-test stdout and stderr are never stored at all: they are the one place a
candidate program could copy the answer key into a column somebody later shows.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin

__all__ = [
    "CodingQuestionKey",
    "CodingRun",
    "CodingSubmission",
    "CODE_SOURCE_MAX_CHARS",
    "CODING_OUTPUT_EXCERPT_MAX_CHARS",
    "HIDDEN_TESTS_MAX",
    "RUN_QUEUED",
    "RUN_COMPLETE",
    "RUN_UNAVAILABLE",
    "RUN_FAILED",
    "RUN_STATUSES",
    "EXECUTION_PENDING",
    "EXECUTION_SUBMITTED",
    "EXECUTION_COMPLETE",
    "EXECUTION_NO_CODE",
    "EXECUTION_STATUSES",
    "REVIEW_PENDING",
    "REVIEW_COMPLETE",
    "REVIEW_FAILED",
    "REVIEW_NOT_APPLICABLE",
    "REVIEW_STATUSES",
    "submission_source_digest",
]

#: The longest program the product stores. Equal to
#: `assessment_formats.types.MAX_CODE_CHARS`, the answer-shape ceiling, and
#: pinned against it by `tests/test_coding_tables.py`: a column narrower than
#: the answer it holds would refuse a valid submission at INSERT.
CODE_SOURCE_MAX_CHARS = 20_000

#: Compiler output and a VISIBLE test's stderr are kept to this many
#: characters. Equal to the default `code_execution_max_output_chars`.
CODING_OUTPUT_EXCERPT_MAX_CHARS = 4_000

#: The database's ceiling on hidden tests per question. The configured range
#: (`coding_hidden_tests_min/max`) sits inside it; the settings validator
#: refuses a maximum above this.
HIDDEN_TESTS_MAX = 30

# ── coding_runs.status ───────────────────────────────────────────────────────
#: Handed to the sandbox; a poll will collect it.
RUN_QUEUED = "queued"
#: Collected; `results_json` holds the visible tests' outcomes.
RUN_COMPLETE = "complete"
#: The sandbox could not take or finish it (outage, queue full, lost ticket).
#: Never the candidate's fault, and never graded: a Run is not a submission.
RUN_UNAVAILABLE = "unavailable"
#: The sandbox refused OUR request as malformed. A defect on this side,
#: recorded with its class name rather than retried into the same refusal.
RUN_FAILED = "failed"
#: Mirrored by `ck_coding_runs_status`.
RUN_STATUSES: tuple[str, ...] = (RUN_QUEUED, RUN_COMPLETE, RUN_UNAVAILABLE, RUN_FAILED)

# ── coding_submissions.execution_status ──────────────────────────────────────
#: Stored, not yet handed to the sandbox.
EXECUTION_PENDING = "pending"
#: Handed to the sandbox and its tickets COMMITTED, so a worker killed while
#: polling collects the same run instead of submitting the code twice.
EXECUTION_SUBMITTED = "submitted"
#: Every hidden test has an outcome.
EXECUTION_COMPLETE = "complete"
#: The final answer was empty. An evidence gap, never executed and never a
#: zero: nothing was submitted, so nothing failed.
EXECUTION_NO_CODE = "no_code"
#: Mirrored by `ck_coding_submissions_execution_status`.
EXECUTION_STATUSES: tuple[str, ...] = (
    EXECUTION_PENDING,
    EXECUTION_SUBMITTED,
    EXECUTION_COMPLETE,
    EXECUTION_NO_CODE,
)

# ── coding_submissions.review_status ─────────────────────────────────────────
REVIEW_PENDING = "pending"
REVIEW_COMPLETE = "complete"
#: The quality review could not be written. The sweep retries it; it is never
#: replaced by a template.
REVIEW_FAILED = "failed"
#: Nothing to review (an empty submission).
REVIEW_NOT_APPLICABLE = "not_applicable"
#: Mirrored by `ck_coding_submissions_review_status`.
REVIEW_STATUSES: tuple[str, ...] = (
    REVIEW_PENDING,
    REVIEW_COMPLETE,
    REVIEW_FAILED,
    REVIEW_NOT_APPLICABLE,
)


def submission_source_digest(language: str, code: str) -> str:
    """sha256 hex over exactly what a submission executes.

    Stored on `coding_submissions.source_sha256` when the answer is accepted,
    and recomputed from `assessment_answers.answer_json` before execution, so
    the program that ran is provably the program that was handed in. The
    language is part of the digest because the same text is a different
    program in a different language.
    """
    payload = f"{language}\n{code}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class CodingQuestionKey(Base, CreatedAtMixin):
    """The answer key of one coding question. INSERT-ONLY; never serialised.

    One row per `candidate_questions` row of type `coding` written in the v2
    shape, keyed by the question's own id. Read and written ONLY by
    `services/coding_assessment/keys.py`;
    `tests/test_coding_key_confinement.py` walks the AST of `app/` to keep it
    that way.
    """

    __tablename__ = "coding_question_keys"
    __table_args__ = (
        CheckConstraint(
            "jsonb_typeof(hidden_tests_json) = 'array' "
            f"AND jsonb_array_length(hidden_tests_json) BETWEEN 1 AND {HIDDEN_TESTS_MAX}",
            name="ck_coding_question_keys_hidden_tests",
        ),
        CheckConstraint(
            f"char_length(reference_source) BETWEEN 1 AND {CODE_SOURCE_MAX_CHARS}",
            name="ck_coding_question_keys_reference_source",
        ),
        CheckConstraint(
            "char_length(reference_language) > 0",
            name="ck_coding_question_keys_reference_language",
        ),
        CheckConstraint(
            "char_length(expected_approach) BETWEEN 1 AND 4000",
            name="ck_coding_question_keys_expected_approach",
        ),
        CheckConstraint(
            "jsonb_typeof(validation_json) = 'object'",
            name="ck_coding_question_keys_validation",
        ),
        Index("ix_coding_question_keys_tenant", "tenant_id"),
    )

    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidate_questions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    #: `[{"key": "h1", "stdin": "...", "expected_stdout": "..."}, ...]`, in the
    #: order the tests are run and reported.
    hidden_tests_json: Mapped[list] = mapped_column(JSONB, nullable=False)
    #: The configured language the reference solution is written in.
    reference_language: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Model-written, therefore untrusted: it was run ONLY in the sandbox, and
    #: it is kept so the validation can be reproduced, never shown to anybody.
    reference_source: Mapped[str] = mapped_column(Text, nullable=False)
    #: What a correct solution does and which edge cases matter. Input to the
    #: code-quality reviewer; it describes the solution, so it is private too.
    expected_approach: Mapped[str] = mapped_column(Text, nullable=False)
    #: How the question was validated, with NO test content: the provider name,
    #: the outcome class of every reference run, each language's starter-code
    #: probe, the generating model and prompt version, a digest of the hidden
    #: tests, and when. `{"provider": ..., "reference": {...}, "starters": {...},
    #: "generation": {...}, "hidden_digest": ..., "validated_at": ...}`.
    validation_json: Mapped[dict] = mapped_column(JSONB, nullable=False)


class CodingRun(Base, UUIDPKMixin, CreatedAtMixin):
    """One press of Run: the candidate's current code against the VISIBLE tests.

    Visible tests are public, so their stdout and stderr may be kept and shown
    back. The row is also the only server-side copy of what the candidate had
    typed at that moment, which is what an auto-submit on timeout falls back to.
    """

    __tablename__ = "coding_runs"
    __table_args__ = (
        UniqueConstraint("conversation_id", "client_token", name="uq_coding_runs_client_token"),
        CheckConstraint(
            "status IN ('queued', 'complete', 'unavailable', 'failed')",
            name="ck_coding_runs_status",
        ),
        CheckConstraint(
            f"char_length(source) <= {CODE_SOURCE_MAX_CHARS}", name="ck_coding_runs_source"
        ),
        CheckConstraint(
            "char_length(client_token) BETWEEN 1 AND 64", name="ck_coding_runs_client_token"
        ),
        CheckConstraint("char_length(language) > 0", name="ck_coding_runs_language"),
        # A queued run has not finished; every other state has. A row claiming
        # completion with no results is the timestamp-without-work shape.
        CheckConstraint(
            "(status = 'queued') = (completed_at IS NULL)", name="ck_coding_runs_completion"
        ),
        CheckConstraint(
            "status <> 'complete' OR results_json IS NOT NULL", name="ck_coding_runs_results"
        ),
        Index("ix_coding_runs_question", "conversation_id", "question_id", "created_at"),
        Index("ix_coding_runs_tenant", "tenant_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assessment_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidate_questions.id", ondelete="CASCADE"), nullable=False
    )
    #: Minted by the client once per click and reused on a retry, so a double
    #: click or a replayed request is one run, not two.
    client_token: Mapped[str] = mapped_column(String(64), nullable=False)
    language: Mapped[str] = mapped_column(String(20), nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    #: One of `RUN_STATUSES`.
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    #: The sandbox's opaque ticket (`ExecutionTicket` refs and keys).
    provider_ref_json: Mapped[dict | None] = mapped_column(JSONB)
    #: Per VISIBLE test: `{key, outcome, passed, stdout, stderr, compile_output}`,
    #: truncated. Never a hidden test.
    results_json: Mapped[list | None] = mapped_column(JSONB)
    #: The exception CLASS name when the run could not complete. Never a message.
    error_class: Mapped[str | None] = mapped_column(String(80))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CodingSubmission(Base, UUIDPKMixin, CreatedAtMixin):
    """One final coding answer, its hidden-test execution and its review.

    The code itself stays in `assessment_answers.answer_json`: one copy of a
    candidate's program. `source_sha256` binds this row to exactly that copy.
    """

    __tablename__ = "coding_submissions"
    __table_args__ = (
        UniqueConstraint("answer_id", name="uq_coding_submissions_answer"),
        CheckConstraint(
            "execution_status IN ('pending', 'submitted', 'complete', 'no_code')",
            name="ck_coding_submissions_execution_status",
        ),
        CheckConstraint(
            "review_status IN ('pending', 'complete', 'failed', 'not_applicable')",
            name="ck_coding_submissions_review_status",
        ),
        CheckConstraint("char_length(source_sha256) = 64", name="ck_coding_submissions_digest"),
        CheckConstraint("execution_attempts >= 0", name="ck_coding_submissions_attempts"),
        CheckConstraint(
            "tests_total IS NULL OR (tests_total BETWEEN 1 AND "
            f"{HIDDEN_TESTS_MAX} AND tests_passed BETWEEN 0 AND tests_total)",
            name="ck_coding_submissions_counts",
        ),
        # A completed execution carries its counts, its per-test outcomes and
        # when it ran: a "complete" row without them would be a stamp without
        # the work behind it.
        CheckConstraint(
            "execution_status <> 'complete' OR (tests_total IS NOT NULL AND "
            "tests_passed IS NOT NULL AND test_results_json IS NOT NULL AND "
            "executed_at IS NOT NULL)",
            name="ck_coding_submissions_complete",
        ),
        CheckConstraint(
            "execution_status <> 'submitted' OR provider_ref_json IS NOT NULL",
            name="ck_coding_submissions_ticket",
        ),
        # An empty answer is an evidence gap: nothing ran, nothing is reviewed.
        CheckConstraint(
            "(execution_status = 'no_code') = (review_status = 'not_applicable')",
            name="ck_coding_submissions_no_code",
        ),
        CheckConstraint(
            "review_status <> 'complete' OR (review_json IS NOT NULL AND reviewed_at IS NOT NULL)",
            name="ck_coding_submissions_review",
        ),
        CheckConstraint(
            f"char_length(compile_output) <= {CODING_OUTPUT_EXCERPT_MAX_CHARS}",
            name="ck_coding_submissions_compile_output",
        ),
        CheckConstraint(
            f"char_length(visible_error_output) <= {CODING_OUTPUT_EXCERPT_MAX_CHARS}",
            name="ck_coding_submissions_visible_error_output",
        ),
        Index("ix_coding_submissions_conversation", "conversation_id"),
        Index("ix_coding_submissions_tenant", "tenant_id"),
        Index(
            "ix_coding_submissions_execution_open",
            "execution_status",
            "created_at",
            postgresql_where=text("execution_status IN ('pending', 'submitted')"),
        ),
        Index(
            "ix_coding_submissions_review_open",
            "review_status",
            "created_at",
            postgresql_where=text("review_status IN ('pending', 'failed')"),
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    answer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assessment_answers.id", ondelete="CASCADE"), nullable=False
    )
    #: Denormalised from the answer for the completion check and the sweep.
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assessment_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidate_questions.id", ondelete="CASCADE"), nullable=False
    )
    #: True when the timer ran out and the server submitted on the candidate's
    #: behalf. Provenance for the recruiter; it changes no scoring.
    auto_submitted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    #: `submission_source_digest(language, code)` at acceptance.
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    #: One of `EXECUTION_STATUSES`.
    execution_status: Mapped[str] = mapped_column(String(20), nullable=False)
    execution_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    #: The exception CLASS name of the last failed attempt. Never a message.
    last_error_class: Mapped[str | None] = mapped_column(String(80))
    #: The sandbox ticket, committed BEFORE polling.
    provider_ref_json: Mapped[dict | None] = mapped_column(JSONB)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: INTERNAL counts. Never serialised as numbers.
    tests_total: Mapped[int | None] = mapped_column(Integer)
    tests_passed: Mapped[int | None] = mapped_column(Integer)
    #: Per hidden test: `{key, outcome, passed, cpu_ms, wall_ms, memory_kb}`.
    #: NO stdin, NO stdout, NO stderr: a program can echo its input.
    test_results_json: Mapped[list | None] = mapped_column(JSONB)
    #: The compiler's output, truncated. Derived from the candidate's own code
    #: only: compilation precedes any input, so it cannot quote a hidden test.
    compile_output: Mapped[str | None] = mapped_column(Text)
    #: stderr from a VISIBLE sample test run beside the hidden ones, truncated.
    #: This is where a runtime error message may be kept; a hidden test's
    #: stderr never is.
    visible_error_output: Mapped[str | None] = mapped_column(Text)
    #: One of `REVIEW_STATUSES`.
    review_status: Mapped[str] = mapped_column(String(20), nullable=False)
    #: INTERNAL criterion scores, reasoning and verbatim code citations.
    review_json: Mapped[dict | None] = mapped_column(JSONB)
    #: Written only for a model-backed review, never for a failed one.
    review_model_id: Mapped[str | None] = mapped_column(String(80))
    review_prompt_version: Mapped[str | None] = mapped_column(String(80))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
