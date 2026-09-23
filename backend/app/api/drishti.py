"""Drishti, the functional head's strategic profile (vivekium feature 1, C3).

ONE PROFILE PER FUNCTION PER FUNCTIONAL HEAD, updated in place by that head
at any time; a leadership change is the CLIENT's trigger, never
auto-detected. There are TWO doors into the same five sections and they
converge before anything else in the product sees them:

* `PUT /functions` is the form. It stores the sections, runs the
  deterministic observable-evidence critique per section (the same detector
  Bodha's SWOT rules use), COMPILES the artifact, and returns the probes.
* `POST /conversation/turn` is the structured capture conversation the
  brief asks for. It captures the head's own words one section at a time
  and returns the next question; when it finishes, the client saves through
  the PUT above. It has no compiler of its own, no artifact of its own and
  no table of its own, which is what keeps the C3 guarantee structural
  rather than careful: see `services/hiring/drishti_conversation`.

CAPABILITY: `author_drishti_profile`, seeded by migration 0115. NOT
`edit_company_profile`, which every client-side staff role holds including
the Hiring Manager, and the brief excludes the Hiring Manager by name. The
READ takes the same capability as the write here rather than a wider one,
because the profile is the function's strategic direction stated in its
head's own words and there is no surface in the product that needs to show
it to somebody who could not have written it; the COMPILED artifact reaches
the matrix by itself and needs no reader.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_tenant_db, require_capability
from app.models.drishti import DrishtiProfile
from app.models.user import User
from app.services import capabilities as caps
from app.services.audit import audit
from app.services.hiring import drishti, drishti_conversation

router = APIRouter()

#: What the server says when somebody else's profile is about to be taken
#: over. Server-authored in one place, for the reason the offer gate's
#: `offer_blocked_reason` is: the screen renders it verbatim, so the sentence
#: describing the rule and the rule itself cannot drift.
HEAD_CHANGE_REQUIRED = (
    "This function's Drishti profile belongs to {name}. Recording a change of "
    "functional head is a decision somebody has to make deliberately, so "
    "confirm it and you will become the head of record for this function."
)


class DrishtiIn(BaseModel):
    function_name: str = Field(min_length=2, max_length=120)
    strategic_purpose: str = Field(default="", max_length=8000)
    people_philosophy: str = Field(default="", max_length=8000)
    non_negotiables: str = Field(default="", max_length=8000)
    culture_expectations: str = Field(default="", max_length=8000)
    strategic_gap: str = Field(default="", max_length=8000)
    #: The head's own title, as they would say it: CTO, CFO, COO, MD.
    #: Recorded beside the binding and denormalised, so the row still reads
    #: "the CTO's profile" after the user row is gone.
    functional_head_title: str = Field(default="", max_length=120)
    #: The client's explicit trigger for a change of functional head. False
    #: is the safe direction and the default: without it, taking over
    #: somebody else's function is refused rather than done silently, which
    #: is the difference between a client decision and auto-detection.
    functional_head_change_confirmed: bool = False


class TurnIn(BaseModel):
    """One exchange of the capture conversation.

    The client posts back what it holds, because the conversation is
    stateless by design (see `drishti_conversation`). Everything here is
    clamped or re-derived server-side: `sections` is filtered to the
    catalogue's five keys and capped, `turns_in_section` is clamped to the
    budget, and `answer` goes through the guardrails before it is read.
    """

    function_name: str = Field(min_length=2, max_length=120)
    #: Empty opens the conversation at the first section.
    section_key: str = Field(default="", max_length=64)
    sections: dict[str, str] = Field(default_factory=dict)
    answer: str = Field(default="", max_length=8000)
    turns_in_section: int = Field(default=0, ge=0, le=64)


def _profile_out(row: DrishtiProfile, user: CurrentUser) -> dict:
    sections = {
        key: getattr(row, key) or "" for key in drishti.SECTION_KEYS
    }
    return {
        "id": str(row.id),
        "function_name": row.function_name,
        "sections": sections,
        "critiques": {key: drishti.critique(text) for key, text in sections.items()},
        "compiled": row.compiled_json,
        "functional_head_name": row.functional_head_name,
        "functional_head_title": row.functional_head_title,
        # Whether THIS caller is the head of record. A resource-scoped answer
        # the capability list structurally cannot give: holding
        # `author_drishti_profile` says a person may author a profile, not
        # that this one is theirs. The screen combines the two through
        # `resolvePermission`; both the read and the write re-authorize.
        "is_functional_head": (
            row.functional_head_user_id is None
            or row.functional_head_user_id == user.user_id
        ),
        "functional_head_bound_at": (
            row.functional_head_bound_at.isoformat()
            if row.functional_head_bound_at
            else None
        ),
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.get("/sections")
async def list_sections(
    user: CurrentUser = Depends(require_capability(caps.AUTHOR_DRISHTI_PROFILE)),
) -> dict:
    """The catalogue, server-authored, so the screen writes no prompts."""
    return {"sections": drishti.sections_payload()}


@router.get("/functions")
async def list_functions(
    user: CurrentUser = Depends(require_capability(caps.AUTHOR_DRISHTI_PROFILE)),
    session: AsyncSession = Depends(get_tenant_db),
) -> dict:
    rows = (
        await session.execute(
            select(DrishtiProfile)
            .where(DrishtiProfile.tenant_id == user.tenant_id)
            .order_by(DrishtiProfile.function_name)
        )
    ).scalars().all()
    return {"profiles": [_profile_out(row, user) for row in rows]}


@router.put("/functions")
async def upsert_function(
    body: DrishtiIn,
    user: CurrentUser = Depends(require_capability(caps.AUTHOR_DRISHTI_PROFILE)),
    session: AsyncSession = Depends(get_tenant_db),
) -> dict:
    """Create or update ONE function's profile, recompile, return probes.

    THE BINDING IS ENFORCED HERE, AND HERE ONLY, because this is the single
    write path both doors reach. A profile with a head of record who is not
    the caller is REFUSED with a 409 naming them, and the only way past it
    is `functional_head_change_confirmed`, which rebinds and audits. Doing
    it the other way round, letting any holder of the capability write and
    quietly become the author, is precisely the auto-detection the brief
    forbids: the platform would have decided a leadership change happened
    because somebody opened a form.

    Compilation stays deterministic and model-free (C3), so the artifact
    every assessment in this function reads is reproducible, diffable
    between versions and explainable during a provider outage. Emphasis is
    resolved LATER, at freeze, against the competencies the matrix actually
    holds, so a profile written before a job exists still reaches it.
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

    binding = drishti.resolve_head_binding(
        current_head_id=row.functional_head_user_id if row is not None else None,
        caller_id=user.user_id,
        exists=row is not None,
        change_confirmed=body.functional_head_change_confirmed,
    )
    if binding == drishti.REFUSE:
        # A 409 rather than a 403. The caller holds the capability; what they
        # do not have is a confirmed decision, and calling that a permission
        # failure would send them to an administrator who cannot help.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=HEAD_CHANGE_REQUIRED.format(
                name=(
                    row.functional_head_name
                    if row is not None and row.functional_head_name
                    else "another functional head"
                )
            ),
        )
    if row is None:
        row = DrishtiProfile(tenant_id=user.tenant_id, function_name=name)
        session.add(row)
    rebound = binding == drishti.BIND

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
    if rebound:
        author = await session.get(User, user.user_id)
        row.functional_head_user_id = user.user_id
        # Denormalised at bind time so "the CTO wrote this" survives the
        # ON DELETE SET NULL above.
        row.functional_head_name = (
            (author.full_name or author.email) if author is not None else None
        )
        row.functional_head_bound_at = datetime.now(timezone.utc)
    title = body.functional_head_title.strip()
    if title:
        row.functional_head_title = title
    row.updated_by = user.user_id
    row.updated_at = datetime.now(timezone.utc)
    await session.flush()
    await audit(
        session,
        tenant_id=user.tenant_id,
        actor_user_id=user.user_id,
        action=(
            "drishti_functional_head_bound" if rebound else "drishti_profile_saved"
        ),
        target_type="drishti_profile",
        target_id=row.id,
        metadata={"function": name},
    )
    return _profile_out(row, user)


@router.post("/conversation/turn")
async def conversation_turn(
    body: TurnIn,
    user: CurrentUser = Depends(require_capability(caps.AUTHOR_DRISHTI_PROFILE)),
    session: AsyncSession = Depends(get_tenant_db),
) -> dict:
    """One exchange of the capture conversation.

    It writes NOTHING. The turn returns the five sections accumulated so
    far and the next question, and the profile only exists once the client
    saves it through the PUT above, which is where the binding, the
    compilation and the audit row all are. That is deliberate: a
    conversation that wrote as it went would be a second way a Drishti
    profile half-exists, and the artifact the matrix reads must have exactly
    one writer.
    """
    turn = await drishti_conversation.next_turn(
        session,
        function_name=body.function_name.strip(),
        section_key=body.section_key.strip(),
        sections=body.sections,
        answer=body.answer,
        turns_in_section=body.turns_in_section,
    )
    return {
        "section_key": turn.section_key,
        "agent_message": turn.agent_message,
        "sections": turn.sections,
        "turns_in_section": turn.turns_in_section,
        # Never inferred by the client. A deterministic probe presented as
        # generation is the lie this field exists to prevent.
        "generated_by_ai": turn.generated_by_ai,
        "refused": turn.refused,
        "finished": turn.finished,
    }


__all__ = ["router", "HEAD_CHANGE_REQUIRED"]
