"""Gate 5: a sourced candidate is not an applicant until they apply.

WHAT WAS WRONG
----------------
`POST /jobs/{id}/candidates/databank` wrote every uploaded resume as
`status = 'applied'`. That row says a person read this job, wanted it, and
submitted an application with their notice period and their expected
compensation in it. None of that happened: a recruiter moved a file out of
their own filing cabinet. Every count, every funnel and every "applicants"
figure downstream inherited the claim, and there was no way to tell an inbound
application from a sourcing decision after the fact.

WHAT THIS MIGRATION DOES
--------------------------
Widens two CHECK constraints so the new vocabulary is storable:

  * `ck_jcl_status` gains `sourced`, the stage a databank upload now starts in.
    Its only forward edge in `services/hiring_pipeline` is `applied`, so a
    sourced candidate cannot be invited to an assessment or shortlisted. The
    MISSING EDGE is the enforcement -- not a flag a future caller has to
    remember to check.
  * `ck_email_log_type` gains `databank_invitation`, the mail that asks such a
    person to sign in and apply. It is deliberately not one of the pipeline
    transition emails: nothing about the candidate's state changes when it is
    sent, because they have no application yet.

NOTHING IS BACKFILLED, AND THAT IS A DECISION
-----------------------------------------------
Existing databank rows keep `applied`. Rewriting them would be a guess: some of
those people were genuinely contacted and did engage through channels this
product never saw, and demoting them would remove candidates from live
pipelines and from lists recruiters are working through today. The distinction
starts being recorded from here; it is not retro-asserted over rows that were
written when nobody was tracking it.

Revision ID: 0078_sourced_pipeline_stage
Revises: 0077_job_early_closure
"""
from alembic import op

revision = "0078_sourced_pipeline_stage"
down_revision = "0077_job_early_closure"
branch_labels = None
depends_on = None

#: Mirrors `hiring_pipeline.ALL_STATUSES`. `offered` is the retired synonym,
#: still storable so historic rows stay readable.
_STATUSES_WITH_SOURCED = (
    "sourced", "applied", "assessment_invited", "assessment_in_progress",
    "assessment_completed", "shortlisted", "rejected", "interview_scheduled",
    "interview_completed", "offer_extended", "joined", "hold", "offered",
)
_STATUSES_BEFORE = tuple(s for s in _STATUSES_WITH_SOURCED if s != "sourced")

#: Mirrors `models/email_log.EMAIL_TYPES`.
_EMAIL_TYPES_WITH_INVITATION = (
    "application_confirmation", "assessment_reminder", "shortlist", "rejected",
    "hold", "question_bank_reminder", "assessment_invitation",
    "assessment_complete", "interview_scheduled", "interview_completed",
    "offer_extended", "joined", "databank_invitation",
)
_EMAIL_TYPES_BEFORE = tuple(
    t for t in _EMAIL_TYPES_WITH_INVITATION if t != "databank_invitation"
)


def _in_list(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({joined})"


def _swap(table: str, name: str, column: str, values: tuple[str, ...]) -> None:
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, _in_list(column, values))


def upgrade() -> None:
    _swap(
        "job_candidate_links", "ck_jcl_status", "status", _STATUSES_WITH_SOURCED
    )
    _swap(
        "email_log",
        "ck_email_log_type",
        "email_type",
        _EMAIL_TYPES_WITH_INVITATION,
    )


def downgrade() -> None:
    # A row carrying the new value would fail the narrower constraint, so the
    # downgrade folds it back to the value it replaced rather than failing the
    # migration. `sourced` becomes `applied`, which is what the column said
    # before this change; the invitation rows become plain outreach records.
    op.execute(
        "UPDATE job_candidate_links SET status = 'applied' WHERE status = 'sourced'"
    )
    op.execute(
        "DELETE FROM email_log WHERE email_type = 'databank_invitation'"
    )
    _swap("job_candidate_links", "ck_jcl_status", "status", _STATUSES_BEFORE)
    _swap("email_log", "ck_email_log_type", "email_type", _EMAIL_TYPES_BEFORE)
