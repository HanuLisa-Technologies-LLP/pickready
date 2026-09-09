#!/usr/bin/env python
"""Measure the AI programme's baseline against a real database, not the tree.

    python -m app.scripts.ai_baseline

RPN-AI-UP-001 W0 asks for `docs/verification/AI_UPGRADE_BASELINE.md` to carry
"the live row counts from the pilot database, not from a local seed", and every
later claim of improvement in that programme is measured against that file.
Nothing in this repository could produce those numbers: the data subnets have
no route to the internet in either direction, so there is no psql from a
laptop, and the only thing that had ever run inside that boundary was
`alembic upgrade head`.

So this is the read side of `scripts/run-migration.sh`, and `scripts/pilot-
baseline.sh` is its runner. It runs as a one-shot task on the `migrate` task
definition, which holds ONE secret, the DSN, and nothing else -- a probe that
could read the model credential would be a probe with more reach than its work
needs.

WHY A MODULE AND NOT AN INLINE `python -c`
------------------------------------------
The first measurement WAS an inline `python -c`, and it got two things wrong
that a reviewed module does not. It ran every query on one connection inside
one implicit transaction, so the first missing table (`evidence_records`, a
name that does not exist here; the ledger is `evidence_items`) aborted the
transaction and every subsequent query reported "current transaction is
aborted" -- eleven real numbers replaced by the same misleading error. And a
command that lives only in a shell history cannot be re-run to compare against,
which is the entire point of a baseline.

Each probe below therefore runs in its OWN connection and its own error
boundary, so one wrong table name costs one row of the report.

WHY IT READS UNDER THE BYPASS FLAG
----------------------------------
`context_chunks`, `profiles` and the rest are FORCE ROW LEVEL SECURITY, and the
policies key off `current_setting('app.tenant_id')`. A plain connection with no
GUC set does not error, it returns ZERO -- which is indistinguishable from an
empty table and is exactly the silent-zero failure this codebase has a written
rule about. So the probe sets the same sentinel tenant and the same explicit
`app.bypass_rls='on'` flag `core.db.superadmin_scope` sets, and for the same
reason: the flag is the audited escape hatch, not the connection's privilege.

IT IS READ ONLY BY CONSTRUCTION. Every statement is a literal in `PROBES`
below. No caller-supplied value reaches SQL, and there is no argument that
could carry one.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

#: The sentinel `superadmin_scope` pins so the policies' `::uuid` cast stays
#: well defined. A valid uuid no uuid4 tenant can hold.
_SENTINEL_TENANT = "00000000-0000-0000-0000-000000000000"

#: (key, sql). Literals only. Ordered so the report reads as a narrative:
#: platform, then tenancy, then the three things the AI programme is about --
#: whether embeddings exist, whether the retrieval index exists, and whether
#: the assessment framework has ever produced a row.
PROBES: tuple[tuple[str, str], ...] = (
    ("schema_version", "SELECT version_num FROM alembic_version"),
    ("postgres_version", "SHOW server_version"),
    # W2.3 needs pgvector 0.8.0 or later for `hnsw.iterative_scan`. This is the
    # confirmation that section asks for by name rather than an assumption read
    # off the RDS release notes.
    ("pgvector_version", "SELECT extversion FROM pg_extension WHERE extname = 'vector'"),
    ("tenants_total", "SELECT count(*) FROM tenants"),
    ("tenants_active", "SELECT count(*) FROM tenants WHERE status = 'active'"),
    ("tenants_demo", "SELECT count(*) FROM tenants WHERE is_demo"),
    ("jobs_total", "SELECT count(*) FROM jobs"),
    ("jobs_published", "SELECT count(*) FROM jobs WHERE posting_start_date IS NOT NULL"),
    ("jobs_with_embedding", "SELECT count(*) FROM jobs WHERE embedding IS NOT NULL"),
    ("candidates_total", "SELECT count(*) FROM candidates"),
    ("profiles_total", "SELECT count(*) FROM profiles"),
    ("profiles_with_embedding", "SELECT count(*) FROM profiles WHERE embedding IS NOT NULL"),
    ("applications_total", "SELECT count(*) FROM job_candidate_links"),
    # The numbers this programme moves. All of them are expected to be zero on
    # the day the baseline is taken, and a later run showing them still zero is
    # the finding, not the absence of one.
    ("context_chunks_total", "SELECT count(*) FROM context_chunks"),
    ("context_chunks_sources", "SELECT count(DISTINCT source_id) FROM context_chunks"),
    (
        "context_chunks_embedded",
        "SELECT count(*) FROM context_chunks WHERE embedding IS NOT NULL",
    ),
    ("reports_total", "SELECT count(*) FROM functional_skills_reports"),
    ("evaluations_total", "SELECT count(*) FROM evaluations"),
    ("evidence_items_total", "SELECT count(*) FROM evidence_items"),
    ("evidence_claims_total", "SELECT count(*) FROM evidence_claims"),
    ("agent_traces_total", "SELECT count(*) FROM agent_execution_traces"),
    ("agent_learnings_total", "SELECT count(*) FROM agent_learnings"),
    ("job_competencies_total", "SELECT count(*) FROM job_competencies"),
    ("scorecard_bindings_total", "SELECT count(*) FROM job_scorecard_bindings"),
)


def _async_dsn(url: str) -> str:
    """asyncpg, whatever the secret says.

    The DSN is shared with Alembic, which wants a sync driver, so the scheme is
    normalised here rather than a second secret being minted for one caller."""
    if url.startswith("postgresql+asyncpg://"):
        return url
    for sync_scheme in ("postgresql+psycopg://", "postgresql+psycopg2://"):
        if url.startswith(sync_scheme):
            return "postgresql+asyncpg://" + url.split("://", 1)[1]
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


async def collect() -> dict[str, object]:
    """Run every probe, each on its own connection, and return the answers.

    A failure is RECORDED as the answer for that key and never raised. This is
    a measurement, and a measurement that stops at the first surprise measures
    less than one that finishes."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("ai_baseline: DATABASE_URL is not set. Nothing to measure.")

    engine = create_async_engine(_async_dsn(dsn), pool_pre_ping=True)
    results: dict[str, object] = {}
    try:
        for key, statement in PROBES:
            # A fresh connection per probe. Cheap against a pooled engine, and
            # it means an aborted transaction cannot reach the next probe --
            # the exact defect the inline version had.
            try:
                async with engine.connect() as conn:
                    await conn.execute(
                        text("SELECT set_config('app.tenant_id', :t, true)"),
                        {"t": _SENTINEL_TENANT},
                    )
                    await conn.execute(
                        text("SELECT set_config('app.bypass_rls', 'on', true)")
                    )
                    results[key] = (await conn.execute(text(statement))).scalar()
            except Exception as exc:  # noqa: BLE001 - a probe reports, it never stops
                first_line = f"ERROR {type(exc).__name__}: {exc}".split("\n")[0]
                results[key] = first_line
    finally:
        await engine.dispose()
    return results


def main() -> int:
    results = asyncio.run(collect())
    # Delimited so a caller can lift the JSON out of a CloudWatch log stream
    # that also carries the container's own startup lines.
    print("AI_BASELINE_JSON_START")
    print(json.dumps(results, indent=2, default=str, sort_keys=False))
    print("AI_BASELINE_JSON_END")
    failed = [k for k, v in results.items() if isinstance(v, str) and v.startswith("ERROR ")]
    if failed:
        print(
            f"ai_baseline: {len(failed)} probe(s) failed: {', '.join(failed)}",
            file=sys.stderr,
        )
    # A failed probe is a reported row, not a failed run. The exit code answers
    # "did the measurement happen", because a non-zero here would stop a
    # pipeline over a table some future migration has not created yet.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
