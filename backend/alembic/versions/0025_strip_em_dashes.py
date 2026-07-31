"""Strip em dashes from candidate-facing content.

The client's 2026-07-28 rule is "no em dashes anywhere in the UI on this
platform". The source sweep removed 223 of them from `frontend/`, but that only
covers text the CODE writes. Seeded and generated CONTENT lives in Postgres and
renders straight onto the public application page, so an em dash there breaks
the rule just as visibly. This was caught by actually loading a live job posting
and reading it, not by any test.

103 rows were affected across four candidate-facing columns. Every occurrence
was the same shape, a spaced em dash used as a clause break:

    "it is the product — a rounding error is an incident"
    "we are not staffing up to hit a headcount target — we add people when..."
    "we track that people actually take it — managers are accountable..."

A comma reads correctly in all of them, which is the same substitution the
frontend sweep used, so the two stay consistent.

New content is already safe: `app/prompts/` instructs the JD generator not to
emit em dashes. This migration is about the backlog.

`downgrade()` is deliberately a no-op. Reinstating an em dash would mean knowing
which of the commas in a paragraph used to be one, and that information does not
survive the forward migration. Nothing depends on the character, so putting it
back has no value worth the guesswork.

Revision ID: 0025_strip_em_dashes
Revises: 0024_provision_bd_account
"""
from alembic import op

revision = "0025_strip_em_dashes"
down_revision = "0024_provision_bd_account"
branch_labels = None
depends_on = None

# chr(8212) is U+2014 EM DASH. Written as chr() rather than as a literal so this
# file itself stays free of the character it exists to remove, which keeps the
# repo-wide grep honest.
_EM = "chr(8212)"

# Plain text columns a candidate or client can read.
_TEXT_COLUMNS = [
    ("jobs", "about_company"),
    ("jobs", "work_life"),
    ("jobs", "benefits"),
    ("jobs", "jd_markdown"),
    ("tenants", "details"),
    ("tenants", "culture"),
]


def upgrade() -> None:
    # Spaced form first (the clause break) so " - " collapses to one comma
    # rather than leaving a stray space before it.
    for table, column in _TEXT_COLUMNS:
        op.execute(
            f"""
            UPDATE {table}
               SET {column} = replace(
                     replace({column}, ' ' || {_EM} || ' ', ', '),
                     {_EM}, ', ')
             WHERE {column} LIKE '%' || {_EM} || '%'
            """
        )

    # The JD is a jsonb document, so cast to text, substitute, cast back. The
    # em dash cannot appear in a KEY (they are fixed English identifiers set by
    # our own schema), so a whole-document replace touches values only.
    op.execute(
        f"""
        UPDATE jobs
           SET jd_json = replace(
                 replace(jd_json::text, ' ' || {_EM} || ' ', ', '),
                 {_EM}, ', ')::jsonb
         WHERE jd_json::text LIKE '%' || {_EM} || '%'
        """
    )


def downgrade() -> None:
    """Intentionally irreversible. See the module docstring."""
    pass
