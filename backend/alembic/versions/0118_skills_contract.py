"""The skills contract: authored skills, the draft state, the immutable snapshot.

Revision ID: 0118_skills_contract
Revises: 0117_consent_versioning

The Tatva matrix becomes a list of SKILLS (Vivekium release, CONTRACT v1+v2).
`job_competencies` IS the skills table (one implementation per concept); this
migration gives it and its neighbours what the new contract needs, and turns
every existing matrix into that shape IN PLACE.

SCHEMA
------
* `job_competencies.authored_by` (`sutra` | `human`). The 2026-09-23 rule read
  "the human's entry" off an ABSENT `swot_origin`; a Sutra draft may carry no
  SWOT quotation (a JD-sourced skill) and must still not read as the team's.
* `jobs.assessment_context_json`, the hidden role summary Sutra writes at Save,
  and the Sutra draft state (`skills_draft_status`, `_error`, `_requested_at`,
  `skills_drafted_at`, `skills_drafted_swot_version`).
* `job_swot_analyses.status` admits `generating`, with `generation_requested_at`,
  because SWOT generation is dispatched rather than run in the request.
* `job_skill_snapshots`, INSERT-ONLY, and `assessment_conversations.
  skill_snapshot_id` + `contract_digest`, the binding of a started session to
  the exact contract it runs against.
* A partial UNIQUE index on `evidence_items` for one live answer evidence row
  per (tenant, application, message), which is what makes the per-answer
  ledger writer (`assessment_pipeline.evidence.record_answer_evidence`)
  idempotent under concurrency rather than only in sequence.

IMMUTABILITY IS THE DATABASE'S, TWICE. UPDATE is REVOKED from `pickready_app`
(0014's default privileges grant it to every new table, so a grant list that
merely omits it omits nothing), and `job_skill_snapshot_is_immutable` refuses
UPDATE for everybody and DELETE unless it is a CASCADE from the job or tenant
(`pg_trigger_depth() > 1`, the referential action is itself a trigger), so
tenant deletion and job purges keep working.

DATA, AND WHY NOTHING IS DELETED OR RE-INSERTED
-----------------------------------------------
`candidate_questions.competency_id` cascades from `job_competencies`, and
answers hang off the questions. Deleting and re-inserting a matrix would
delete issued questions and the answers to them. Every step below is an
UPDATE of an existing row or an INSERT of a new one.

1. `authored_by = 'sutra'` where the compiler left a trace (`swot_origin` or
   `provenance_json`); every other row stays `human`, the column default.
2. The draft state: a job with any skill row, active or not, is `drafted`.
3. Duplicate live answer evidence (a rescore wrote a second row per message
   before the writer was idempotent) is SUPERSEDED onto the earliest row,
   never deleted, then the unique index is built.
4. LOCKED JOBS: every job with a started assessment gets snapshot version 1
   (`source='migration'`) from its ACTIVE rows exactly as they stand, with a
   per-bucket priority from (force_rank, ordinal, name), an empty role summary
   (nobody wrote one, and inventing one would claim work that did not run),
   and `locked_at` = the first start. Every started session on the job is
   bound to it. The rows themselves are NOT touched: they are history.
5. UNLOCKED JOBS: `force_rank` is rewritten, in place, to the per-bucket
   priority 1..n (it was a job-wide rank across every scored item).
6. SAVED JOBS (`framework_approved_at` set) get `assessment_context_json =
   {"role_summary": "", "generated_by": "migration"}`, so every job that was
   ready for candidates STAYS ready. No invitation outage on deploy.
7. LIFECYCLE: a live job (`ratified_at` or `status = 'ratified'`) that is
   not archived becomes PUBLISHED, FIRST, whatever pre-publication state it
   was left in. NO PUBLISHED JOB IS EVER UNPUBLISHED: pilot has 31 live jobs
   with no saved skills and they stay live (CONTRACT v3); they cannot invite
   anybody until their skills are saved, which is today's state too. Then, on
   unpublished rows only, the retired approval states (SENT_TO_HIRING_MANAGER,
   IN_REVIEW) and NULL go back to DRAFT, and a DRAFT with saved skills becomes
   FINALIZED.
8. CREATOR ASSIGNMENTS: the creator of a job who is a Recruiter or a Hiring
   Manager of its tenant gets one active assignment in that role, unless the
   job already has an active holder. Supersedes 0061's refusal to backfill
   from `created_by` (see `backfill_creator_assignments`).
9. OVERFLOW IS REPORTED, NEVER TRUNCATED. A bucket holding more than five
   active skills is logged per job (locked or not) and summarised; nothing is
   removed. An unlocked job over the limit keeps working and is refused only
   at its next Save, with the count. `app.scripts.skills_overflow_report`
   prints the same list on demand.

NOT HERE, DELIBERATELY, AND OWNED BY THE PACKAGE THAT DELETES THE CODE:
tightening `ck_jobs_lifecycle_state` to six states and deleting the
`send_jd_to_hiring_manager` rows from `role_permissions`. Both are the second
half of a code change (the enum members, the approval routes and the
capability constant) that has not landed yet; doing the database half first
would let a live route write a value the CHECK refuses, and would fail
`test_capability_seed_parity` for a capability the code still grants. A
capability and its seeding are one change (claude.md rule 2), and so is its
removal.

The job_competencies trigger from 0042 nulls `jobs.reach_embedding` for every
job whose rows steps 1 and 5 touch. That is its documented rule (AI Reach
re-embeds a NULL on its next search), so it is left to fire.

The digest here is a LOCAL copy of `assessment_contract.compute_digest`,
deliberately: a migration must mean the same thing on the day it is replayed.
`tests/test_skills_contract_migration.py` pins the two as identical.

DOWNGRADE drops what this adds. Rewritten `force_rank` values, superseded
duplicate evidence, the lifecycle remap and the creator assignments are not
reversed: the first two were a representation change and a repair (restoring
the duplicates would reintroduce the defect), a remapped lifecycle is a state
the row's own columns already asserted, and an assignment is a live access
record a downgrade has no business revoking.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections import defaultdict
from typing import Any, Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0118_skills_contract"
down_revision = "0117_consent_versioning"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration.0118_skills_contract")

TENANT = "nullif(current_setting('app.tenant_id', true), '')::uuid"
BYPASS = "current_setting('app.bypass_rls', true) = 'on'"

#: Report order, identical to `assessment_contract.BUCKETS`.
BUCKETS: tuple[str, ...] = ("must_have", "nice_to_have", "behavioural")
MAX_PER_BUCKET = 5

_SNAPSHOT_IMMUTABILITY = """
CREATE OR REPLACE FUNCTION job_skill_snapshot_is_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION
            'job_skill_snapshots is insert-only: snapshot % of job % cannot be rewritten',
            OLD.version, OLD.job_id
            USING ERRCODE = 'restrict_violation';
    END IF;
    -- A DELETE is legal only as the cascade of the job or tenant it belongs
    -- to. The referential action runs as a trigger, so it arrives here one
    -- level deeper than a DELETE statement issued directly.
    IF pg_trigger_depth() <= 1 THEN
        RAISE EXCEPTION
            'job_skill_snapshots is insert-only: snapshot % of job % cannot be deleted',
            OLD.version, OLD.job_id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN OLD;
END;
$$
"""


# ── The canonical digest, a local copy of assessment_contract.compute_digest ─


def canonical_digest(skills: list[dict[str, Any]], role_summary: str, grade: str) -> str:
    """`skills` items carry id, name, bucket, priority, evidence_line."""
    ordered = sorted(
        skills,
        key=lambda s: (BUCKETS.index(s["bucket"]), int(s["priority"]), s["name"], str(s["id"])),
    )
    payload = {
        "grade": grade,
        "role_summary": role_summary,
        "skills": [
            {
                "bucket": s["bucket"],
                "evidence_line": s["evidence_line"],
                "id": str(s["id"]),
                "name": s["name"],
                "priority": int(s["priority"]),
            }
            for s in ordered
        ],
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


# ── Data steps. Module level so the migration test can run them on seeded rows;
#    every one is idempotent, so running it twice changes nothing. ───────────
#
# `job_ids` SCOPES a step to the named jobs, and exists for one caller: the
# migration test, which runs these steps on a shared test database and must
# touch only the rows it seeded. `upgrade()` passes None, which is every row.

def _scope(column: str, job_ids: Sequence[Any] | None) -> tuple[str, dict[str, Any]]:
    """`AND <column> = ANY(:job_ids)` for a scoped run, nothing for the real one."""
    if job_ids is None:
        return "", {}
    return (
        f" AND {column} = ANY(CAST(:job_ids AS uuid[]))",
        {"job_ids": [str(job_id) for job_id in job_ids]},
    )


def backfill_authored_by(
    connection: sa.engine.Connection, job_ids: Sequence[Any] | None = None
) -> int:
    scope, params = _scope("job_id", job_ids)
    return connection.execute(
        sa.text(
            f"""
            UPDATE job_competencies
               SET authored_by = 'sutra'
             WHERE authored_by <> 'sutra'
               AND (swot_origin IS NOT NULL OR provenance_json IS NOT NULL){scope}
            """
        ),
        params,
    ).rowcount


def backfill_draft_state(
    connection: sa.engine.Connection, job_ids: Sequence[Any] | None = None
) -> int:
    scope, params = _scope("j.id", job_ids)
    return connection.execute(
        sa.text(
            f"""
            UPDATE jobs j
               SET skills_draft_status = 'drafted',
                   skills_drafted_at = COALESCE(j.framework_generated_at, earliest.created_at),
                   skills_drafted_swot_version = swot.version
              FROM (SELECT job_id, MIN(created_at) AS created_at
                      FROM job_competencies GROUP BY job_id) AS earliest
              LEFT JOIN job_swot_analyses swot ON swot.job_id = earliest.job_id
             WHERE earliest.job_id = j.id
               AND j.skills_draft_status = 'not_started'{scope}
            """
        ),
        params,
    ).rowcount


def supersede_duplicate_answer_evidence(
    connection: sa.engine.Connection, job_ids: Sequence[Any] | None = None
) -> int:
    """Keep the EARLIEST live answer evidence per message; supersede the rest
    onto it. The claim links of a superseded row stay, and a superseded item is
    not counted as live evidence by any reader."""
    scope, params = _scope("job_id", job_ids)
    return connection.execute(
        sa.text(
            f"""
            WITH ranked AS (
                SELECT id,
                       first_value(id) OVER w AS keeper,
                       row_number() OVER w AS n
                  FROM evidence_items
                 WHERE source_type = 'answer'
                   AND status = 'active'
                   AND link_id IS NOT NULL{scope}
                WINDOW w AS (PARTITION BY tenant_id, link_id, source_id
                             ORDER BY created_at, id)
            )
            UPDATE evidence_items e
               SET status = 'superseded', superseded_by = ranked.keeper, updated_at = now()
              FROM ranked
             WHERE e.id = ranked.id AND ranked.n > 1
            """
        ),
        params,
    ).rowcount


def _active_skills(connection: sa.engine.Connection, job_id: Any) -> list[dict[str, Any]]:
    rows = connection.execute(
        sa.text(
            """
            SELECT id, name, category, observable_evidence
              FROM job_competencies
             WHERE job_id = :job_id AND is_active
             ORDER BY category, force_rank ASC NULLS LAST, ordinal, name, id
            """
        ),
        {"job_id": job_id},
    ).mappings().all()
    positions: dict[str, int] = defaultdict(int)
    skills: list[dict[str, Any]] = []
    for row in rows:
        positions[row["category"]] += 1
        skills.append(
            {
                "id": str(row["id"]),
                "name": row["name"],
                "bucket": row["category"],
                "priority": positions[row["category"]],
                "evidence_line": row["observable_evidence"] or "",
            }
        )
    skills.sort(
        key=lambda s: (BUCKETS.index(s["bucket"]), s["priority"], s["name"], s["id"])
    )
    return skills


def snapshot_locked_jobs(
    connection: sa.engine.Connection, job_ids: Sequence[Any] | None = None
) -> list[Any]:
    """Version 1 for every job with a started assessment and no snapshot yet."""
    scope, params = _scope("j.id", job_ids)
    jobs = connection.execute(
        sa.text(
            f"""
            SELECT j.id, j.tenant_id, j.assessment_grade,
                   (SELECT MIN(c.started_at) FROM assessment_conversations c
                     WHERE c.job_id = j.id AND c.started_at IS NOT NULL) AS first_start
              FROM jobs j
             WHERE EXISTS (SELECT 1 FROM assessment_conversations c
                            WHERE c.job_id = j.id AND c.started_at IS NOT NULL)
               AND NOT EXISTS (SELECT 1 FROM job_skill_snapshots s WHERE s.job_id = j.id){scope}
             ORDER BY j.id
            """
        ),
        params,
    ).mappings().all()
    locked: list[Any] = []
    for job in jobs:
        skills = _active_skills(connection, job["id"])
        digest = canonical_digest(skills, "", job["assessment_grade"])
        snapshot_id = uuid.uuid4()
        connection.execute(
            sa.text(
                """
                INSERT INTO job_skill_snapshots (
                    id, tenant_id, job_id, version, skills_json, role_summary,
                    context_json, grade, digest, source,
                    locked_by_conversation_id, locked_at
                ) VALUES (
                    :id, :tenant_id, :job_id, 1, CAST(:skills AS jsonb), '',
                    CAST(:context AS jsonb), :grade, :digest, 'migration',
                    NULL, :locked_at
                )
                """
            ),
            {
                "id": snapshot_id,
                "tenant_id": job["tenant_id"],
                "job_id": job["id"],
                "skills": json.dumps(skills, ensure_ascii=False),
                "context": json.dumps({"generated_by": "migration"}),
                "grade": job["assessment_grade"],
                "digest": digest,
                "locked_at": job["first_start"],
            },
        )
        bound = connection.execute(
            sa.text(
                """
                UPDATE assessment_conversations
                   SET skill_snapshot_id = :snapshot_id, contract_digest = :digest
                 WHERE job_id = :job_id AND started_at IS NOT NULL
                   AND skill_snapshot_id IS NULL
                """
            ),
            {"snapshot_id": snapshot_id, "digest": digest, "job_id": job["id"]},
        ).rowcount
        logger.info(
            "0118 locked job_id=%s skills=%d conversations_bound=%d contract_digest=%s",
            job["id"], len(skills), bound, digest,
        )
        locked.append(job["id"])
    return locked


def rank_unlocked_skills(
    connection: sa.engine.Connection, job_ids: Sequence[Any] | None = None
) -> int:
    """`force_rank` becomes the per-bucket priority on jobs nobody has started."""
    scope, params = _scope("job_id", job_ids)
    return connection.execute(
        sa.text(
            f"""
            UPDATE job_competencies jc
               SET force_rank = ranked.priority
              FROM (
                    SELECT id,
                           row_number() OVER (
                               PARTITION BY job_id, category
                               ORDER BY force_rank ASC NULLS LAST, ordinal, name, id
                           ) AS priority
                      FROM job_competencies
                     WHERE is_active
                       AND job_id NOT IN (SELECT job_id FROM job_skill_snapshots){scope}
                   ) AS ranked
             WHERE jc.id = ranked.id
               AND jc.force_rank IS DISTINCT FROM ranked.priority
            """
        ),
        params,
    ).rowcount


def stamp_saved_context(
    connection: sa.engine.Connection, job_ids: Sequence[Any] | None = None
) -> int:
    scope, params = _scope("id", job_ids)
    return connection.execute(
        sa.text(
            f"""
            UPDATE jobs
               SET assessment_context_json =
                   CAST('{{"role_summary": "", "generated_by": "migration"}}' AS jsonb)
             WHERE framework_approved_at IS NOT NULL
               AND assessment_context_json IS NULL{scope}
            """
        ),
        params,
    ).rowcount


#: Every pre-publication state a PUBLISHED job may be sitting in. A job
#: published through Create Job's direct publish kept `DRAFT`, because only the
#: separate publish route wrote the lifecycle; on pilot that is every live job.
_PRE_PUBLICATION_STATES = ("DRAFT", "SENT_TO_HIRING_MANAGER", "IN_REVIEW", "FINALIZED")
#: The two approval-chain states the skills flow retires. Nothing writes them
#: once the chain's routes are deleted, so a job still in one goes back to DRAFT.
_RETIRED_STATES = ("SENT_TO_HIRING_MANAGER", "IN_REVIEW")
#: A row the product treats as live: stamped by publication, either field.
_IS_PUBLISHED = "(ratified_at IS NOT NULL OR status::text = 'ratified')"


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def remap_lifecycle(
    connection: sa.engine.Connection, job_ids: Sequence[Any] | None = None
) -> dict[str, int]:
    """Bring `jobs.lifecycle_state` in line with what each row already records.

    PUBLISHED FIRST, AND IT ONLY EVER MOVES A JOB FORWARD. A job that is live
    (`ratified_at` set, or `status = 'ratified'`) and not archived is
    PUBLISHED, whatever pre-publication state it was left in. Running the
    retired-state step first would send a live job back to DRAFT, which is an
    unpublish nobody asked for; pilot's 31 live jobs have no saved skills and
    MUST stay live (CONTRACT v3).

    Then, on UNPUBLISHED rows only, the retired approval states and NULL go
    back to DRAFT (RBAC refuses every state-gated capability on NULL), and a
    DRAFT whose skills were already saved becomes FINALIZED, which is what
    Save Skills now writes.
    """
    scope, params = _scope("id", job_ids)
    published = connection.execute(
        sa.text(
            f"""
            UPDATE jobs
               SET lifecycle_state = 'PUBLISHED'
             WHERE archived_at IS NULL
               AND {_IS_PUBLISHED}
               AND (lifecycle_state IS NULL
                    OR lifecycle_state IN ({_quoted(_PRE_PUBLICATION_STATES)})){scope}
            """
        ),
        params,
    ).rowcount
    unpublished = f"archived_at IS NULL AND NOT {_IS_PUBLISHED}"
    drafted = connection.execute(
        sa.text(
            f"""
            UPDATE jobs
               SET lifecycle_state = 'DRAFT'
             WHERE {unpublished}
               AND (lifecycle_state IS NULL
                    OR lifecycle_state IN ({_quoted(_RETIRED_STATES)})){scope}
            """
        ),
        params,
    ).rowcount
    finalized = connection.execute(
        sa.text(
            f"""
            UPDATE jobs
               SET lifecycle_state = 'FINALIZED'
             WHERE {unpublished}
               AND framework_approved_at IS NOT NULL
               AND lifecycle_state = 'DRAFT'{scope}
            """
        ),
        params,
    ).rowcount
    return {"published": published, "drafted": drafted, "finalized": finalized}


def backfill_creator_assignments(
    connection: sa.engine.Connection, job_ids: Sequence[Any] | None = None
) -> int:
    """One active assignment for a job's creator, when the creator IS a
    Recruiter or a Hiring Manager of that job's tenant.

    SUPERSEDES 0061's decision not to backfill from `created_by`. That
    decision was right while nothing read the SCOPED permission cells; the
    skills flow reads them (a Hiring Manager edits skills on a job assigned to
    them, a Recruiter publishes one), and with the table empty the creator of
    a job could no longer finish it. The rule stays narrow: only the creator,
    only in their own role, never a disabled account, and never beside an
    active holder of that assignment (the partial unique indexes from 0061
    would refuse a second one anyway).
    """
    scope, params = _scope("j.id", job_ids)
    return connection.execute(
        sa.text(
            f"""
            INSERT INTO job_assignments (tenant_id, job_id, user_id, assignment_role, active)
            SELECT j.tenant_id, j.id, u.id, u.role::text, true
              FROM jobs j
              JOIN users u ON u.id = j.created_by AND u.tenant_id = j.tenant_id
             WHERE u.role::text IN ('recruiter', 'hiring_manager')
               AND u.status::text <> 'disabled'
               AND NOT EXISTS (
                    SELECT 1 FROM job_assignments a
                     WHERE a.job_id = j.id
                       AND a.assignment_role = u.role::text
                       AND a.active
               ){scope}
            """
        ),
        params,
    ).rowcount


#: Kept textually in step with `app.scripts.skills_overflow_report`, which
#: prints the same list on demand; the migration test compares the two.
OVERFLOW_SQL = """
    SELECT jc.job_id, j.tenant_id, jc.category, COUNT(*) AS active,
           EXISTS (SELECT 1 FROM job_skill_snapshots s WHERE s.job_id = jc.job_id)
               AS locked
      FROM job_competencies jc
      JOIN jobs j ON j.id = jc.job_id
     WHERE jc.is_active{scope}
     GROUP BY jc.job_id, j.tenant_id, jc.category
    HAVING COUNT(*) > :limit
     ORDER BY jc.job_id, jc.category
"""


def report_overflow(
    connection: sa.engine.Connection, job_ids: Sequence[Any] | None = None
) -> list[dict[str, Any]]:
    scope, params = _scope("jc.job_id", job_ids)
    rows = [
        dict(row)
        for row in connection.execute(
            sa.text(OVERFLOW_SQL.format(scope=scope)), {**params, "limit": MAX_PER_BUCKET}
        ).mappings().all()
    ]
    for row in rows:
        logger.warning(
            "0118 skills_over_limit job_id=%s tenant_id=%s bucket=%s active=%d "
            "limit=%d locked=%s (reported, not truncated)",
            row["job_id"], row["tenant_id"], row["category"], row["active"],
            MAX_PER_BUCKET, row["locked"],
        )
    logger.info(
        "0118 skills_over_limit summary buckets=%d jobs=%d locked_jobs=%d",
        len(rows),
        len({row["job_id"] for row in rows}),
        len({row["job_id"] for row in rows if row["locked"]}),
    )
    return rows


def run_data_steps(
    connection: sa.engine.Connection, job_ids: Sequence[Any] | None = None
) -> dict[str, Any]:
    """Every data step, in order. Returned counts are for the log and the test.

    The order is load-bearing twice: the snapshot is taken BEFORE unlocked
    skills are re-ranked (a locked job's rows are history and are never
    rewritten), and the saved-context stamp runs BEFORE the lifecycle remap,
    which reads `framework_approved_at` to decide FINALIZED.
    """
    counts: dict[str, Any] = {
        "authored_by_sutra": backfill_authored_by(connection, job_ids),
        "drafted_jobs": backfill_draft_state(connection, job_ids),
        "superseded_answer_evidence": supersede_duplicate_answer_evidence(connection, job_ids),
        "locked_jobs": snapshot_locked_jobs(connection, job_ids),
        "reranked_skills": rank_unlocked_skills(connection, job_ids),
        "saved_context_stamped": stamp_saved_context(connection, job_ids),
        "lifecycle": remap_lifecycle(connection, job_ids),
        "creator_assignments": backfill_creator_assignments(connection, job_ids),
        "over_limit": report_overflow(connection, job_ids),
    }
    logger.info(
        "0118 data authored_by_sutra=%d drafted_jobs=%d superseded_answer_evidence=%d "
        "locked_jobs=%d reranked_skills=%d saved_context_stamped=%d "
        "lifecycle_published=%d lifecycle_drafted=%d lifecycle_finalized=%d "
        "creator_assignments=%d over_limit_buckets=%d",
        counts["authored_by_sutra"], counts["drafted_jobs"],
        counts["superseded_answer_evidence"], len(counts["locked_jobs"]),
        counts["reranked_skills"], counts["saved_context_stamped"],
        counts["lifecycle"]["published"], counts["lifecycle"]["drafted"],
        counts["lifecycle"]["finalized"], counts["creator_assignments"],
        len(counts["over_limit"]),
    )
    return counts


def upgrade() -> None:
    # ── job_competencies.authored_by ─────────────────────────────────────────
    op.add_column(
        "job_competencies",
        sa.Column("authored_by", sa.String(10), nullable=False, server_default="human"),
    )
    op.create_check_constraint(
        "ck_job_competencies_authored_by",
        "job_competencies",
        "authored_by IN ('sutra', 'human')",
    )

    # ── jobs: hidden context and the draft state ─────────────────────────────
    op.add_column("jobs", sa.Column("assessment_context_json", postgresql.JSONB()))
    op.add_column(
        "jobs",
        sa.Column(
            "skills_draft_status", sa.String(20), nullable=False,
            server_default="not_started",
        ),
    )
    op.create_check_constraint(
        "ck_jobs_skills_draft_status",
        "jobs",
        "skills_draft_status IN ('not_started', 'drafting', 'drafted', 'failed')",
    )
    op.add_column("jobs", sa.Column("skills_draft_error", sa.Text()))
    op.add_column(
        "jobs", sa.Column("skills_draft_requested_at", sa.DateTime(timezone=True))
    )
    op.add_column("jobs", sa.Column("skills_drafted_at", sa.DateTime(timezone=True)))
    op.add_column("jobs", sa.Column("skills_drafted_swot_version", sa.Integer()))

    # ── job_swot_analyses: generation is dispatched ──────────────────────────
    op.drop_constraint(
        "ck_job_swot_analyses_status_vocabulary", "job_swot_analyses", type_="check"
    )
    op.create_check_constraint(
        "ck_job_swot_analyses_status_vocabulary",
        "job_swot_analyses",
        "status IN ('not_generated', 'generating', 'generated', 'failed', 'edited')",
    )
    op.add_column(
        "job_swot_analyses",
        sa.Column("generation_requested_at", sa.DateTime(timezone=True)),
    )

    # ── job_skill_snapshots ──────────────────────────────────────────────────
    op.create_table(
        "job_skill_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "job_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("skills_json", postgresql.JSONB(), nullable=False),
        sa.Column("role_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "context_json", postgresql.JSONB(), nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("grade", sa.String(40), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("source", sa.String(10), nullable=False),
        sa.Column("locked_by_conversation_id", postgresql.UUID(as_uuid=True)),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("job_id", "version", name="uq_job_skill_snapshots_version"),
        sa.CheckConstraint(
            "source IN ('lock', 'migration')", name="ck_job_skill_snapshots_source"
        ),
        sa.CheckConstraint("version >= 1", name="ck_job_skill_snapshots_version"),
        sa.CheckConstraint("length(digest) = 64", name="ck_job_skill_snapshots_digest"),
    )
    op.create_index("ix_job_skill_snapshots_tenant", "job_skill_snapshots", ["tenant_id"])
    op.create_index("ix_job_skill_snapshots_job", "job_skill_snapshots", ["job_id"])
    op.execute(_SNAPSHOT_IMMUTABILITY)
    op.execute(
        """
        CREATE TRIGGER trg_job_skill_snapshots_immutable
        BEFORE UPDATE OR DELETE ON job_skill_snapshots
        FOR EACH ROW EXECUTE FUNCTION job_skill_snapshot_is_immutable()
        """
    )
    op.execute("GRANT SELECT, INSERT, DELETE ON job_skill_snapshots TO pickready_app")
    # 0014's ALTER DEFAULT PRIVILEGES granted UPDATE the moment the table was
    # created, so the revoke is what makes it insert-only for the app role.
    op.execute("REVOKE UPDATE ON job_skill_snapshots FROM pickready_app")
    op.execute("ALTER TABLE job_skill_snapshots ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE job_skill_snapshots FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY job_skill_snapshots_tenant_isolation ON job_skill_snapshots "
        f"USING ((tenant_id = {TENANT}) OR ({BYPASS})) "
        f"WITH CHECK ((tenant_id = {TENANT}) OR ({BYPASS}))"
    )

    # ── assessment_conversations: the binding ────────────────────────────────
    op.add_column(
        "assessment_conversations",
        sa.Column(
            "skill_snapshot_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("job_skill_snapshots.id", ondelete="SET NULL"),
        ),
    )
    op.add_column(
        "assessment_conversations", sa.Column("contract_digest", sa.String(64))
    )
    op.create_index(
        "ix_assessment_conversations_skill_snapshot",
        "assessment_conversations",
        ["skill_snapshot_id"],
    )

    # ── Data, in place ───────────────────────────────────────────────────────
    run_data_steps(op.get_bind())

    # ── One live answer evidence row per message, AFTER the dedupe ───────────
    op.execute(
        "CREATE UNIQUE INDEX ux_evidence_items_live_answer ON evidence_items "
        "(tenant_id, link_id, source_id) "
        "WHERE source_type = 'answer' AND status = 'active' AND link_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ux_evidence_items_live_answer")

    op.drop_index(
        "ix_assessment_conversations_skill_snapshot",
        table_name="assessment_conversations",
    )
    op.drop_column("assessment_conversations", "contract_digest")
    op.drop_column("assessment_conversations", "skill_snapshot_id")

    op.execute(
        "DROP POLICY IF EXISTS job_skill_snapshots_tenant_isolation ON job_skill_snapshots"
    )
    op.execute("DROP TRIGGER IF EXISTS trg_job_skill_snapshots_immutable ON job_skill_snapshots")
    op.execute("DROP FUNCTION IF EXISTS job_skill_snapshot_is_immutable()")
    op.drop_index("ix_job_skill_snapshots_job", table_name="job_skill_snapshots")
    op.drop_index("ix_job_skill_snapshots_tenant", table_name="job_skill_snapshots")
    op.drop_table("job_skill_snapshots")

    # A row still generating has no worker coming for it after a downgrade;
    # it reads as the failed generation it now is.
    op.execute(
        "UPDATE job_swot_analyses SET status = 'failed', "
        "generation_error = COALESCE(generation_error, 'Generation did not finish.') "
        "WHERE status = 'generating'"
    )
    op.drop_column("job_swot_analyses", "generation_requested_at")
    op.drop_constraint(
        "ck_job_swot_analyses_status_vocabulary", "job_swot_analyses", type_="check"
    )
    op.create_check_constraint(
        "ck_job_swot_analyses_status_vocabulary",
        "job_swot_analyses",
        "status IN ('not_generated', 'generated', 'failed', 'edited')",
    )

    op.drop_column("jobs", "skills_drafted_swot_version")
    op.drop_column("jobs", "skills_drafted_at")
    op.drop_column("jobs", "skills_draft_requested_at")
    op.drop_column("jobs", "skills_draft_error")
    op.drop_constraint("ck_jobs_skills_draft_status", "jobs", type_="check")
    op.drop_column("jobs", "skills_draft_status")
    op.drop_column("jobs", "assessment_context_json")

    op.drop_constraint("ck_job_competencies_authored_by", "job_competencies", type_="check")
    op.drop_column("job_competencies", "authored_by")
