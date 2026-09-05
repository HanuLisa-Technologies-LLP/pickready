"""Process entry points for background work.

Three doors, one room: the generic worker Lambda, the on-demand Fargate task,
and the two request/response agent handlers. All of them execute through
`app.workers.runtime`, so none of them owns any behaviour of its own beyond
reading its platform's event shape and reporting its platform's result.

`bootstrap()` is what every one of them calls first. It configures logging and
loads this function's secrets into the environment, in that order, because a
failure in the second is something you want to be able to read.
"""
from __future__ import annotations

import logging
import os

_configured = False


def configure_logging() -> None:
    """Make sure INFO records actually reach the log, once per process.

    The Lambda runtime installs its own root handler and leaves the root level
    at WARNING unless told otherwise, so every `logger.info` this codebase
    writes about a dispatch, a retry or a completed task would be dropped. The
    tasks' INFO lines are the operational record of what ran, so losing them
    turns a worker into a black box that only speaks when it fails.

    `LOG_LEVEL` stays the operator's override, and it is read here rather than
    through `Settings` so logging is configured before anything that could fail
    while validating configuration.
    """
    global _configured
    if _configured:
        return
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        # Only outside Lambda: the runtime installs its own handler, and adding
        # a second one duplicates every line in CloudWatch.
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
    _configured = True


def bootstrap() -> None:
    """What every entry point does before anything else.

    Logging first, then secrets, and the order matters: a secret that cannot be
    read raises, and an unconfigured logger would swallow the line that says
    which one.

    On ECS this loads nothing and returns: the agent has already injected the
    task definition's secrets into the environment. On Lambda it fetches them,
    because Lambda has no injection mechanism that does not also put the value
    in the console. See `app.workers.secrets_bootstrap`.
    """
    configure_logging()

    from app.workers.secrets_bootstrap import load_into_environment

    load_into_environment()
