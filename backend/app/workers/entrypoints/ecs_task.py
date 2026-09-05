"""Entry point for the on-demand Fargate task that runs long work.

Run as `python -m app.workers.entrypoints.ecs_task`. The task to run arrives in
the container environment, put there by `readypick-assessment-trigger` as a
container override on `ecs:RunTask`.

THE PROCESS EXITS WHEN THE WORK IS DONE, AND THAT IS THE BILLING MODEL
----------------------------------------------------------------------
There is no loop here, nothing polls, and nothing waits for more work. The
Fargate task stops when this process ends, and that is when the meter stops.
A worker that stayed alive for the next job would be the always-on pool this
architecture exists to remove.

THE EXIT CODE IS THE RESULT
---------------------------
Zero on success, non-zero on failure, because that is the only channel a
stopped ECS task has. `DescribeTasks` reports the container exit code, the
CloudWatch alarm on failed assessment-agent tasks counts non-zero exits, and
the operator reading either of them is reading the truth rather than a log line
that says the process started.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

from app.workers.entrypoints import bootstrap

#: The container override the trigger Lambda writes. One variable carrying the
#: whole dispatch payload rather than one variable per argument: the payload
#: shape is already defined once, in `dispatch.payload_for`, and spreading it
#: across ad-hoc environment variables would be a second definition of it that
#: drifts the first time a task grows an argument.
TASK_ENV = "READYPICK_TASK"

logger = logging.getLogger(__name__)


def _payload() -> dict[str, Any]:
    raw = os.environ.get(TASK_ENV)
    if not raw:
        raise SystemExit(
            f"{TASK_ENV} is not set. This container runs one dispatched task "
            "and has nothing to do without one."
        )
    try:
        body = json.loads(raw)
    except ValueError as exc:
        raise SystemExit(f"{TASK_ENV} is not valid JSON: {exc}") from exc
    if not isinstance(body, dict) or not body.get("task"):
        raise SystemExit(f"{TASK_ENV} must carry a 'task' name")
    return body


def main() -> int:
    bootstrap()
    from app.workers.runtime import run_task

    body = _payload()
    logger.info(
        "ecs_task.start task=%s run_id=%s", body.get("task"), body.get("run_id")
    )
    try:
        run_task(body)
    except Exception:
        # Already recorded against the run and logged with its traceback by
        # `run_task`. Repeating the traceback here would double every failure
        # in CloudWatch; the exit code is what this layer adds.
        logger.error("ecs_task.failed task=%s", body.get("task"))
        return 1
    logger.info("ecs_task.done task=%s", body.get("task"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
