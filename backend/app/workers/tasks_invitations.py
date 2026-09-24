"""The assessment invitation email task (PLAN-p3 WP1).

`workers/tasks.py` imports this module, and that import IS the registration:
`registry.resolve` imports `app.workers.tasks` and nothing else, so a task
module missing from that line is a task no dispatch reaches
(`tests/test_task_registry.py` pins the line).
"""
from __future__ import annotations

import logging
import uuid

from app.workers.registry import Route, task
from app.workers.runtime import (
    worker_session as _worker_session,
    _run,
)

logger = logging.getLogger(__name__)


@task(
    name="pickready.send_assessment_invitation",
    route=Route.LAMBDA,
    # Safe to retry: the run is idempotent on the application's invitation
    # email (`email_outbox.invitation_key`), so a second attempt after a
    # transient database error queues nothing twice.
    max_attempts=3,
    backoff_seconds=5.0,
)
def send_assessment_invitation(link_id: str, actor_user_id: str | None = None):
    """Draft and queue the invitation email for one invited application.

    Dispatched AFTER the invitation commits (`services/assessment_invitations`),
    so the conversation row this reads is always visible. Seconds of work: one
    `email_composition` draft and one outbox row, so Route.LAMBDA. The commit
    below is what dispatches the send (`email_outbox` uses
    `dispatch_after_commit`).
    """
    from app.services import assessment_invitations

    async def _task():
        async with _worker_session() as session:
            result = await assessment_invitations.send_invitation_email(
                session,
                link_id=uuid.UUID(str(link_id)),
                actor_user_id=uuid.UUID(str(actor_user_id)) if actor_user_id else None,
            )
            await session.commit()
            logger.info(
                "assessment_invitation.%s link_id=%s reason=%s",
                result["status"],
                link_id,
                result.get("reason", ""),
            )
            return result

    return _run(_task())
