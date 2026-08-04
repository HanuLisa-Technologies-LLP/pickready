"""Send jobs that claim an approved assessment setup, but have none, back to review.

Revision ID: 0035_unreview_empty_setups
Revises: 0034_guard_rls_guc_cast

WHAT WENT WRONG
---------------
Measured on production 2026-08-01, across all four tenants:

    tenant          assessment_status       competencies  technical questions
    Sarkar Corp     ready_for_candidates    0             0     (9 jobs)
    Specter & Co.   ready_for_candidates    0             0    (10 jobs)
    ACRM Corp       ready_for_candidates    0             0    (10 jobs)

Twenty-nine jobs carried BOTH `questions_approved_at` and
`framework_approved_at`, and therefore `assessment_status =
'ready_for_candidates'`, while `job_competencies` and `technical_questions`
held nothing at all for them. They were stamped in bulk rather than through
`POST /assessments/jobs/{id}/finalize` and `.../framework/finalize`, which are
the only two handlers that write those columns and which both refuse an empty
set (422). Nothing in the running product can produce this state; it predates
the handlers.

WHY IT MATTERS
--------------
`ready_for_candidates` is the promise the whole invitation path turns on.
`api/pipeline.select_candidates_for_assessment` reads it and lets the recruiter
mail an invitation; `api/assessments._candidate_link` reads it and opens the
door. With the setup empty, the candidate walks through both gates and lands on
`_ensure_conversation_ready`, which can only answer 409 "We are preparing your
assessment. Please try again in a moment." -- forever, as far as that candidate
can tell, because the thing being prepared was never reviewed by anyone.

It also defeats the review gate itself. The Hiring Manager's setup screen shows
the framework as approved and FROZEN (`_reject_frozen`), so the one control that
could fix it is disabled on exactly the jobs that need it.

WHAT THIS DOES
--------------
Clears both approval stamps and returns `assessment_status` to
`questions_pending_review` for any job whose active framework or active
technical bank is empty. That is the honest state: the setup has not been
reviewed, because there was nothing to review. The job stays PUBLISHED and keeps
taking and ranking applications -- publishing and assessment readiness are
independent -- and the recruiter's setup screen unfreezes so the framework can
be generated and finalised normally.

Deliberately NOT done here: enqueuing generation. A migration runs in the
release job, which has no broker connection, and `_ensure_conversation_ready`
plus the job-setup screen already enqueue it on demand.

The condition is written as "approved but empty", so a job that was genuinely
reviewed is untouched, and re-running the migration is a no-op.

DOWNGRADE
---------
Irreversible by design: the pre-migration state asserted a review that never
happened, and restoring it would re-break the invitation path. `downgrade` is a
no-op rather than a lie.
"""
from alembic import op

revision = "0035_unreview_empty_setups"
down_revision = "0034_guard_rls_guc_cast"
branch_labels = None
depends_on = None


# A job is "set up" only when BOTH halves actually hold rows. `is_active` is the
# same filter the API reads through (`ppi.load_framework`, `_bank_out`), so a
# framework whose every entry was soft-deleted counts as empty here too.
_EMPTY_SETUP = """
    (
        NOT EXISTS (
            SELECT 1 FROM job_competencies c
            WHERE c.job_id = jobs.id AND c.is_active
        )
        OR NOT EXISTS (
            SELECT 1 FROM technical_questions q
            WHERE q.job_id = jobs.id AND q.is_active
        )
    )
"""


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE jobs
           SET questions_approved_at = NULL,
               framework_approved_at = NULL,
               assessment_status     = 'questions_pending_review'
         WHERE (
                   questions_approved_at IS NOT NULL
                OR framework_approved_at IS NOT NULL
                OR assessment_status = 'ready_for_candidates'
               )
           AND {_EMPTY_SETUP}
        """
    )


def downgrade() -> None:
    """No-op. See the module docstring: the prior state was not a valid one."""
