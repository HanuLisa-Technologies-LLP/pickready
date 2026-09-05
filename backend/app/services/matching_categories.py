"""The Matching Agent's per-job category list (spec §3.2).

WHAT CHANGED
------------
Matching used to run on ONE fixed set of four parameters across every job in the
product. It now follows the same AI-recommends, recruiter-finalises pattern the
PPI matrix does, generated fresh per job: the AI proposes at least five
categories at job creation, the recruiter adds, modifies, replaces or removes
them, and the finalised list applies automatically to every candidate sourced
for that job with no further per-candidate step.

THE LINE MATCHING MUST NOT CROSS
--------------------------------
Every category here is judged from RESUME TEXT ALONE, before the candidate has
spoken to anything. Skill depth and verified behavioural competency belong to
PPI and are assessed in a conversation. The two share named territory in places
-- "Skills present" and a Must-have skill item can carry the same word -- and
they ask genuinely different questions of genuinely different evidence. The
prompt says so, and `DEFAULT_CATEGORIES`' descriptions say so, because this is
the distinction that keeps the AI Score and the PPI Assessment worth showing
separately (spec §9.1).

WHY THE DEFAULT KEYS ARE THE OLD ONES
-------------------------------------
Four of the five defaults keep the exact `key` the four fixed parameters used
(`skills_match`, `experience_relevance`, `role_alignment`, `education_fit`),
even though their display names are now the specification's. Every ranked
candidate in the product carries a `match_breakdown_json` keyed on those
strings. Renaming the keys would have orphaned every score already written and
bought nothing: a key is what the scorer files under, a name is what a human
reads, and only the second one needed to change.

THE MINIMUM OF FIVE IS ENFORCED SERVER-SIDE
-------------------------------------------
`finalize` refuses a list shorter than five. The rule is the client's and it is
about comparability: a job ranked on two categories is not comparable with the
rest of the product, and "the UI disabled the button" is not an enforcement of
anything a determined client cannot post around.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job
from app.models.job_setup import JobMatchingCategory
from app.prompts import registry
from app.services import agent_loop, llm_router

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_CATEGORIES",
    "DEFAULT_KEYS",
    "MAXIMUM_CATEGORIES",
    "MINIMUM_CATEGORIES",
    "generate_categories",
    "list_categories",
    "load_keys",
    "resolved_categories",
    "slugify",
]

#: The client's stated minimum, enforced at save (spec §3.2).
MINIMUM_CATEGORIES = 5

#: Not in the specification. A list this long stops being a ranking and starts
#: being a form: every category costs a comment on every candidate, and a
#: recruiter reading twelve 25-30 word comments per resume is reading an essay
#: instead of a shortlist.
MAXIMUM_CATEGORIES = 8

#: (key, name, description). The AI's proposed default five (spec §3.2).
#: Ordered as the specification lists them.
DEFAULT_CATEGORIES: tuple[tuple[str, str, str], ...] = (
    (
        "skills_match",
        "Skills present",
        "Whether the resume indicates the skills the job description requires, at "
        "all. A presence check judged on semantic equivalence, never a depth check.",
    ),
    (
        "behavioural_signal",
        "Behavioural signal",
        "What the resume's own language suggests about how the candidate operates: "
        "achievement phrasing, scope of responsibility, action verbs. Inferred from "
        "text alone and never verified.",
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

DEFAULT_KEYS: tuple[str, ...] = tuple(key for key, _, _ in DEFAULT_CATEGORIES)

#: The four the product scored on before Draft v4. A job whose categories were
#: never generated (created before this release, or generated and not yet
#: landed) is scored on exactly these, so nothing about an existing job's
#: ranking changes underneath it.
LEGACY_KEYS: tuple[str, ...] = (
    "skills_match",
    "experience_relevance",
    "role_alignment",
    "education_fit",
)

_MAX_KEY = 60


def slugify(name: str) -> str:
    """A stable key from a display name.

    Lower case, words joined by underscores, bounded. Deterministic so the same
    name always produces the same key -- which matters because the key is what
    a score is filed under, and a recruiter who deletes a category and adds it
    back with the same name should not orphan the scores in between.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", str(name or "").casefold()).strip("_")
    return (slug or "category")[:_MAX_KEY]


async def list_categories(
    session: AsyncSession, job_id: Any
) -> list[JobMatchingCategory]:
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
    return list(rows)


async def resolved_categories(
    session: AsyncSession, job_id: Any
) -> list[tuple[str, str, str]]:
    """This job's categories as (key, name, description), never empty.

    Falls back to the four the product has always scored on rather than to the
    new default five, and the direction is deliberate. A job created before
    Draft v4 has candidates already ranked on four keys; handing the scorer a
    fifth would leave every existing candidate with a category the report has to
    render as missing. A new job gets its five from the generator.
    """
    rows = await list_categories(session, job_id)
    if rows:
        return [(row.key, row.name, row.description or "") for row in rows]
    by_key = {key: (key, name, description) for key, name, description in DEFAULT_CATEGORIES}
    return [by_key[key] for key in LEGACY_KEYS]


async def load_keys(session: AsyncSession, job_id: Any) -> tuple[str, ...]:
    """Just the keys, which is what the scorer and the aggregate need."""
    return tuple(key for key, _, _ in await resolved_categories(session, job_id))


def _normalise(rows: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        name = " ".join(str(row.get("name") or "").split())[:255]
        if not name:
            continue
        key = slugify(name)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "key": key,
                "name": name,
                "description": " ".join(str(row.get("description") or "").split())[:1000],
            }
        )
        if len(out) >= MAXIMUM_CATEGORIES:
            break
    return out


def _default_rows() -> list[dict[str, str]]:
    return [
        {"key": key, "name": name, "description": description}
        for key, name, description in DEFAULT_CATEGORIES
    ]


def _merge_defaults(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Top a short generation up to the minimum, from the defaults.

    Reached when the model returned fewer than five usable categories. Topping
    up is better than rejecting: the recruiter reviews this list anyway, and a
    default they choose to delete costs one click, while an empty screen costs
    them the whole list by hand.
    """
    if len(rows) >= MINIMUM_CATEGORIES:
        return rows
    have = {row["key"] for row in rows}
    for default in _default_rows():
        if len(rows) >= MINIMUM_CATEGORIES:
            break
        if default["key"] in have:
            continue
        rows.append(default)
    return rows


async def generate_categories(
    session: AsyncSession, job: Job, *, replace: bool = False
) -> list[JobMatchingCategory]:
    """Propose this job's Matching categories and leave them AWAITING REVIEW.

    Idempotent by default: a job that already has categories keeps them, so a
    redelivery cannot discard a list a recruiter has already edited.

    Never approves anything. `jobs.matching_categories_finalized_at` is stamped
    only when the recruiter saves, which is the other half of the one setup
    session (spec §10).
    """
    existing = (
        await session.execute(
            select(JobMatchingCategory).where(JobMatchingCategory.job_id == job.id)
        )
    ).scalars().all()
    if existing and not replace:
        return list(existing)

    system = registry.render(
        "matching_categories_system",
        defaults="\n".join(
            f"  {name}: {description}" for _, name, description in DEFAULT_CATEGORIES
        ),
        maximum=MAXIMUM_CATEGORIES,
    )
    payload = json.dumps(
        {
            "title": job.title,
            "grade": job.assessment_grade,
            "experience_min_years": job.experience_min_years,
            "experience_max_years": job.experience_max_years,
            "jd": job.jd_json,
            "jd_markdown": (job.jd_markdown or "")[:6000],
        }
    )

    async def _execute(reflection: str) -> list[dict[str, str]]:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": payload},
        ]
        if reflection:
            messages.append({"role": "user", "content": reflection})
        raw = await llm_router.chat_completion(
            "jd_generation", messages, response_format_json=True, session=session
        )
        return _normalise(json.loads(raw).get("categories", []))

    def _evaluate(candidate: list[dict[str, str]]) -> agent_loop.Critique:
        if len(candidate) < MINIMUM_CATEGORIES:
            return agent_loop.reject(
                f"return at least {MINIMUM_CATEGORIES} categories; the previous "
                f"attempt returned {len(candidate)}"
            )
        missing = [row["name"] for row in candidate if not row["description"]]
        if missing:
            return agent_loop.reject(
                "every category needs a one-line description of what in a resume "
                "answers it; these had none: " + ", ".join(missing)
            )
        return agent_loop.ok()

    result = await agent_loop.run_loop(
        name="matching_categories",
        execute=_execute,
        evaluate=_evaluate,
        fallback=_default_rows(),
        max_attempts=agent_loop.BACKGROUND_ATTEMPTS,
        deadline_seconds=agent_loop.BACKGROUND_DEADLINE,
        max_generated_tokens=agent_loop.BACKGROUND_TOKEN_BUDGET,
    )
    if result.degraded:
        logger.warning(
            "matching_categories.degraded job_id=%s reasons=%s",
            job.id, list(result.reasons),
        )
    rows = _merge_defaults(list(result.value or _default_rows()))

    for row in existing:
        row.is_active = False
    created = [
        JobMatchingCategory(
            tenant_id=job.tenant_id,
            job_id=job.id,
            key=row["key"],
            name=row["name"],
            description=row["description"] or None,
            ordinal=ordinal,
        )
        for ordinal, row in enumerate(rows, 1)
    ]
    session.add_all(created)
    await session.flush()
    logger.info(
        "matching_categories.generated job_id=%s count=%d", job.id, len(created)
    )
    return created


def categories_are_complete(
    rows: list[JobMatchingCategory],
) -> tuple[bool, str | None]:
    """Whether this list may be finalised (spec §3.2).

    One rule, and it is the client's: at least five after editing. Enforced here
    rather than only in the UI, because a disabled button is not an enforcement.
    """
    active = [row for row in rows if row.is_active]
    if len(active) < MINIMUM_CATEGORIES:
        return False, (
            f"A job is matched on at least {MINIMUM_CATEGORIES} categories. There "
            f"{'is' if len(active) == 1 else 'are'} {len(active)}. Please add "
            f"{MINIMUM_CATEGORIES - len(active)} more before saving."
        )
    return True, None


def touch(row: JobMatchingCategory) -> None:
    row.updated_at = datetime.now(timezone.utc)


assert len(DEFAULT_CATEGORIES) == MINIMUM_CATEGORIES
assert len(set(DEFAULT_KEYS)) == len(DEFAULT_KEYS)
assert set(LEGACY_KEYS) < set(DEFAULT_KEYS)
assert MAXIMUM_CATEGORIES >= MINIMUM_CATEGORIES
