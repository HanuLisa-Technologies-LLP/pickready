"""TEMPORARY (Phase 2 WP-C): the Yukti columns until WP-B's migration lands.

WP-C (the ranked table, `yukti.ranking`, `yukti.projection`) was built in
parallel with WP-B, which owns the migration `phase2_yukti` and the model
attributes (PLAN-p2 section 3.10). The ranked query reads those columns, so on
the WP-C branch alone every test that reaches the ranked route would fail with
UndefinedColumnError for a reason that is not a defect in WP-C.

This module adds EXACTLY the PLAN-p2 columns to the test database with
`ADD COLUMN IF NOT EXISTS`, and maps them onto the ORM classes only when the
class does not already map them. Both halves are therefore INERT once WP-B's
migration and models are merged: the columns exist, the attributes exist, and
nothing here changes anything. The orchestrator deletes this file and its one
call in `conftest.py` at integration; leaving it for a run is harmless, which
is the point of making it idempotent rather than clever.

It is test support only. Nothing under `app/` imports it.
"""
from __future__ import annotations

import asyncio
import os

#: PLAN-p2 section 3.10, verbatim in shape: the seven link columns, the
#: tenant's assessment weight, and the report's Must-have flag.
DDL: tuple[str, ...] = (
    "ALTER TABLE job_candidate_links ADD COLUMN IF NOT EXISTS yukti_pre_score REAL NULL "
    "CHECK (yukti_pre_score BETWEEN 0 AND 100)",
    "ALTER TABLE job_candidate_links ADD COLUMN IF NOT EXISTS yukti_status VARCHAR(20) "
    "NOT NULL DEFAULT 'pending' "
    "CHECK (yukti_status IN ('pending','scored','not_assessed','legacy'))",
    "ALTER TABLE job_candidate_links ADD COLUMN IF NOT EXISTS yukti_failure_reason VARCHAR(40) NULL",
    "ALTER TABLE job_candidate_links ADD COLUMN IF NOT EXISTS evidence_tags_json JSONB "
    "NOT NULL DEFAULT '[]'::jsonb",
    "ALTER TABLE job_candidate_links ADD COLUMN IF NOT EXISTS yukti_provenance_json JSONB NULL",
    "ALTER TABLE job_candidate_links ADD COLUMN IF NOT EXISTS yukti_scored_at TIMESTAMPTZ NULL",
    "ALTER TABLE job_candidate_links ADD COLUMN IF NOT EXISTS yukti_profile_id UUID NULL "
    "REFERENCES profiles(id) ON DELETE SET NULL",
    "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS yukti_assessment_weight_pct SMALLINT "
    "NOT NULL DEFAULT 70 CHECK (yukti_assessment_weight_pct BETWEEN 0 AND 100)",
    "ALTER TABLE functional_skills_reports ADD COLUMN IF NOT EXISTS must_have_failed "
    "BOOLEAN NOT NULL DEFAULT false",
)


def map_columns() -> None:
    """Map the columns onto the ORM classes that do not map them yet."""
    from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, SmallInteger, String
    from sqlalchemy.dialects.postgresql import JSONB, UUID

    from app.models.assessment import FunctionalSkillsReport
    from app.models.candidate import JobCandidateLink
    from app.models.tenant import Tenant

    link_columns = {
        "yukti_pre_score": lambda: Column("yukti_pre_score", Float, nullable=True),
        "yukti_status": lambda: Column(
            "yukti_status", String(20), nullable=False, default="pending",
            server_default="pending",
        ),
        "yukti_failure_reason": lambda: Column("yukti_failure_reason", String(40)),
        "evidence_tags_json": lambda: Column(
            "evidence_tags_json", JSONB, nullable=False, default=list,
            server_default="[]",
        ),
        "yukti_provenance_json": lambda: Column("yukti_provenance_json", JSONB),
        "yukti_scored_at": lambda: Column("yukti_scored_at", DateTime(timezone=True)),
        "yukti_profile_id": lambda: Column(
            "yukti_profile_id", UUID(as_uuid=True),
            ForeignKey("profiles.id", ondelete="SET NULL"),
        ),
    }
    for name, make in link_columns.items():
        if name not in JobCandidateLink.__table__.c:
            setattr(JobCandidateLink, name, make())
    if "yukti_assessment_weight_pct" not in Tenant.__table__.c:
        Tenant.yukti_assessment_weight_pct = Column(
            "yukti_assessment_weight_pct", SmallInteger, nullable=False,
            default=70, server_default="70",
        )
    if "must_have_failed" not in FunctionalSkillsReport.__table__.c:
        FunctionalSkillsReport.must_have_failed = Column(
            "must_have_failed", Boolean, nullable=False, default=False,
            server_default="false",
        )


async def _ensure_columns(url: str) -> None:
    import asyncpg

    dsn = url.replace("postgresql+asyncpg://", "postgresql://")
    connection = await asyncpg.connect(dsn, timeout=10)
    try:
        for statement in DDL:
            await connection.execute(statement)
    finally:
        await connection.close()


def ensure_columns() -> None:
    """Add the columns to the test database, when one is reachable.

    A run with no database (the pure unit modules) reaches no ranked query,
    so an unreachable server is reported on stderr and left alone rather than
    failing collection of every module that never needed it.
    """
    import asyncpg

    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith("postgresql"):
        return
    try:
        asyncio.run(_ensure_columns(url))
    except (
        OSError,
        asyncio.TimeoutError,
        asyncpg.exceptions.InvalidCatalogNameError,
    ) as exc:
        import sys

        print(
            f"phase2_pending_schema: test database not reachable ({type(exc).__name__}); "
            "the Yukti columns were not added",
            file=sys.stderr,
        )
