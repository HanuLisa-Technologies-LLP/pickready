"""AWS Lambda entry point for `readypick-task-worker`.

One function runs every `Route.LAMBDA` task. The event is the wire format
`dispatch.payload_for` produces, and nothing else is accepted: a payload this
cannot read is a programming error, not a variant to be tolerated, and guessing
at it would run some other task than the one that was asked for.

WHY THE PLATFORM BUDGET IS PASSED INTO THE RETRY LOOP
-----------------------------------------------------
`context.get_remaining_time_in_millis()` is the only honest source for how long
this invocation has left. Without it the retry loop would be reasoning about a
budget it invented, and a delivery task with a sixty-second backoff would
happily start a retry with forty seconds left and be killed mid-send. Killed
mid-send is the expensive case: the SMTP conversation may already have been
accepted, so the retry that follows sends the message twice.

WHY THE SCHEDULED SWEEPS ARRIVE HERE TOO
----------------------------------------
An EventBridge Scheduler rule invokes this function with the same payload shape
a dispatch produces. That is deliberate: a sweep is an ordinary task, and
giving it its own entry point would mean the schedule could fire something the
registry does not have, which is exactly how a beat entry outlived the module
it called.
"""
from __future__ import annotations

import logging
from typing import Any

from app.workers.entrypoints import bootstrap
from app.workers.runtime import run_task

bootstrap()
logger = logging.getLogger(__name__)


def lambda_handler(event: Any, context: Any) -> dict[str, Any]:
    if not isinstance(event, dict) or not event.get("task"):
        # Raised, not returned. A malformed event that answers 200 is an
        # invocation the platform records as a success and nobody investigates.
        raise ValueError("task worker event must carry a 'task' name")

    def remaining() -> float:
        getter = getattr(context, "get_remaining_time_in_millis", None)
        if getter is None:
            # Only reachable outside Lambda (a local invoke, a test). The
            # runtime's own default budget applies instead of a fabricated one.
            from app.workers.runtime import DEFAULT_BUDGET_SECONDS

            return DEFAULT_BUDGET_SECONDS
        # A margin, so the last thing this invocation does is record the
        # failure rather than being killed while writing it.
        return max(0.0, (getter() / 1000.0) - 5.0)

    result = run_task(event, remaining_seconds=remaining)
    return {
        "task": event["task"],
        "run_id": event.get("run_id"),
        "result": result if isinstance(result, dict) else {"result": result},
    }
