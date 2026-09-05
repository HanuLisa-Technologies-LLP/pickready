"""Every registered task must be able to RUN, not merely to register.

`@task` binds a name to a function at import time and never looks inside the
body, so a task whose body imports a module that no longer exists registers
perfectly, appears in the registry, is schedulable, and raises
`ModuleNotFoundError` the first time something dispatches it. The failure lands
in a log the caller never reads, and a schedule keeps firing it every hour.

That is not hypothetical: `pickready.probe_llm_models` did
`from app.scripts.probe_llm_models import probe` for the whole period after the
single-vendor consolidation deleted that module (claude.md Part B: "`llm_capacity.py`
... and `scripts/probe_llm_models.py` are deleted"), with a beat entry firing it
every 3600 seconds. Nothing in the suite touched it, because nothing in the
suite ever called it.

The checks below catch different halves:

  * the deferred imports inside every task body resolve, which importing the
    registry alone does NOT prove;
  * every scheduled entry names a task that exists, which catches a schedule
    left pointing at a task somebody deleted; and
  * every task declares a route, a retry policy that terminates, and a body
    whose signature matches how the runtime will call it.

This module replaces `test_celery_task_imports.py`. The transport changed on
2026-09-04; the failure mode it guards did not.
"""
from __future__ import annotations

import ast
import importlib
import inspect
import pathlib

import pytest

# Imported for its SIDE EFFECT: registration happens at import.
import app.workers.tasks  # noqa: F401
from app.workers.registry import Route, all_specs, names, resolve
from app.workers.schedule import SCHEDULE

WORKERS = pathlib.Path(__file__).resolve().parents[1] / "app" / "workers"

TASK_PREFIX = "pickready."


def test_the_task_registry_is_not_empty() -> None:
    """A guard on the guard: if the import wiring changed and the registry came
    back empty, every assertion below would pass by vacuum."""
    assert len(names()) >= 20


def test_every_task_name_is_namespaced() -> None:
    """The prefix is what makes a dispatch payload self-describing in a log and
    in a CloudWatch metric filter."""
    stray = [name for name in names() if not name.startswith(TASK_PREFIX)]
    assert not stray, f"task name(s) outside the {TASK_PREFIX!r} namespace: {stray}"


@pytest.mark.parametrize(
    "module_name",
    [
        "app.workers.tasks",
        "app.workers.dispatch",
        "app.workers.runtime",
        "app.workers.entrypoints.lambda_worker",
        "app.workers.entrypoints.ecs_task",
        "app.workers.entrypoints.agents",
    ],
)
def test_every_deferred_import_in_a_worker_module_resolves(module_name: str) -> None:
    """Import every module a worker module imports lazily inside a function.

    Task bodies import lazily on purpose (the API process must not pull the
    worker's dependencies), which is exactly why a stale one survives to
    production. Walking the AST reaches them without executing a single task.
    """
    module = importlib.import_module(module_name)
    source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            targets.add(node.module)
        elif isinstance(node, ast.Import):
            targets.update(alias.name for alias in node.names)

    unresolved: list[str] = []
    for target in sorted(targets):
        if not target.startswith("app."):
            continue
        try:
            importlib.import_module(target)
        except ImportError as exc:
            unresolved.append(f"{target}: {exc}")
    assert not unresolved, (
        f"{module_name} imports modules that do not exist: {unresolved}"
    )


def test_every_scheduled_entry_names_a_registered_task() -> None:
    """A schedule pointing at a name nothing registered is a silent no-op at
    best and an hourly traceback at worst."""
    registered = set(names())
    orphans = {
        entry.rule: entry.task for entry in SCHEDULE if entry.task not in registered
    }
    assert not orphans, f"scheduled rules name unregistered tasks: {orphans}"


def test_every_scheduled_task_runs_without_arguments() -> None:
    """A scheduler sends no arguments, so a sweep that needs one can never run.

    Celery would have accepted the schedule entry and failed at invocation with
    a TypeError in a worker log; there is no worker log to read now, so the
    mismatch is caught here instead.
    """
    needy = []
    for entry in SCHEDULE:
        spec = resolve(entry.task)
        signature = inspect.signature(spec.fn)
        required = [
            parameter
            for parameter in signature.parameters.values()
            if parameter.default is inspect.Parameter.empty
            and parameter.kind
            in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
        ]
        if spec.bind:
            required = required[1:]
        if required:
            needy.append(f"{entry.task}{signature}")
    assert not needy, f"scheduled task(s) require arguments a schedule cannot send: {needy}"


def test_every_scheduled_rule_name_is_unique() -> None:
    """The rule is addressed by name. Two entries sharing one would leave a
    single rule firing whichever task was applied last, silently."""
    rules = [entry.rule for entry in SCHEDULE]
    assert len(rules) == len(set(rules)), f"duplicate rule names in {rules}"


def test_every_retry_policy_terminates() -> None:
    """An unbounded retry is a task that can occupy its function for ever.

    Celery's `max_retries=None` meant "use the default", which is easy to read
    as "no limit". Here the cap is an integer and it is asserted to be one.
    """
    offenders = []
    for spec in all_specs():
        if spec.max_attempts < 1 or spec.max_attempts > 10:
            offenders.append(f"{spec.name}: max_attempts={spec.max_attempts}")
        if spec.backoff_max_seconds > 300:
            offenders.append(
                f"{spec.name}: backoff_max_seconds={spec.backoff_max_seconds}"
            )
    assert not offenders, offenders


def test_every_bound_task_takes_its_context_first() -> None:
    """`bind=True` makes the runtime prepend a `TaskContext`. A task that
    declares it and does not take it fails on its first real dispatch with an
    argument-count error, which is the kind of break a registry can catch."""
    wrong = []
    for spec in all_specs():
        if not spec.bind:
            continue
        first = next(iter(inspect.signature(spec.fn).parameters), None)
        if first is None or "ctx" not in first:
            wrong.append(f"{spec.name}: first parameter is {first!r}")
    assert not wrong, wrong


def test_every_route_is_a_real_route() -> None:
    assert {spec.route for spec in all_specs()} <= {Route.LAMBDA, Route.ECS}


def test_the_deleted_probe_task_is_gone() -> None:
    """Delete rather than deprecate (spec-doc6 section 10.1 rule 4).

    Named rather than left to the sweep above, so a reintroduction fails with
    the reason attached instead of as a generic import error.
    """
    assert "pickready.probe_llm_models" not in names()
    assert not [e for e in SCHEDULE if "probe" in e.rule]
    assert "probe_llm_models" not in (WORKERS / "tasks.py").read_text(encoding="utf-8")


def test_celery_is_gone_from_the_worker_package() -> None:
    """Delete rather than deprecate, again.

    A leftover `celery_app.py` would still import, still register tasks under
    its own registry, and give a reader two answers to "where does this task
    run". The dependency is gone from requirements too, so an import of it
    would be an outright failure rather than a second code path.
    """
    assert not (WORKERS / "celery_app.py").exists()
    for path in WORKERS.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "import celery" not in source, path
        assert "from celery" not in source, path

    requirements = (
        pathlib.Path(__file__).resolve().parents[1] / "requirements.txt"
    ).read_text(encoding="utf-8")
    for line in requirements.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith("#") or not stripped:
            continue
        assert not stripped.startswith(("celery", "flower", "kombu")), line
