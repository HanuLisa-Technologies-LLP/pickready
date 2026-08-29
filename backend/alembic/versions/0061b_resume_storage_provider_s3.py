"""Let the resume provider vocabulary say what the code actually writes: s3.

Revision ID: 0060_resume_provider_s3
Revises: 0061_rbac_cardinality_audit

THE FILE NAME AND THE REVISION ID DISAGREE, DELIBERATELY, AND TEMPORARILY
--------------------------------------------------------------------------
The file is `0061b_` because that is where this migration sits in the graph:
after `0061_rbac_cardinality_audit` and before `0062_embedding_provenance`. The
revision id still says `0060_` because `0062_embedding_provenance` already names
it, and renaming the id here without repointing 0062 in the same commit would
leave `alembic upgrade head` raising KeyError for everybody -- the exact defect
this file's sibling fix repaired in `0058_single_embedding_space`.

The intended end state is this migration at the TAIL, as
`0063_resume_storage_provider_s3.py` with `revision = "0063_resume_provider_s3"`
and `down_revision = "0062_embedding_provenance"`. Reaching it is a single
atomic change across TWO files and must be made as one commit:

  1. here: revision -> "0063_resume_provider_s3",
           down_revision -> "0062_embedding_provenance", rename the file;
  2. in `0062_embedding_provenance.py`:
           down_revision -> "0061_rbac_cardinality_audit".

None of these revisions has been applied to a deployed database, so renaming the
id is safe once both halves land together.
`tests/test_db_enum_parity.py::test_the_migration_chain_resolves_end_to_end`
asserts the single head that proves it.

THE DEFECT
----------
`0046_private_gcs_resumes` created

    CHECK (resume_storage_provider IN ('cloudinary', 'gcs'))

when the private bucket was Google's. The AWS move (spec-doc5 Part D) changed
`services/resume_storage.STORAGE_PROVIDER` from "gcs" to "s3" and rewrote the
transport, and no migration widened the vocabulary behind it. Nothing in the
schema was asked to agree with the constant, so nothing noticed.

HOW IT WOULD HAVE MANIFESTED
-----------------------------
Every resume upload writes this column. On a database migrated to head, the
INSERT into `profiles` is refused by PostgreSQL with

    CheckViolationError: new row for relation "profiles" violates check
    constraint "ck_profiles_resume_storage_provider"

which surfaces as a 500 on `POST /jobs/{id}/apply`, on the candidate's My
Profile resume replacement, and on the databank bulk upload. That is the whole
apply flow, for every candidate, on every tenant. Ten tests already covered it
and all ten were reporting SKIPPED with "no database reachable", so the suite
was green while the product's front door was closed.

WHY THIS IS ADDITIVE AND NOT A REPLACEMENT
-------------------------------------------
'cloudinary' and 'gcs' stay in the vocabulary. Rows written before each move
still carry them, `object_storage.is_legacy_uri` reads them to tell an
un-migrated object apart from a missing one, and
`scripts/migrate_resumes_to_s3.py` selects on them. Narrowing the vocabulary
would strand exactly the rows the migration scripts exist to find.
"""
from alembic import op

revision = "0060_resume_provider_s3"
# Rebased onto 0061 rather than 0059. Two migrations were authored against
# 0059 concurrently (`0060_company_dna_versioning` and this one), which forks the
# history: `alembic upgrade head` then refuses with "Multiple head revisions are
# present", and if it did not, a rolling deploy would apply one branch and leave
# the other silently unrun. The revision ID is left alone because
# `0062_embedding_provenance` already names it; only the parent moved, so the
# file number reads out of sequence and the graph does not.
# `tests/test_db_enum_parity.py::test_the_migration_chain_resolves_end_to_end`
# asserts the single head that makes this checkable.
down_revision = "0061_rbac_cardinality_audit"
branch_labels = None
depends_on = None

_CONSTRAINT = "ck_profiles_resume_storage_provider"
_TABLE = "profiles"
_COLUMN = "resume_storage_provider"

#: Every value the application can write, current and legacy. Kept in step with
#: `services.resume_storage.STORAGE_PROVIDER` / `LEGACY_STORAGE_PROVIDER` and
#: the pre-GCS 'cloudinary' rows, and asserted against this constraint by
#: `tests/test_db_enum_parity.py`.
_VOCABULARY = ("cloudinary", "gcs", "s3")
_PREVIOUS = ("cloudinary", "gcs")


def _recreate(values: tuple[str, ...]) -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    rendered = ", ".join(f"'{value}'" for value in values)
    op.create_check_constraint(
        _CONSTRAINT, _TABLE, f"{_COLUMN} IN ({rendered})"
    )


def upgrade() -> None:
    _recreate(_VOCABULARY)


def downgrade() -> None:
    # Any row already written as 's3' would fail the narrower constraint, and a
    # downgrade that leaves the table unable to satisfy its own CHECK is a
    # downgrade that cannot be applied twice. Send those rows back to the value
    # the narrower vocabulary used for the same private-bucket arrangement.
    op.execute(
        f"UPDATE {_TABLE} SET {_COLUMN} = 'gcs' WHERE {_COLUMN} = 's3'"
    )
    _recreate(_PREVIOUS)
