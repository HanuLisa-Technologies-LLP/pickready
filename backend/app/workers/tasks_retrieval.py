"""The semantic index repair task.

Its own module rather than a hunk in `workers/tasks.py`, for the reason the
2026-09-24 carve gave the assessment tasks theirs: several phases edit
`tasks.py` in parallel, and a task that lives in a file nobody else touches is
a task whose registration cannot be lost in a merge. `workers/tasks.py` imports
this module, which is what registers it (`registry.resolve` imports `tasks`
and nothing else).
"""
from __future__ import annotations

import logging

from app.core.config import get_settings
from app.workers.registry import Route, task
from app.workers.runtime import (
    worker_session as _worker_session,
    _run,
)

logger = logging.getLogger(__name__)


@task(
    name="pickready.repair_semantic_index",
    route=Route.LAMBDA,
    rls="bypass",
    rls_reason="a sweep: one run reads every tenant's rows",
)
def repair_semantic_index():
    """Hourly: re-embed chunks whose vector is missing, stale or the wrong width.

    The half `reconcile_context_index` cannot see. That sweep finds documents
    with NO chunks; this one finds chunks that exist and are not searchable by
    meaning: written with a NULL vector during an embedding outage, embedded by
    a retired model or text builder, or carrying a vector of the wrong width.
    `rag.repair` holds the predicate, the provenance stamp and the reasons.

    `Route.LAMBDA` because one pass is bounded by
    `retrieval_repair_sweep_batch` (default 200, two vendor round trips) and
    takes seconds. 0 PAUSES the sweep, which is the owner's lever on the one
    cost spike this ships with: every chunk written before 2026-09-24 has no
    model stamp and is re-embedded by the first passes.

    One transaction per pass. A pass that dies before its commit leaves every
    chunk as it was, and the next hour selects the same rows again, because
    the predicate asks the table rather than a checkpoint.
    """
    from app.services.rag import repair as rag_repair

    async def _task():
        limit = get_settings().retrieval_repair_sweep_batch
        async with _worker_session() as session:
            result = await rag_repair.repair(session, limit=limit)
            await session.commit()
        return {
            "selected": result.selected,
            "repaired": result.repaired,
            "superseded": result.superseded,
            "remaining_estimate": result.remaining_estimate,
            "degraded": result.degraded,
            "skipped": result.skipped,
        }

    return _run(_task())
