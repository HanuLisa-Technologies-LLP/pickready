"""The Leadership Intelligence draft task (owner spec 2026-09-29, section 17).

Its own module for the reason the 2026-09-24 carve gave the assessment tasks
theirs; `workers/tasks.py` imports it, which is what registers it.

WHY IT IS DISPATCHED
--------------------
A draft gathers public pages and makes a reasoning call, which is minutes of
worst case and not a request handler's work (rule 4). The two synchronous
agents (`agent_client`) are a closed set by design, and the generative
interactive tier is capped at two, so the draft is a background task like the
Job SWOT: the route answers 202 with a task id, and the screen polls
`GET /leadership/me/draft/{task_id}`, which reads the run-status record.

WHY THE RESULT RIDES THE RUN-STATUS RECORD AND NOT A TABLE
-----------------------------------------------------------
An AI draft is NEVER saved (rule 37.11). A table row holding it would be a
draft saved by another name. The run-status record is transient by design
(six hours, Redis) and carries the owner, so the poll route answers only the
leader who asked. An expired record reads as PENDING, and the screen stops
polling after its own bounded wait and says the draft could not be read.
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
    name="pickready.draft_leadership",
    route=Route.LAMBDA,
    rls="bypass",
    rls_reason=(
        "one leader's draft in one tenant: every read carries an explicit tenant "
        "predicate, and the only write is that tenant's audit row; not yet proven "
        "under tenant_worker_session with a real-Postgres read-back test"
    ),
)
def draft_leadership(user_id: str, tenant_id: str):
    """Write one AI draft for one leader. Writes no leadership row.

    ONE attempt: a failed draft is a STATE the leader sees (they write it
    themselves or ask again), and retrying here would spend a second model
    call nobody is waiting for.
    """
    from app.services.audit import record_action
    from app.services.leadership import draft, profiles

    async def _task() -> dict:
        async with _worker_session() as session:
            author = await profiles.author_of(session, uuid.UUID(str(user_id)))
            if author is None or str(author.tenant_id) != str(tenant_id):
                # The role changed between the click and the run: nothing to draft.
                logger.info("leadership.draft_author_gone user_id=%s", user_id)
                return {
                    "owner_user_id": str(user_id),
                    "tenant_id": str(tenant_id),
                    "status": "failed",
                    "fields": {},
                    "sources": [],
                    "message": profiles.NOT_A_LEADER,
                    "generated_by_ai": False,
                }
            outcome = await draft.draft_for(session, author)
            # Spec 29: the event, never the text.
            await record_action(
                session,
                action="leadership_draft_generated",
                actor_user_id=author.user_id,
                actor_role=author.role,
                tenant_id=author.tenant_id,
                resource_type="leadership_profile",
                metadata={
                    "status": outcome.status,
                    "sources": list(outcome.sources),
                    "model_id": outcome.model_id,
                },
            )
            await session.commit()
            return {
                "owner_user_id": str(author.user_id),
                "tenant_id": str(author.tenant_id),
                **outcome.as_payload(),
            }

    return _run(_task())
