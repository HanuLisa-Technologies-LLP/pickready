"""Embedding provenance, re-embed shadow columns, and detachable human observations.

Revision ID: 0062_embedding_provenance
Revises: 0060_resume_provider_s3
Create Date: 2026-08-29

TWO CHANGES, ONE ARGUMENT
-------------------------
Both halves of this migration answer the same question: after a machine artifact
is replaced, what is still true about the row that survives?

  1. WHICH MODEL PRODUCED THIS VECTOR is currently answerable only by
     inference. `profiles.embedding`, `jobs.embedding` and
     `context_chunks.embedding` are `vector(1024)` columns that hold BGE-M3
     vectors written before the single-vendor consolidation and Voyage vectors
     written after it. A same-width swap is not a same-space swap: a cosine
     distance between the two is a number with no meaning, and nothing about it
     looks wrong. There is no column that says which is which, so retrieval
     mixes two spaces and the only way to find out is to guess from
     `created_at`. That is the root cause spec-doc6 section 7 names, and these
     columns are the fix.

  2. A HUMAN OBSERVATION MUST NOT BE CASCADE-DELETED WITH A MACHINE ARTIFACT.
     `review_dispositions` records that a person looked at a flag and decided
     something; `calibration_records` records a person's later judgment about
     whether a grade turned out to be right. Both referenced `evaluations` with
     ON DELETE CASCADE, so purging the old evaluations would have silently
     erased both. The dispositions table already guards the same value in the
     other direction -- `decided_by` is ON DELETE RESTRICT precisely so a
     departed user cannot leave a row asserting that a human decided while
     being unable to say who -- and the cascade on `evaluation_id` walked
     straight past that guard by destroying the whole row.

WHY THE REFERENCE IS NULLED RATHER THAN THE ROW KEPT INTACT
------------------------------------------------------------
spec-doc6 section 6.2: "keep the remark and null the reference with a migration
note; do not cascade-delete human observations." A dangling `evaluation_id`
pointing at a row that no longer exists would be worse than a NULL, because a
reader cannot tell a dangling pointer from a live one without a join that
returns nothing.

Nulling the reference alone is not enough, though. RBAC section 29 requires
every human remark to preserve author, timestamp, CANDIDATE and JOB context,
and `review_dispositions` carried its candidate and job only THROUGH the
evaluation. So this migration adds `job_id` and `link_id` to that table and
backfills them from the evaluation while the evaluation still exists. A
disposition detached without that backfill would satisfy the letter of the rule
and lose exactly the context the rule exists to protect.

`evaluation_ref` keeps the original identifier as a plain uuid with no foreign
key, so the export written by `app.scripts.legacy_reset --export` can be joined
back to the surviving human row by hand. `detached_note` and `detached_at`
record why it happened; a detachment that left no trace is indistinguishable
from a row that never had a reference.

ROLLING-DEPLOY SAFETY, AND HOW IT IS PROVEN
---------------------------------------------
This is an expand-only migration. Every statement is one of:

    ADD COLUMN ... (nullable, no default, no volatile default)
    ALTER COLUMN ... DROP NOT NULL
    DROP CONSTRAINT <fk> / ADD CONSTRAINT <fk> ... ON DELETE SET NULL
    UPDATE ... (a bounded backfill of the two columns just added)

There is no DROP COLUMN, no SET NOT NULL, no ALTER TYPE, no CHECK constraint and
no index, and `tests/test_reembed.py` asserts that by reading this file's own
source rather than trusting this paragraph.

The three properties that make it safe with old and new code running together:

  * every added column is nullable with no default, so a table rewrite is not
    triggered and code that has never heard of the column keeps inserting
    successfully;
  * the ORM enumerates its columns explicitly, so a process running the
    previous image does not see the new columns at all;
  * the only constraint CHANGE relaxes a rule. CASCADE to SET NULL strictly
    REDUCES what a concurrent delete removes, so an old-image process that
    deletes an evaluation between the migration and its own restart destroys
    less than it did before, never more. Relaxations are the direction that is
    safe to apply first; a tightening would not be.

NO INDEX IS CREATED, DELIBERATELY. The re-embedding script walks each table in
full, batched by primary key, and filters on `embedding_model IS DISTINCT FROM
'voyage-context-4'` inside that walk. A sequential scan over a table the script
is going to read end to end anyway buys nothing, and `CREATE INDEX` takes a lock
that this migration has no reason to take.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0062_embedding_provenance"
down_revision: Union[str, None] = "0060_resume_provider_s3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: The model id every re-embedded vector is stamped with. Kept as a literal
#: here rather than imported from `app.config.llm_providers`, because a
#: migration must reproduce the same SQL a year from now even if the
#: application constant has moved on: a migration that changes meaning when the
#: code around it changes is not a migration.
VOYAGE_MODEL = "voyage-context-4"

#: Every vector column in the schema, with the provenance prefix its columns
#: take. `jobs` carries two independent vectors in two different spaces, so it
#: appears twice: `jobs.embedding` ranks candidates against a JD and
#: `jobs.reach_embedding` ranks roles against each other for AI Reach. They
#: were deliberately kept as separate columns and they need separate
#: provenance, or a re-embed of one would appear to vouch for the other.
VECTOR_COLUMNS: tuple[tuple[str, str, int], ...] = (
    ("profiles", "embedding", 1024),
    ("jobs", "embedding", 1024),
    ("jobs", "reach_embedding", 1024),
    ("context_chunks", "embedding", 1024),
)


def _provenance_columns(table: str, column: str, dim: int) -> list[str]:
    """The four columns one vector column needs, as ALTER TABLE fragments.

    `<col>_model` names the vendor model. `<col>_contract_version` is OUR
    version of how the text handed to that model was built -- the input type,
    the output dimension and the template. The vendor does not version a model
    id, so without this a change to what we embed would be invisible while the
    model string stayed identical, which is the same ambiguity one level up.

    `<col>_shadow` is where a re-embedding run writes. Writing straight into
    the live column would leave a half-embedded index serving two spaces the
    moment a run failed part way, which is precisely the state this whole
    migration exists to make impossible.
    """
    return [
        f"ADD COLUMN IF NOT EXISTS {column}_model varchar(64)",
        f"ADD COLUMN IF NOT EXISTS {column}_contract_version varchar(32)",
        f"ADD COLUMN IF NOT EXISTS {column}_generated_at timestamptz",
        f"ADD COLUMN IF NOT EXISTS {column}_shadow vector({dim})",
    ]


def _detach_fk(table: str, column: str, target: str, constraint: str) -> None:
    """Replace an inline ON DELETE CASCADE with a named ON DELETE SET NULL.

    The existing constraint was created by an inline REFERENCES clause in
    migration 0059, so Postgres named it and this migration cannot. It is
    looked up by (table, referenced table) rather than assumed, because a name
    guessed from a naming convention is a migration that fails on the one
    database where the convention was not followed.
    """
    bind = op.get_bind()
    existing = bind.execute(
        sa.text(
            "SELECT c.conname FROM pg_constraint c "
            "WHERE c.conrelid = CAST(:table AS regclass) AND c.contype = 'f' "
            "AND c.confrelid = CAST(:target AS regclass)"
        ),
        {"table": table, "target": target},
    ).scalars().all()
    for name in existing:
        op.execute(f'ALTER TABLE {table} DROP CONSTRAINT "{name}"')
    op.execute(
        f"ALTER TABLE {table} ADD CONSTRAINT {constraint} "
        f"FOREIGN KEY ({column}) REFERENCES {target}(id) ON DELETE SET NULL"
    )


def upgrade() -> None:
    # ── 1. Embedding provenance and the re-embed shadow ──────────────────────
    for table, column, dim in VECTOR_COLUMNS:
        clauses = ",\n            ".join(_provenance_columns(table, column, dim))
        op.execute(f"ALTER TABLE {table}\n            {clauses}")

    # Existing vectors are stamped with what they actually are, which is NOT
    # necessarily Voyage: any row written before the consolidation carries a
    # BGE-M3 vector and the two are not comparable. There is no way to tell
    # them apart retroactively, so nothing is guessed -- a NULL model on a
    # non-NULL vector is the honest record of "produced by an unrecorded
    # model", and it is exactly the predicate the re-embedding script selects
    # on. Backfilling them all to `voyage-context-4` would have looked tidier
    # and would have asserted something nobody can check.

    # ── 2. Human observations survive the purge of what they refer to ────────
    op.execute(
        """
        ALTER TABLE review_dispositions
            ADD COLUMN IF NOT EXISTS job_id uuid REFERENCES jobs(id) ON DELETE SET NULL,
            ADD COLUMN IF NOT EXISTS link_id uuid
                REFERENCES job_candidate_links(id) ON DELETE SET NULL,
            ADD COLUMN IF NOT EXISTS evaluation_ref uuid,
            ADD COLUMN IF NOT EXISTS detached_at timestamptz,
            ADD COLUMN IF NOT EXISTS detached_note text
        """
    )
    op.execute(
        """
        ALTER TABLE calibration_records
            ADD COLUMN IF NOT EXISTS evaluation_ref uuid,
            ADD COLUMN IF NOT EXISTS detached_at timestamptz,
            ADD COLUMN IF NOT EXISTS detached_note text
        """
    )

    # The backfill runs BEFORE the cascade is relaxed, while the evaluation is
    # still there to read the context out of. Doing it in the other order would
    # work today and lose the context of every row detached in between.
    op.execute(
        """
        UPDATE review_dispositions d
           SET job_id = e.job_id,
               link_id = e.link_id
          FROM evaluations e
         WHERE e.id = d.evaluation_id
           AND (d.job_id IS NULL OR d.link_id IS NULL)
        """
    )

    op.execute("ALTER TABLE review_dispositions ALTER COLUMN evaluation_id DROP NOT NULL")
    op.execute("ALTER TABLE calibration_records ALTER COLUMN evaluation_id DROP NOT NULL")
    _detach_fk(
        "review_dispositions",
        "evaluation_id",
        "evaluations",
        "fk_review_dispositions_evaluation",
    )
    _detach_fk(
        "calibration_records",
        "evaluation_id",
        "evaluations",
        "fk_calibration_records_evaluation",
    )


def downgrade() -> None:
    # The downgrade restores the CASCADE and the NOT NULL, which means it
    # cannot run while a detached row exists: a disposition whose
    # `evaluation_id` is NULL has no evaluation to point back at. That is
    # correct rather than inconvenient. Restoring the constraint would have to
    # either delete the human observation or invent a reference for it, and
    # both are worse than refusing.
    op.execute(
        "DELETE FROM review_dispositions WHERE evaluation_id IS NULL AND detached_at IS NOT NULL"
    )
    op.execute(
        "DELETE FROM calibration_records WHERE evaluation_id IS NULL AND detached_at IS NOT NULL"
    )
    for table, constraint in (
        ("review_dispositions", "fk_review_dispositions_evaluation"),
        ("calibration_records", "fk_calibration_records_evaluation"),
    ):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint}")
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {constraint} "
            f"FOREIGN KEY (evaluation_id) REFERENCES evaluations(id) ON DELETE CASCADE"
        )
        op.execute(f"ALTER TABLE {table} ALTER COLUMN evaluation_id SET NOT NULL")
    op.execute(
        "ALTER TABLE review_dispositions "
        "DROP COLUMN IF EXISTS job_id, "
        "DROP COLUMN IF EXISTS link_id, "
        "DROP COLUMN IF EXISTS evaluation_ref, "
        "DROP COLUMN IF EXISTS detached_at, "
        "DROP COLUMN IF EXISTS detached_note"
    )
    op.execute(
        "ALTER TABLE calibration_records "
        "DROP COLUMN IF EXISTS evaluation_ref, "
        "DROP COLUMN IF EXISTS detached_at, "
        "DROP COLUMN IF EXISTS detached_note"
    )
    for table, column, _dim in VECTOR_COLUMNS:
        op.execute(
            f"ALTER TABLE {table} "
            f"DROP COLUMN IF EXISTS {column}_model, "
            f"DROP COLUMN IF EXISTS {column}_contract_version, "
            f"DROP COLUMN IF EXISTS {column}_generated_at, "
            f"DROP COLUMN IF EXISTS {column}_shadow"
        )
