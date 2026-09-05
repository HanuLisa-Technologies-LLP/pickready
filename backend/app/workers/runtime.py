"""The one place a background task is actually executed.

Every entry point converges here: the generic worker Lambda, the on-demand
Fargate task, and the in-process thread the docker-compose stack uses. There is
one implementation of "resolve a name, open a session, run it, retry it, record
what happened", so a task cannot behave differently depending on which door it
came through.

THE RETRY LOOP PREDICTS, IT DOES NOT MERELY OBSERVE
---------------------------------------------------
Before starting attempt N+1 the loop asks whether that attempt could FINISH
inside the remaining budget, using the longest attempt seen so far as the
estimate. `elapsed >= deadline` is the check that sounds right and is not: a
task whose attempts take eighty seconds under a hundred-second budget starts a
second attempt at eighty seconds and is killed by the platform at a hundred,
having thrown away the work of both. This is the same rule `agent_loop` and
`llm_router` already state, applied to the outer loop.

THERE IS NO SOFT TIME LIMIT ANY MORE, AND THAT IS A SIMPLIFICATION
------------------------------------------------------------------
Celery had two ceilings: a soft one that raised inside the task and a hard one
that killed it. The soft limit existed to turn a hang into a retryable
exception, and the lesson this codebase then recorded was that retrying a
timeout is exactly wrong, so `SoftTimeLimitExceeded` had to be added to a
no-retry exclusion list to undo the thing the soft limit was for.

Now the ceiling belongs to the platform, and the two platforms differ:

  Lambda  a hard function timeout. A task that outruns it is killed, and no
          retry is attempted, because the retry loop lives inside the
          invocation that was killed. The exclusion list is gone because
          nothing needs excluding: a task that could not finish in its budget
          will not finish in another one.

  Fargate NO ceiling. An on-demand task runs until its process exits, and
          `stopTimeout` is a SIGTERM grace period rather than a limit on the
          work. `DEFAULT_BUDGET_SECONDS` below is a self-imposed bound on the
          RETRY LOOP for that case, and it never cuts an attempt short.

WHAT A FAILURE COSTS
--------------------
The final failure is recorded in the run status and logged, and then it is
RE-RAISED. The Lambda invocation fails, which is what makes the function's
error metric move, the CloudWatch alarm fire and the on-failure destination
publish. Swallowing it here would leave the platform believing every
invocation succeeded, which is the reporting failure this product has already
been bitten by from the other direction.
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.workers import status
from app.workers.registry import TaskSpec, resolve

logger = logging.getLogger(__name__)

#: Used when the caller cannot say how much time is left, which is the Fargate
#: entry point: an on-demand task has NO platform-imposed ceiling. It runs until
#: its process exits, and `stopTimeout` is a SIGTERM grace period rather than a
#: limit on the work.
#:
#: So this is not a mirror of a platform bound, it is a self-imposed one, and it
#: exists for a narrow purpose: to stop the RETRY LOOP running for ever. A
#: single attempt is never cut short by it, because the check is made before an
#: attempt starts and never during one. Roughly an hour, which is far longer
#: than the slowest legitimate chain and short enough that a task retrying a
#: permanent failure cannot bill indefinitely.
DEFAULT_BUDGET_SECONDS = 3300.0


class PermanentTaskFailure(Exception):
    """This will fail the same way every time. Do not spend a retry on it.

    Raised by a task that has determined the input is wrong rather than the
    world being briefly unavailable: a missing Company DNA artifact, a row that
    no longer exists, an argument that does not parse. The distinction matters
    because the alternative is five backoff attempts against a condition no
    amount of waiting changes, producing five log lines that read like a bug in
    the task rather than a fact about its input.
    """


@dataclass
class TaskContext:
    """Handed to a task declared with `bind=True`, mirroring Celery's `self`.

    Carries the two things a task legitimately needs to know about its own
    execution: which run it is (so it can publish progress a screen is polling)
    and which attempt this is (so a delivery task can audit the terminal
    failure rather than the intermediate ones).
    """

    run_id: str
    name: str
    attempt: int
    max_attempts: int

    @property
    def is_final_attempt(self) -> bool:
        return self.attempt >= self.max_attempts

    def publish(self, payload: dict[str, Any]) -> None:
        """Record in-flight progress for the polling endpoint.

        Never raises into the work. A progress display that can fail the task
        it is describing is a strictly worse trade than one that goes blank,
        which is the rule `matching_progress.Progress` already states.
        """
        try:
            _run(status.write(self.run_id, status.STATE_PROGRESS, payload))
        except Exception:  # noqa: BLE001 -- see above
            logger.debug("taskrun.publish_failed run_id=%s", self.run_id, exc_info=True)


# -- Session helper ----------------------------------------------------------


@asynccontextmanager
async def worker_session():
    """Fresh engine + session for one task run, disposed on exit.

    A fresh engine per run rather than a module-level one: a Lambda execution
    environment is frozen between invocations, and a pooled asyncpg connection
    does not survive the freeze. It comes back looking healthy and fails on
    first use, which is the worst version of a broken connection because it
    fails inside the task rather than at connect time.

    ASSUMPTION (unchanged from the Celery worker this replaces): background
    tasks are trusted backend processes that legitimately operate across
    tenants (databank matching spans `tenant_id IS NULL` rows), so the session
    runs with `app.bypass_rls = 'on'`, the same escape hatch the RLS policies
    define for the audit-logged super-admin path. Tenant scoping inside a task
    is done explicitly per query where a tenant is known.
    """
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            # false => session-level (not transaction-local): survives commits.
            await session.execute(
                text("SELECT set_config('app.bypass_rls', 'on', false)")
            )
            yield session
    finally:
        await engine.dispose()


def _run(coro):
    """Run a coroutine to completion from sync task code.

    `asyncio.run` rather than a reused loop: task bodies are synchronous
    functions by design, so the API process never has to import what a worker
    imports, and a loop that outlived one run would carry its engine with it.
    """
    return asyncio.run(coro)


# -- Execution ---------------------------------------------------------------


def _max_attempts(spec: TaskSpec) -> int:
    """The attempt cap, resolved at RUN time rather than import time.

    Delivery keeps its operator knob: `settings.delivery_max_retries` is what
    the Celery task consulted, and reading it here rather than freezing it into
    the registry means changing it is still an environment change rather than a
    deploy.
    """
    if spec.max_attempts_setting:
        configured = getattr(get_settings(), spec.max_attempts_setting, None)
        if isinstance(configured, int) and configured >= 0:
            return configured + 1
    return spec.max_attempts


def _backoff(spec: TaskSpec, attempt: int) -> float:
    return min(spec.backoff_seconds * (2 ** (attempt - 1)), spec.backoff_max_seconds)


def run_task(
    body: dict[str, Any],
    *,
    remaining_seconds: Callable[[], float] | None = None,
) -> Any:
    """Execute one dispatched task. The only path to a task body.

    `body` is the wire format `dispatch.payload_for` produces.
    `remaining_seconds` is how much of the platform's budget is left, which the
    Lambda entry point wires to `context.get_remaining_time_in_millis`.
    """
    name = body.get("task")
    if not isinstance(name, str) or not name:
        raise PermanentTaskFailure("dispatched payload has no task name")
    run_id = str(body.get("run_id") or "")
    args = list(body.get("args") or [])
    kwargs = dict(body.get("kwargs") or {})

    spec = resolve(name)
    cap = _max_attempts(spec)
    started = time.monotonic()
    longest = 0.0
    last_error: BaseException | None = None

    for attempt in range(1, cap + 1):
        ctx = TaskContext(run_id=run_id, name=name, attempt=attempt, max_attempts=cap)
        call_args = (ctx, *args) if spec.bind else tuple(args)
        attempt_started = time.monotonic()
        try:
            result = spec.fn(*call_args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 -- classified immediately below
            longest = max(longest, time.monotonic() - attempt_started)
            last_error = exc
            retryable = isinstance(exc, spec.retry_on) and not isinstance(
                exc, PermanentTaskFailure
            )
            if not retryable or attempt >= cap:
                break
            delay = _backoff(spec, attempt)
            budget = (
                remaining_seconds()
                if remaining_seconds is not None
                else DEFAULT_BUDGET_SECONDS - (time.monotonic() - started)
            )
            # Predictive, not observational. See the module docstring.
            if delay + longest >= budget:
                logger.warning(
                    "taskrun.retry_abandoned task=%s run_id=%s attempt=%d "
                    "delay=%.1f longest=%.1f budget=%.1f",
                    name, run_id, attempt, delay, longest, budget,
                )
                break
            logger.warning(
                "taskrun.retrying task=%s run_id=%s attempt=%d/%d error=%s delay=%.1f",
                name, run_id, attempt, cap, type(exc).__name__, delay,
            )
            time.sleep(delay)
            continue

        elapsed = time.monotonic() - attempt_started
        payload = result if isinstance(result, dict) else {"result": result}
        if run_id:
            _run(status.write(run_id, status.STATE_SUCCESS, payload))
        logger.info(
            "taskrun.succeeded task=%s run_id=%s attempt=%d elapsed=%.1fs",
            name, run_id, attempt, elapsed,
        )
        return result

    assert last_error is not None  # the loop only breaks after an exception
    if run_id:
        # The exception CLASS NAME, never its message. A message can quote a
        # row, and this payload is read by a recruiter's browser.
        _run(
            status.write(
                run_id,
                status.STATE_FAILURE,
                {},
                error=type(last_error).__name__,
            )
        )
    logger.error(
        "taskrun.failed task=%s run_id=%s error=%s",
        name, run_id, type(last_error).__name__,
        exc_info=last_error,
    )
    raise last_error
