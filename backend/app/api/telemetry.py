"""Small, audit-backed telemetry endpoints for non-blocking UI observability."""
import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_public_db, get_tenant_db, require_capability
from app.models.candidate import JobCandidateLink
from app.services import capabilities as caps
from app.services import rbac
from app.services.audit import audit
from app.services.telemetry import landing_view_allowed

router = APIRouter()
log = structlog.get_logger()


@router.post("/landing-view", status_code=status.HTTP_204_NO_CONTENT)
async def landing_view(
    request: Request,
    session: AsyncSession = Depends(get_public_db),
) -> Response:
    """Record a bounded anonymous landing view without retaining visitor PII."""
    client_host = request.client.host if request.client else None
    if await landing_view_allowed(client_host):
        await audit(
            session,
            tenant_id=None,
            actor_user_id=None,
            action="landing_viewed",
            target_type="page",
            target_id="landing",
            metadata=None,
        )
        log.info("telemetry.landing_viewed")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/rating-comments-view/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def rating_comments_view(
    link_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.VIEW_REVIEW_SCREEN)),
    session: AsyncSession = Depends(get_tenant_db),
) -> Response:
    """Audit a displayed AI explanation after checking the profile's access rules."""
    link = await session.get(JobCandidateLink, link_id)
    if link is None or link.tenant_id != user.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Link not found")

    full_access = await rbac.has_capability(
        session, user.tenant_id, user.role, caps.SEND_OUTREACH
    )
    if not full_access and not link.hm_access_granted:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Profile access not granted",
        )

    await audit(
        session,
        tenant_id=user.tenant_id,
        actor_user_id=user.user_id,
        action="rating_comments_viewed",
        target_type="job_candidate_link",
        target_id=link.id,
        metadata=None,
    )
    log.info("telemetry.rating_comments_viewed", tenant_id=str(user.tenant_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
