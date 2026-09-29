"""Leadership Intelligence (owner spec 2026-09-29, sections 16 to 18, 28, 31).

The rework of Drishti: `/drishti` and its capability are gone, and this is
the one surface through which a CEO, an MD or a Functional Head writes what
the company's leaders want from hires.

* `GET /me` and `PUT /me` take `author_leadership_intelligence`, and the
  stream a save writes is the AUTHOR's, read from the users row: a CEO cannot
  write the MD's input, and a Functional Head's department is theirs, never a
  field of the request (rule 37.12, `services/leadership/profiles`).
* `POST /me/draft` dispatches the AI draft (`pickready.draft_leadership`) and
  `GET /me/draft/{task_id}` reads it back from the run-status record, to the
  leader who asked and nobody else. The draft is NEVER saved by itself (rule
  37.11): nothing here writes a leadership row from a model.
* `GET /company` is the company-wide read (`view_leadership_intelligence`:
  the Super Admin, the CEO and the MD), refused to a department-scoped
  principal the way every company-wide surface is.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CurrentUser,
    get_tenant_db,
    require_capability,
    require_organisation_wide,
)
from app.schemas.leadership import (
    LeadershipCompanyOut,
    LeadershipDraftOut,
    LeadershipDraftStartOut,
    LeadershipMeOut,
    LeadershipSaveIn,
)
from app.services import capabilities as caps
from app.services.leadership import draft as leadership_draft
from app.services.leadership import profiles
from app.workers import status as run_status
from app.workers.dispatch import dispatch_after_commit

router = APIRouter()

DRAFT_TASK = "pickready.draft_leadership"


def _refused(exc: profiles.LeadershipRefused) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.get("/me", response_model=LeadershipMeOut)
async def my_leadership(
    user: CurrentUser = Depends(require_capability(caps.AUTHOR_LEADERSHIP_INTELLIGENCE)),
    session: AsyncSession = Depends(get_tenant_db),
) -> LeadershipMeOut:
    """The caller's own latest version and what they may write."""
    return LeadershipMeOut.model_validate(await profiles.my_view(session, user.user_id))


@router.put("/me", response_model=LeadershipMeOut)
async def save_my_leadership(
    body: LeadershipSaveIn,
    user: CurrentUser = Depends(require_capability(caps.AUTHOR_LEADERSHIP_INTELLIGENCE)),
    session: AsyncSession = Depends(get_tenant_db),
) -> LeadershipMeOut:
    """Save a NEW version of the caller's own stream (spec 18.3)."""
    try:
        view = await profiles.save(
            session,
            user_id=user.user_id,
            company_requirements=body.company_requirements,
            department_requirements=body.department_requirements,
            ideal_employee_expectations=body.ideal_employee_expectations,
            department_expectations=body.department_expectations,
            ai_draft_sources=body.ai_draft_sources,
        )
    except profiles.LeadershipRefused as exc:
        raise _refused(exc) from exc
    return LeadershipMeOut.model_validate(view)


@router.post(
    "/me/draft",
    response_model=LeadershipDraftStartOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def draft_my_leadership(
    user: CurrentUser = Depends(require_capability(caps.AUTHOR_LEADERSHIP_INTELLIGENCE)),
    session: AsyncSession = Depends(get_tenant_db),
) -> LeadershipDraftStartOut:
    """Ask for an AI draft. Answers a task id to poll; writes nothing.

    Refused BEFORE the dispatch for a caller who is not a leader, so a model
    call is never spent on somebody who could not save its result.
    """
    author = await profiles.author_of(session, user.user_id)
    if author is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=profiles.NOT_A_LEADER)
    handle = dispatch_after_commit(
        session, DRAFT_TASK, args=[str(author.user_id), str(author.tenant_id)]
    )
    return LeadershipDraftStartOut(task_id=handle.id)


@router.get("/me/draft/{task_id}", response_model=LeadershipDraftOut)
async def read_my_draft(
    task_id: str,
    user: CurrentUser = Depends(require_capability(caps.AUTHOR_LEADERSHIP_INTELLIGENCE)),
) -> LeadershipDraftOut:
    """The draft, once written, to the leader who asked for it.

    PENDING until the task reports. A record written for somebody else reads
    exactly like one that does not exist (a 404), for the reason a
    cross-tenant read does: telling a caller a draft exists is the leak.
    """
    record = await run_status.read(task_id)
    if record.state == run_status.STATE_FAILURE:
        return LeadershipDraftOut(
            status="failed",
            message="The draft could not be written just now. Write it yourself, or try again.",
        )
    if record.state != run_status.STATE_SUCCESS:
        return LeadershipDraftOut(status="pending")
    if user.tenant_id is None or not leadership_draft.owner_matches(
        record.payload, user_id=user.user_id, tenant_id=user.tenant_id
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found")
    return LeadershipDraftOut.model_validate(
        {key: value for key, value in record.payload.items() if key in LeadershipDraftOut.model_fields}
    )


@router.get("/company", response_model=LeadershipCompanyOut)
async def company_leadership(
    user: CurrentUser = Depends(
        require_organisation_wide(caps.VIEW_LEADERSHIP_INTELLIGENCE)
    ),
    session: AsyncSession = Depends(get_tenant_db),
) -> LeadershipCompanyOut:
    """Every leader's latest version, read-only (spec 28)."""
    if user.tenant_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=profiles.NOT_A_LEADER)
    return LeadershipCompanyOut.model_validate(
        await profiles.company_overview(session, user.tenant_id)
    )


__all__ = ["router", "DRAFT_TASK"]
