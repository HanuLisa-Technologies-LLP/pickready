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

from app.core.logging import configure_logging as core_configure_logging

def configure_logging() -> None:
    """Make sure records actually reach the log, in the SAME SHAPE as the API.

    ONE IMPLEMENTATION, AND THIS IS THE DELEGATION TO IT. This function used to
    configure logging itself, with a plain
    `%(asctime)s %(levelname)s %(name)s %(message)s` formatter, while the API
    process configured structlog with a JSON renderer. Two implementations of
    one concept, and the consequence was operational rather than cosmetic: an
    API line and the worker line for the same piece of work came out in
    different formats, so no log query could join them and a correlation id
    could not be followed across the boundary it exists to cross.

    `app.core.logging.configure_logging` is now the only one, and it carries
    both of the properties this function existed to guarantee:

      * IT SETS THE ROOT LEVEL FROM `LOG_LEVEL`, DEFAULTING TO INFO. The Lambda
        runtime leaves the root at WARNING unless told otherwise, so every
        `logger.info` about a dispatch, a retry or a completed task would be
        dropped, and the INFO lines ARE the operational record of what ran.
      * IT REPLACES THE ROOT HANDLER LIST RATHER THAN APPENDING. That is what
        keeps the old "only outside Lambda" guard unnecessary: the runtime
        installs its own handler, and appending a second one duplicated every
        line in CloudWatch. Replacing cannot.

    It is idempotent, so the `_configured` latch that used to live here is gone
    rather than duplicated.
    """
    core_configure_logging()


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
