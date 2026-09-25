"""The retired Matching categories: ONE READER LEFT, and it is Phase 5's.

VIVEKIUM RELEASE (Phase 2 WP-F, 2026-09-25): the category generator, its
prompt, its task, the Matching Categories editor and its routes, the finalise
check, the key helpers and the legacy scorer that read these rows are all
DELETED. Yukti scores a FIXED structure held in `yukti/config.py` (owner
decision D2), and nobody edits categories.

What survives is `resolved_categories`, for exactly one reader:
`functional_assessment._matching_dimensions`, the PRISM AI Score section,
which still renders the frozen `match_breakdown_json` under the category names
it was scored on. Phase 5 replaces that section with
`yukti.projection.ai_score_summary`; on that change this module and the
`JobMatchingCategory` model's last read go with it. The TABLE stays (S4: a
legacy customer table is never dropped), so this is a code deletion then, not
a migration.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job_setup import JobMatchingCategory

__all__ = ["resolved_categories"]

#: (key, name, description) for the four keys every pre-2026-08 breakdown was
#: scored on. A job with no category rows renders its frozen breakdown under
#: these names, which is what it was scored against.
_LEGACY_CATEGORIES: tuple[tuple[str, str, str], ...] = (
    (
        "skills_match",
        "Skills present",
        "Whether the resume indicates the skills the job description requires, at "
        "all. A presence check judged on semantic equivalence, never a depth check.",
    ),
    (
        "experience_relevance",
        "Experience level",
        "Whether the experience is in the same function and at a comparable level, "
        "not a numeric count of years.",
    ),
    (
        "role_alignment",
        "Role and designation alignment",
        "The candidate's actual designation and duties against the job's role and "
        "responsibilities. Duties over titles.",
    ),
    (
        "education_fit",
        "Education",
        "Degree level and specialisation against the job's education requirement.",
    ),
)


async def resolved_categories(
    session: AsyncSession, job_id: Any
) -> list[tuple[str, str, str]]:
    """This job's categories as (key, name, description), never empty.

    The job's own rows when it has any (the frozen breakdown was filed under
    their keys), else the four legacy keys every older breakdown carries.
    """
    rows = (
        await session.execute(
            select(JobMatchingCategory)
            .where(
                JobMatchingCategory.job_id == job_id,
                JobMatchingCategory.is_active.is_(True),
            )
            .order_by(JobMatchingCategory.ordinal)
        )
    ).scalars().all()
    if rows:
        return [(row.key, row.name, row.description or "") for row in rows]
    return list(_LEGACY_CATEGORIES)
