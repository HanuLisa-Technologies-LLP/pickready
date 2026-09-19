"""Drishti, the functional head's strategic profile (vivekium feature 1, C3).

ONE PROFILE PER FUNCTION, updated in place by the client at any time; a
leadership change is the CLIENT's trigger, never auto-detected. The PUT is
the whole conversation loop's server half: it stores the sections, runs the
deterministic observable-evidence critique per section (the same detector
Bodha's SWOT rules use), COMPILES the artifact, and returns the probes, so
the screen can keep asking until the prose says something watchable.

CAPABILITY: `edit_company_profile`, the client-owned strategic-context
authority this already is; reading takes the same, because a function's
strategic profile is the company's own page, not recruitment data.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_tenant_db, require_capability
from app.models.drishti import DrishtiProfile
from app.services import capabilities as caps
from app.services.audit import audit
from app.services.hiring import drishti

router = APIRouter()


class DrishtiIn(BaseModel):
    function_name: str = Field(min_length=2, max_length=120)
    strategic_purpose: str = Field(default="", max_length=8000)
    people_philosophy: str = Field(default="", max_length=8000)
    non_negotiables: str = Field(default="", max_length=8000)
    culture_expectations: str = Field(default="", max_length=8000)
    strategic_gap: str = Field(default="", max_length=8000)


def _profile_out(row: DrishtiProfile) -> dict:
    sections = {
        key: getattr(row, key) or "" for key in drishti.SECTION_KEYS
    }
    return {
        "id": str(row.id),
        "function_name": row.function_name,
        "sections": sections,
        "critiques": {key: drishti.critique(text) for key, text in sections.items()},
        "compiled": row.compiled_json,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.get("/sections")
async def list_sections(
    user: CurrentUser = Depends(require_capability(caps.EDIT_COMPANY_PROFILE)),
) -> dict:
    """The catalogue, server-authored, so the screen writes no prompts."""
    return {"sections": drishti.sections_payload()}


@router.get("/functions")
async def list_functions(
    user: CurrentUser = Depends(require_capability(caps.EDIT_COMPANY_PROFILE)),
    session: AsyncSession = Depends(get_tenant_db),
) -> dict:
    rows = (
        await session.execute(
            select(DrishtiProfile)
            .where(DrishtiProfile.tenant_id == user.tenant_id)
            .order_by(DrishtiProfile.function_name)
        )
    ).scalars().all()
    return {"profiles": [_profile_out(row) for row in rows]}


@router.put("/functions")
async def upsert_function(
    body: DrishtiIn,
    user: CurrentUser = Depends(require_capability(caps.EDIT_COMPANY_PROFILE)),
    session: AsyncSession = Depends(get_tenant_db),
) -> dict:
    """Create or update ONE function's profile, recompile, return probes.

    The emphasis in the compiled artifact names competencies from the
    tenant's own frozen matrices' vocabulary is a per-freeze concern;
    compilation here records the OBSERVABLE lines and the non-negotiable
    names verbatim, and `scorecard.compile_matrix` resolves emphasis at
    freeze time against the competencies the matrix actually holds, so a
    profile written before a job exists still reaches it.
    """
    name = body.function_name.strip()
    row = (
        await session.execute(
            select(DrishtiProfile).where(
                DrishtiProfile.tenant_id == user.tenant_id,
                DrishtiProfile.function_name.ilike(name),
            )
        )
    ).scalars().first()
    if row is None:
        row = DrishtiProfile(tenant_id=user.tenant_id, function_name=name)
        session.add(row)
    sections = {
        "strategic_purpose": body.strategic_purpose.strip(),
        "people_philosophy": body.people_philosophy.strip(),
        "non_negotiables": body.non_negotiables.strip(),
        "culture_expectations": body.culture_expectations.strip(),
        "strategic_gap": body.strategic_gap.strip(),
    }
    for key, value in sections.items():
        setattr(row, key, value)
    row.compiled_json = drishti.compile_profile(
        function_name=name, sections=sections
    )
    row.updated_by = user.user_id
    row.updated_at = datetime.now(timezone.utc)
    await session.flush()
    await audit(
        session,
        tenant_id=user.tenant_id,
        actor_user_id=user.user_id,
        action="drishti_profile_saved",
        target_type="drishti_profile",
        target_id=row.id,
        metadata={"function": name},
    )
    return _profile_out(row)
