"""Small, audit-backed telemetry endpoints for non-blocking UI observability."""
import structlog
from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_public_db
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
