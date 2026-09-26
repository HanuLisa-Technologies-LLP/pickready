"""Stage 1 helper: the PRISM Validation section, the application's own words.

Moved from `functional_assessment.validation_node` (PLAN-p5 WP5-D) unchanged
in behaviour. Nothing here is scored, interpreted or judged (spec section 7):
the candidate's answer to "Why does this role interest you?" reaches the
recruiter exactly as written, and the recruiter, not any agent, decides
whether the stated interest is genuine.

The six application fields come first, then the candidate's full profile
questionnaire under the same `fields` list the renderers already read.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.candidate import Candidate
from app.services.application_validation import MANDATORY_KEYS, VALIDATION_FIELDS

__all__ = ["validation_section"]


async def validation_section(session: AsyncSession, link: Any) -> dict[str, Any]:
    """The section as the report row stores it (`validation_json`)."""
    from app.services.candidate_profile_form import profile_form_answers

    submitted = link.validation_json or {}
    captured = {key: submitted.get(key) for key in MANDATORY_KEYS}
    candidate = await session.get(Candidate, link.candidate_id)
    profile_fields = [
        {
            "key": item["key"],
            "label": item["question"],
            "value": item["answer"],
            "group": item["group"],
        }
        for item in profile_form_answers(
            candidate.profile_form_json if candidate else None
        )
    ]
    return {
        # "captured" = this application carried the mandatory fields at all.
        # Applications submitted before 2026-07-30 predate them and render as
        # an explicit "not collected" rather than a blank panel.
        "captured": bool(submitted),
        **captured,
        "fields": [
            {
                "key": field["key"],
                "label": field["label"],
                "value": submitted.get(field["key"]),
                "group": "Application",
            }
            for field in VALIDATION_FIELDS
        ]
        + profile_fields,
    }
