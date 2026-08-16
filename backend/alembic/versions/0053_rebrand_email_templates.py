"""Rebrand PickReady to ReadyPick in stored email templates.

The 2026-08-16 rename swept the source tree, but `email_templates` rows are
DATA, and the seeding helpers that write them (`api/companies._ensure_invite_
template` and the OTP/payment template seeders) all guard on "create only if
absent". So every tenant onboarded before the rename keeps its original row and
would go on mailing staff invitations, OTP codes and payment notices under the
old brand, while every surface the code renders says the new one.

This is the same lesson as `0025_strip_em_dashes`: sweeping the source only
covers text the CODE writes, and content that lives in Postgres renders to a
real person just as visibly. Found by querying the table, not by any test.

SCOPE, AND WHAT IS DELIBERATELY LEFT ALONE
------------------------------------------
Only `email_templates.subject` and `.body` are rewritten. Every other column in
the database that matches "pickready" is excluded on purpose:

  * `report_dimensions.description` ("PickReady Functional Index dimension",
    2770 rows) -- reports are IMMUTABLE (claude.md), and every one of these is a
    legacy PFI-era row for a retired index that no current code path writes. A
    written report is a permanent record of the criteria it was written
    against, brand included.
  * `assessment_messages.content` and `profiles.resume_text` -- a candidate's
    own words and their own document. An answer is never re-worded.
  * `audit_log.metadata_json` -- an append-only audit record.
  * `users.email`, `candidates.email`, `email_log.recipient_email`,
    `otp_challenges.identifier` -- real addresses. Rewriting a user's email
    would break the Firebase uid binding they sign in with.
  * `profiles.resume_url`, `resume_public_id`, `resume_metadata_json` -- object
    storage paths that point at real bytes in a real bucket.

Case is preserved by substituting both forms: "PickReady" -> "ReadyPick" and
"pickready" -> "readypick". No template contains the brand in any other casing.

`downgrade()` restores the old spelling, which is honest here (unlike 0025):
the substitution is exact and loses no information.

Revision ID: 0053_rebrand_email_templates
Revises: 0052_remove_company_page
"""
from alembic import op

revision = "0053_rebrand_email_templates"
down_revision = "0052_remove_company_page"
branch_labels = None
depends_on = None


def _swap(old_title: str, old_lower: str, new_title: str, new_lower: str) -> None:
    op.execute(
        f"""
        UPDATE email_templates
           SET subject = replace(replace(subject, '{old_title}', '{new_title}'),
                                 '{old_lower}', '{new_lower}'),
               body    = replace(replace(body,    '{old_title}', '{new_title}'),
                                 '{old_lower}', '{new_lower}')
         WHERE subject ILIKE '%{old_lower}%' OR body ILIKE '%{old_lower}%'
        """
    )


def upgrade() -> None:
    _swap("PickReady", "pickready", "ReadyPick", "readypick")


def downgrade() -> None:
    _swap("ReadyPick", "readypick", "PickReady", "pickready")
