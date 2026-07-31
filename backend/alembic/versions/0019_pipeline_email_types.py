"""Widen `email_log.email_type` for the six pipeline transition emails.

Spec §4.1 lists an email per pipeline stage. Six of those types did not exist:
assessment_invitation, assessment_complete, interview_scheduled,
interview_completed, offer_extended, joined. The CHECK constraint from
migration 0016 enumerated only the original six, so writing any of them would
have failed at insert time.

The constraint is REPLACED rather than dropped: an unconstrained free-text
column is how a typo becomes a permanently unfilterable row in the email log.

Revision ID: 0019_pipeline_email_types
Revises: 0018_job_posting_lifecycle
"""
from alembic import op

revision = "0019_pipeline_email_types"
down_revision = "0018_job_posting_lifecycle"
branch_labels = None
depends_on = None

#: Must stay in step with models/email_log.EMAIL_TYPES.
EMAIL_TYPES = (
    "application_confirmation",
    "assessment_reminder",
    "shortlist",
    "rejected",
    "hold",
    "question_bank_reminder",
    "assessment_invitation",
    "assessment_complete",
    "interview_scheduled",
    "interview_completed",
    "offer_extended",
    "joined",
)

ORIGINAL_TYPES = EMAIL_TYPES[:6]


def _replace_check(values: tuple[str, ...]) -> None:
    op.drop_constraint("ck_email_log_type", "email_log", type_="check")
    op.create_check_constraint(
        "ck_email_log_type",
        "email_log",
        "email_type IN (" + ", ".join(f"'{value}'" for value in values) + ")",
    )


def upgrade() -> None:
    _replace_check(EMAIL_TYPES)


def downgrade() -> None:
    # Rows carrying one of the new types would violate the narrower constraint,
    # so they are retired to the closest original type rather than deleted —
    # an email that was genuinely sent must stay in the audit trail.
    op.execute(
        """
        UPDATE email_log SET email_type = 'assessment_reminder'
        WHERE email_type IN ('assessment_invitation', 'assessment_complete')
        """
    )
    op.execute(
        """
        UPDATE email_log SET email_type = 'shortlist'
        WHERE email_type IN ('interview_scheduled', 'interview_completed',
                             'offer_extended', 'joined')
        """
    )
    _replace_check(ORIGINAL_TYPES)
