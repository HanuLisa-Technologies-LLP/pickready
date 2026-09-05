"""Hand a background task to whatever is going to run it.

This is the one replacement for `celery_app.send_task`. Every call site that
used to publish a message to a Redis broker now calls `dispatch(...)` and gets
back a run id it can hand to a client for polling.

THE THREE BACKENDS ARE THREE REAL DEPLOYMENTS, NOT A FALLBACK CHAIN
-------------------------------------------------------------------
`aws` invokes Lambda. `local` runs the task in a thread in this process, which
is what the docker-compose stack does because there is no Lambda on a laptop.
`record` accepts the dispatch, runs nothing, and remembers it, which is what
the test suite does because executing a task would make every route test depend
on a model provider.

Nothing falls back to anything. The backend is chosen once from configuration
and a failure inside it RAISES, so a caller that wants best-effort behaviour
wraps the call itself. That is deliberate and it is the behaviour the existing
call sites were already written against: several of them catch the enqueue
failure and degrade visibly (a skipped outreach recipient says it could not be
queued), and a dispatcher that swallowed the error would turn those into
silence.

WHY LONG WORK IS STILL ADDRESSED THROUGH A LAMBDA
-------------------------------------------------
`Route.ECS` does not call `ecs:RunTask` from here. It invokes
`readypick-assessment-trigger`, whose entire job is that one call. The reason is
least privilege: running a Fargate task means holding `iam:PassRole` over the
assessment agent's task role, and that is a privilege-escalation primitive,
since anything that can pass a role can run code as it. The API service has no
business holding it. A 128MB function that does nothing else can.

TIMEOUTS ARE EXPLICIT, BECAUSE THIS EXACT MISTAKE HAS BEEN MADE HERE BEFORE
--------------------------------------------------------------------------
Publishing to Redis had no timeout by default, so an unreachable broker did not
fail, it HUNG, which silently defeated every `try/except` around an enqueue,
because nothing was ever raised for the handler to catch. It was observed as a
management job that found thirty files, blocked forever on its first enqueue,
and was killed at the 900-second ceiling having written nothing.

botocore has the same shape of default (a 60-second connect timeout, and
retries on top). The client below sets both timeouts short and caps the
attempts, so a Lambda control-plane problem costs a request a few seconds and
an exception the caller can handle, never a hung worker.
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import dataclass
from typing import Any, Sequence

from app.core.config import get_settings
from app.workers.registry import Route, TaskSpec, resolve

logger = logging.getLogger(__name__)

BACKEND_AWS = "aws"
BACKEND_LOCAL = "local"
BACKEND_RECORD = "record"

#: The generic worker Lambda. One function runs every `Route.LAMBDA` task,
#: because they share an image, a role and a set of secrets, and one function
#: per task would multiply the deployment surface by thirty for no isolation
#: anybody asked for. The three agent functions are separate for the opposite
#: reason: they are request/response and sized differently.
WORKER_FUNCTION = "readypick-task-worker"

#: Invoked for `Route.ECS`. Calls `ecs:RunTask` and returns. See the docstring.
TRIGGER_FUNCTION = "readypick-assessment-trigger"


class DispatchError(RuntimeError):
    """The task could not be handed off. Raised, never swallowed."""


@dataclass(frozen=True)
class TaskHandle:
    """What the caller gets back.

    `id` is generated HERE rather than returned by the platform, so a request
    handler has the polling id before the invoke completes and an asynchronous
    invoke does not have to be waited on to learn it. Celery generated its task
    ids client-side for the same reason, so the contract the frontend already
    polls against is unchanged.
    """

    id: str
    name: str
    route: Route


@dataclass(frozen=True)
class RecordedDispatch:
    id: str
    name: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


_recorded: list[RecordedDispatch] = []
_recorded_lock = threading.Lock()
_client: Any = None


def backend() -> str:
    """The configured backend, validated once per call.

    `record` is refused in production outright. It is the mode that accepts
    work and does not do it, which is exactly the failure this product has been
    bitten by, so it may only ever be reached deliberately and never by a
    misread environment variable on a live service.
    """
    settings = get_settings()
    chosen = (settings.task_dispatch_backend or BACKEND_AWS).strip().lower()
    if chosen not in {BACKEND_AWS, BACKEND_LOCAL, BACKEND_RECORD}:
        raise DispatchError(f"unknown task dispatch backend: {chosen!r}")
    if chosen == BACKEND_RECORD and settings.is_production:
        raise DispatchError(
            "task_dispatch_backend=record accepts work without running it and "
            "is refused in production"
        )
    return chosen


def _lambda_client():
    """A boto3 Lambda client with bounded timeouts. See the module docstring."""
    global _client
    if _client is None:
        import boto3
        from botocore.config import Config

        settings = get_settings()
        _client = boto3.client(
            "lambda",
            region_name=settings.aws_region,
            config=Config(
                connect_timeout=5,
                read_timeout=10,
                retries={"max_attempts": 2, "mode": "standard"},
            ),
        )
    return _client


def reset_client() -> None:
    """Drop the cached client. Used by tests and after a credential change."""
    global _client
    _client = None


def payload_for(
    run_id: str, spec: TaskSpec, args: Sequence[Any], kwargs: dict
) -> dict:
    """The one wire format. Both entry points parse exactly this shape.

    Arguments are JSON, which is what Celery's `task_serializer="json"` already
    required, so nothing that was dispatchable before has become undispatchable.
    """
    return {
        "run_id": run_id,
        "task": spec.name,
        "args": list(args),
        "kwargs": dict(kwargs),
    }


def dispatch(
    name: str,
    args: Sequence[Any] | None = None,
    kwargs: dict[str, Any] | None = None,
) -> TaskHandle:
    """Hand `name` off to run, and return its handle.

    The signature mirrors `celery_app.send_task(name, args=[...])` so the call
    sites read the same and a reviewer can see that only the transport changed.
    """
    spec = resolve(name)
    args = tuple(args or ())
    kwargs = dict(kwargs or {})
    run_id = str(uuid.uuid4())
    body = payload_for(run_id, spec, args, kwargs)

    mode = backend()
    if mode == BACKEND_RECORD:
        with _recorded_lock:
            _recorded.append(
                RecordedDispatch(id=run_id, name=spec.name, args=args, kwargs=kwargs)
            )
        logger.info("dispatch.recorded task=%s run_id=%s", spec.name, run_id)
        return TaskHandle(id=run_id, name=spec.name, route=spec.route)

    if mode == BACKEND_LOCAL:
        _run_locally(body)
        return TaskHandle(id=run_id, name=spec.name, route=spec.route)

    function = TRIGGER_FUNCTION if spec.route is Route.ECS else WORKER_FUNCTION
    try:
        response = _lambda_client().invoke(
            FunctionName=function,
            InvocationType="Event",
            Payload=json.dumps(body).encode("utf-8"),
        )
    except Exception as exc:  # noqa: BLE001 -- re-raised as DispatchError below
        logger.exception(
            "dispatch.invoke_failed task=%s function=%s", spec.name, function
        )
        raise DispatchError(
            f"could not dispatch {spec.name}: {type(exc).__name__}"
        ) from exc

    accepted = int(response.get("StatusCode") or 0)
    if accepted != 202:
        # An asynchronous invoke answers 202 and nothing else. Anything here is
        # a refusal wearing a success shape, and treating it as accepted is how
        # work disappears behind a log line that says it was queued.
        raise DispatchError(
            f"{spec.name}: lambda {function} answered {accepted}, expected 202"
        )
    logger.info(
        "dispatch.invoked task=%s run_id=%s function=%s route=%s",
        spec.name,
        run_id,
        function,
        spec.route.value,
    )
    return TaskHandle(id=run_id, name=spec.name, route=spec.route)


def _run_locally(body: dict) -> None:
    """Run the task in a daemon thread, for the docker-compose stack.

    A thread rather than an inline call because the call sites are request
    handlers: running a matching pass inline would hold the request open for
    minutes and change the behaviour being developed against. Errors surface
    where they would in a deployed worker, in the log, because `run_task`
    records the failure itself and never re-raises past this boundary.
    """
    from app.workers.runtime import run_task

    def _target() -> None:
        try:
            run_task(body)
        except Exception:  # noqa: BLE001 -- a thread's exception has nowhere to go
            logger.exception("dispatch.local_run_failed task=%s", body.get("task"))

    threading.Thread(
        target=_target, name=f"task-{body.get('task')}", daemon=True
    ).start()


# -- Test-mode inspection ----------------------------------------------------
# Only meaningful under `record`. Exposed so a test can assert WHAT was
# dispatched, which is a stronger check than the monkeypatched broker it
# replaces: patching `send_task` with a lambda proved only that the call site
# ran, never that it named a task that exists.


def recorded() -> list[RecordedDispatch]:
    with _recorded_lock:
        return list(_recorded)


def recorded_names() -> list[str]:
    return [item.name for item in recorded()]


def clear_recorded() -> None:
    with _recorded_lock:
        _recorded.clear()
