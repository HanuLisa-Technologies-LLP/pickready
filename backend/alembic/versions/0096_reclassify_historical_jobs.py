"""Re-run STEM classification over historical jobs (2026-09-13 spec, section 20).

Revision ID: 0096_reclassify_historical_jobs
Revises: 0095_bgv_and_conversations

WHAT WAS WRONG WITH THE ROWS THIS MIGRATION REWRITES
-----------------------------------------------------
The classification engine read the job description BODY and almost nothing
else. A title carried at most 0.30 of the 0.50 a job needed to be STEM, and
several STEM titles carried nothing at all, so every job whose description was
thin was stored as Non-STEM regardless of what the job actually was:

    Software Engineer      0.30  ->  NON_STEM
    Data Scientist         0.30  ->  NON_STEM
    Electronics Engineer   0.30  ->  NON_STEM
    Research Scientist     0.00  ->  NON_STEM
    Cloud Engineer         0.00  ->  NON_STEM

`services/stem_classification` now reads the occupational function out of the
title as a second layer. That fixes every job created from here on. It does
nothing at all for the rows already written, and the spec is explicit that
leaving them is not acceptable: "Do not leave existing historical data
inconsistent with newly created data."

WHY THE ENGINE IS IMPORTED HERE, AGAINST THIS DIRECTORY'S USUAL RULE
---------------------------------------------------------------------
Seed migrations in this repository write string literals rather than importing
constants, so a historical migration's effect cannot shift under a later
rename. That rule protects a migration whose OUTPUT is the data. This one's
output is a FUNCTION of the data, and the function is three hundred lines of
regex: transcribing it would create the second implementation claude.md rule 5
forbids, and the copy would be the one nobody tested. So the engine is
imported, and the mitigation is that this migration runs once: a database that
has it stamped never re-runs it, whatever the engine does later.

WHICH ROWS ARE LEFT ALONE, AND WHY EACH EXCLUSION IS LOAD-BEARING
-------------------------------------------------------------------
  * `classification_locked` rows are skipped. The lock is stamped by the
    completion charge (`services/credit_reconciliation`), so a locked job has
    already had a report billed against the rate it carried. Part 3 section 8
    says a locked classification is compensated with a credit adjustment and
    never rewritten, and rewriting one here would leave the ledger stating a
    rate the job no longer claims.
  * `classification_overridden` rows are skipped. A Provider admin looked at
    the row and decided. An engine improvement does not outrank a human who
    has already ruled on the same job.
  * A row whose recomputed label equals its stored label is not written at
    all, so `updated_at` stays honest about what changed.

RLS IS NOT IN THE WAY, AND IT IS WORTH SAYING WHY
---------------------------------------------------
`jobs` carries FORCE row-level security, so a bare UPDATE from a non-superuser
role would match zero rows and report success. `alembic/env.py` sets
`app.bypass_rls = 'on'` on the migration connection for exactly this reason,
and its comment records the two migrations that silently backfilled nothing
before it did. This migration relies on that and adds no escape hatch of its
own.

Downgrade cannot restore the previous labels (they were not snapshotted, and
snapshotting a column purely to un-fix it would be worse), so it is a no-op.
The forward direction is idempotent: running it twice computes the same
labels from the same text.
"""
import json

from alembic import op
import sqlalchemy as sa

revision = "0096_reclassify_historical_jobs"
down_revision = "0095_bgv_and_conversations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.services.stem_classification import classify_safe, credit_cost

    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            """
            SELECT id, title, raw_jd_text, jd_markdown, role_classification
            FROM jobs
            WHERE classification_locked = FALSE
              AND classification_overridden = FALSE
            """
        )
    ).mappings().all()

    for row in rows:
        # Part 3 Rule 3 classifies the RAW pre-edit JD. Historical rows that
        # predate that column have only the edited document, which is the best
        # evidence that exists for them; a row with neither is classified on
        # its title alone, which is now enough to be right about.
        document = row["raw_jd_text"] or row["jd_markdown"] or ""
        result = classify_safe(document, row["title"] or "")
        if result.classification == row["role_classification"]:
            continue
        connection.execute(
            sa.text(
                """
                UPDATE jobs
                SET role_classification = :label,
                    classification_confidence = :confidence,
                    classification_signals = CAST(:signals AS jsonb),
                    classification_tentative = :tentative,
                    credit_cost_per_report = :cost
                WHERE id = :job_id
                """
            ),
            {
                "job_id": row["id"],
                "label": result.classification,
                "confidence": result.confidence,
                "signals": json.dumps(result.explanation),
                "tentative": bool(result.tentative or result.engine_error),
                "cost": credit_cost(result.classification),
            },
        )


def downgrade() -> None:
    """No-op. The previous labels were wrong and were not snapshotted."""
