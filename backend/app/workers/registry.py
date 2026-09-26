"""The task registry: every background task, and where it runs.

WHY THIS EXISTS
---------------
Celery kept two facts in two places -- the task name lived on a decorator and
the queue it went to lived in `task_routes` on the app object -- and keeping
them in step was a convention rather than a mechanism. A task added without a
routing entry silently fell through to the default queue, which is how AI work
and email delivery ended up sharing one pool and one wedged question-generation
run stopped a staff invitation from being sent (2026-08-01).

Here the name, the implementation and the destination are ONE declaration, so a
task cannot exist without saying where it runs, and `dispatch` reads the answer
off the registry instead of off a second table somebody has to remember.

THE ROUTE IS THE COST DECISION, NOT A PREFERENCE
------------------------------------------------
`Route.LAMBDA` is work measured in seconds that must not wait behind anything:
sending an email, parsing one resume, a reconciliation sweep. It runs in the
`readypick-task-worker` Lambda, which is billed per invocation and has no
standing capacity for a slow task to occupy.

`Route.ECS` is work measured in minutes with a real ceiling risk: scoring a
whole candidate pool, compiling a Tatva matrix through seven stages, writing a
PRISM report. It runs as one on-demand Fargate task per dispatch, which starts
when the work arrives and stops when the process exits. Lambda's fifteen-minute
ceiling is the hard reason for the split: a matching run over a large pool
legitimately outruns it, and a task killed at a ceiling leaves half a pool
scored with nothing saying so.

RETRIES LIVE HERE, IN ONE PLACE
-------------------------------
`max_attempts` and `backoff_seconds` replace Celery's `autoretry_for` /
`retry_backoff` / `max_retries`. They are declared per task because the right
answer differs per task and always did: delivery is worth retrying because SMTP
fails transiently, and a SWOT generation is not, because a failed draft is a
state the team retries from the tab and a second model call would only repeat
it.

The retry loop runs INSIDE the invocation (see `runtime.run_task`), and the
Lambda's own asynchronous retry is set to zero in Terraform. Two retry
mechanisms stacked would multiply: three in-process attempts under two platform
attempts is nine sends of one email, and a duplicate invitation is worse than a
failure somebody can see.

EVERY TASK SAYS HOW IT REACHES DATA
-----------------------------------
`rls` is a REQUIRED keyword with no default, so a task that does not declare
its row-level-security scope is refused at import rather than quietly running
across every tenant. Two values, and only two:

  "tenant"  the body opens `runtime.tenant_worker_session(tenant_id)`, so the
            Postgres policy, not a WHERE clause, is what keeps the run inside
            one customer. A task is declared this way only once a test has run
            its body against real Postgres under the RLS role and read back the
            same rows the bypass session wrote (`tests/test_worker_tenant_session`).
  "bypass"  the body keeps `runtime.worker_session()`, and `rls_reason` says WHY
            in words: it sweeps every tenant, it reads a tenant-free table
            (`candidates`, `profiles`, `candidate_employments`, the consent
            tables), or it has not yet been proven under a tenant session. A
            bypass with no reason is refused, because an undeclared escape hatch
            is indistinguishable from an accidental one.

The declaration is BINDING, not decorative: the same test walks every body and
fails a "tenant" task that opens the bypass session, and a "bypass" task that
opens the tenant one.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Iterator, Literal

#: The two row-level-security scopes a task may declare. See the module docstring.
RLS_TENANT = "tenant"
RLS_BYPASS = "bypass"
RLS_SCOPES = frozenset({RLS_TENANT, RLS_BYPASS})
RlsScope = Literal["tenant", "bypass"]


class Route(str, Enum):
    """Where a task's work is performed. See the module docstring."""

    LAMBDA = "lambda"
    ECS = "ecs"


class UnknownTask(KeyError):
    """A name with no registered implementation.

    Raised rather than ignored. Celery accepted an unknown name at publish time
    and only discovered it in a worker log, so a beat entry pointing at a
    deleted task queued a message every hour that nothing could ever run.
    """


@dataclass(frozen=True)
class TaskSpec:
    name: str
    fn: Callable[..., Any]
    route: Route
    #: Total attempts INCLUDING the first. 1 means "run it once, never retry".
    max_attempts: int
    #: A `Settings` attribute holding a RETRY count, if this task's cap is an
    #: operator knob rather than a code decision. Resolved at run time as
    #: `value + 1` attempts, so changing it stays an environment change.
    max_attempts_setting: str | None
    #: Base for exponential backoff between attempts, in seconds. Attempt N
    #: sleeps `backoff_seconds * 2 ** (N - 1)`, capped by `backoff_max_seconds`.
    #: Setting both equal gives the fixed delay the delivery tasks want.
    backoff_seconds: float
    backoff_max_seconds: float
    #: Which exceptions are worth another attempt. Narrow by default is wrong
    #: here (a transient database blip is worth retrying and has no shared base
    #: class), so the default is broad and the tasks that must NOT retry a
    #: whole class of failure say so, exactly as `dont_autoretry_for` did.
    retry_on: tuple[type[BaseException], ...]
    #: True when the implementation takes a `runtime.TaskContext` as its first
    #: argument. The same meaning Celery's `bind=True` had, for the same two
    #: users: publishing progress, and knowing which attempt this is.
    bind: bool
    #: A one-line description, used by the operator-facing task listing.
    summary: str
    #: How the body reaches data: "tenant" or "bypass" (see the module
    #: docstring). `task()` refuses a registration without one; the default
    #: exists only so a hand-built spec in a runtime test need not invent a
    #: scope, and None there reads as "undeclared", never as bypass.
    rls: str | None = None
    #: Why a "bypass" task needs the escape hatch, in words. Empty for "tenant".
    rls_reason: str = ""


_REGISTRY: dict[str, TaskSpec] = {}


def task(
    *,
    name: str,
    route: Route,
    rls: RlsScope,
    rls_reason: str = "",
    max_attempts: int = 1,
    max_attempts_setting: str | None = None,
    backoff_seconds: float = 2.0,
    backoff_max_seconds: float = 60.0,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    bind: bool = False,
    summary: str = "",
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register `fn` as the implementation of `name`.

    The decorated function is returned UNWRAPPED. A task body is an ordinary
    function and stays directly callable, which is what lets the test suite
    exercise one without a dispatcher, a broker or a mock in the way.
    """

    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        if name in _REGISTRY:
            raise ValueError(f"duplicate task name: {name}")
        if max_attempts < 1:
            raise ValueError(f"{name}: max_attempts must be at least 1")
        if rls not in RLS_SCOPES:
            raise ValueError(
                f"{name}: rls must be one of {sorted(RLS_SCOPES)}, got {rls!r}"
            )
        if rls == RLS_BYPASS and not rls_reason.strip():
            raise ValueError(
                f"{name}: rls='bypass' needs an rls_reason saying why this task "
                "may read across tenants"
            )
        if rls == RLS_TENANT and rls_reason.strip():
            raise ValueError(
                f"{name}: rls='tenant' takes no rls_reason; the tenant session "
                "is the rule, only the escape hatch needs a justification"
            )
        _REGISTRY[name] = TaskSpec(
            name=name,
            fn=fn,
            route=route,
            max_attempts=max_attempts,
            max_attempts_setting=max_attempts_setting,
            backoff_seconds=backoff_seconds,
            backoff_max_seconds=backoff_max_seconds,
            retry_on=retry_on,
            bind=bind,
            summary=summary or (fn.__doc__ or "").strip().split("\n")[0],
            rls=rls,
            rls_reason=rls_reason.strip(),
        )
        return fn

    return decorate


def resolve(name: str) -> TaskSpec:
    """The spec for `name`, or `UnknownTask`.

    Importing `app.workers.tasks` is what populates the registry, and it is
    imported here lazily rather than at module import so the dispatch side of
    this package does not drag the worker's dependencies into the API process.
    """
    if name not in _REGISTRY:
        import app.workers.tasks  # noqa: F401  -- registration side effect
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise UnknownTask(name) from exc


def all_specs() -> Iterator[TaskSpec]:
    import app.workers.tasks  # noqa: F401  -- registration side effect

    yield from sorted(_REGISTRY.values(), key=lambda spec: spec.name)


def names() -> list[str]:
    return [spec.name for spec in all_specs()]
