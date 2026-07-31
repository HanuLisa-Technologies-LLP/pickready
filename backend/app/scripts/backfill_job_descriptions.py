"""Generate and persist missing candidate-facing job descriptions.

Dry-run by default. Use ``--apply`` to write the generated descriptions.
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.core.db import get_session_factory, superadmin_scope
from app.models.job import Job
from app.models.tenant import Tenant
from app.services.jd_generation import generate_job_description


async def run(*, apply: bool) -> tuple[int, int]:
    scanned = 0
    updated = 0
    async with get_session_factory()() as session:
        async with session.begin():
            async with superadmin_scope(session):
                jobs = (
                    await session.execute(select(Job).order_by(Job.created_at, Job.id))
                ).scalars().all()
                for job in jobs:
                    jd = dict(job.jd_json or {})
                    if str(jd.get("description") or "").strip():
                        continue
                    scanned += 1
                    company = await session.get(Tenant, job.tenant_id)
                    generated = await generate_job_description(
                        {
                            "title": job.title,
                            "requirements": jd.get("responsibilities"),
                            "skills": jd.get("skills") or [],
                            "experience": jd.get("experience_years"),
                            "company_context": (
                                f"{company.name}. {company.culture or ''}"
                                if company
                                else ""
                            ),
                            "department": job.department,
                            "level": job.level,
                        }
                    )
                    description = str(generated.get("description") or "").strip()
                    if not description:
                        continue
                    if apply:
                        jd["description"] = description
                        job.jd_json = jd
                    updated += 1
                if not apply:
                    await session.rollback()
    return scanned, updated


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist generated descriptions. Without this flag the run is read-only.",
    )
    args = parser.parse_args()
    scanned, updated = asyncio.run(run(apply=args.apply))
    print(
        f"missing_descriptions={scanned} "
        f"{'updated' if args.apply else 'generatable'}={updated}"
    )
    return 0 if scanned == updated else 1


if __name__ == "__main__":
    raise SystemExit(main())
