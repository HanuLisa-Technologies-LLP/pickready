"""Which jobs hold more than five active skills in a bucket. Read only.

The skills contract allows at most five skills per bucket (Must-have,
Nice-to-have, Behavioural). Migration `0118_skills_contract` did NOT truncate
the matrices that were already larger: removing a criterion a hiring manager
approved, or one a started candidate was questioned against, is a decision for
a person, not a migration. It logged every such bucket instead, and this
script prints the same list on demand, so the report does not live only in a
migration log that has since rotated.

An UNLOCKED job over the limit keeps working and is refused at its next Save
with the count; a LOCKED job keeps its snapshot exactly as it was taken.

    python -m app.scripts.skills_overflow_report

Writes nothing. Exit status 0 whatever it finds: this is a report, not a gate.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session_factory, superadmin_scope

#: The per-bucket ceiling. Mirrors `MAX_PER_BUCKET` in migration 0118;
#: `tests/test_skills_contract_migration.py` asserts the two report the same rows.
MAX_PER_BUCKET = 5

#: Locked = the job has a skills snapshot, the table and never a stamp.
_OVERFLOW_SQL = text(
    """
    SELECT jc.job_id, j.tenant_id, j.title, jc.category, COUNT(*) AS active,
           EXISTS (SELECT 1 FROM job_skill_snapshots s WHERE s.job_id = jc.job_id)
               AS locked
      FROM job_competencies jc
      JOIN jobs j ON j.id = jc.job_id
     WHERE jc.is_active
     GROUP BY jc.job_id, j.tenant_id, j.title, jc.category
    HAVING COUNT(*) > :limit
     ORDER BY jc.job_id, jc.category
    """
)


@dataclass(frozen=True)
class OverflowRow:
    job_id: uuid.UUID
    tenant_id: uuid.UUID
    title: str
    bucket: str
    active: int
    locked: bool


async def overflow_rows(session: AsyncSession) -> list[OverflowRow]:
    """Every (job, bucket) holding more than `MAX_PER_BUCKET` active skills.

    The caller supplies the scope; `main` reads across tenants through the
    audited bypass, the same path every other operator report uses.
    """
    rows = (await session.execute(_OVERFLOW_SQL, {"limit": MAX_PER_BUCKET})).mappings().all()
    return [
        OverflowRow(
            job_id=row["job_id"],
            tenant_id=row["tenant_id"],
            title=row["title"],
            bucket=row["category"],
            active=int(row["active"]),
            locked=bool(row["locked"]),
        )
        for row in rows
    ]


async def run() -> list[OverflowRow]:
    factory = get_session_factory()
    async with factory() as session:
        async with superadmin_scope(session):
            rows = await overflow_rows(session)
            await session.rollback()
    return rows


def main() -> int:
    rows = asyncio.run(run())
    for row in rows:
        state = "locked" if row.locked else "unlocked"
        print(
            f"job_id={row.job_id} tenant_id={row.tenant_id} bucket={row.bucket} "
            f"active={row.active} limit={MAX_PER_BUCKET} {state} title={row.title!r}"
        )
    print(
        f"buckets_over_limit={len(rows)} jobs={len({r.job_id for r in rows})} "
        f"locked_jobs={len({r.job_id for r in rows if r.locked})}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
