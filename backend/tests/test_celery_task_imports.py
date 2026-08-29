"""Every registered Celery task must be able to RUN, not merely to register.

`@celery_app.task` binds a name to a function at import time and never looks
inside the body, so a task whose body imports a module that no longer exists
registers perfectly, appears in `celery_app.tasks`, is schedulable by beat, and
raises `ModuleNotFoundError` the first time a worker picks the message up. The
worker logs the failure, the caller never hears about it, and the beat schedule
keeps queueing it every hour.

That is not hypothetical: `pickready.probe_llm_models` did
`from app.scripts.probe_llm_models import probe` for the whole period after the
single-vendor consolidation deleted that module (CLAUDE.md Part B: "`llm_capacity.py`
... and `scripts/probe_llm_models.py` are deleted"), with a beat entry firing it
every 3600 seconds. Nothing in the suite touched it, because nothing in the
suite ever called it.

Two checks, and they catch different halves:

  * the deferred imports inside every task body resolve, which is what an
    `import celery_app` alone does NOT prove; and
  * every `beat_schedule` entry names a task the app actually registered, which
    is what catches a schedule left pointing at a task somebody deleted.
"""
from __future__ import annotations

import ast
import importlib
import pathlib

import pytest

# Imported for its SIDE EFFECT: `celery_app` declares `include=["app.workers.tasks"]`
# and Celery honours that lazily, when a worker starts. Without this the registry
# is empty and every assertion below passes by saying nothing.
import app.workers.tasks  # noqa: F401
from app.workers.celery_app import celery_app

WORKERS = pathlib.Path(__file__).resolve().parents[1] / "app" / "workers"

#: Registered under this prefix, i.e. ours. Celery's own bookkeeping tasks
#: (`celery.chord`, `celery.backend_cleanup` and friends) live in the same
#: registry and are not this project's to vouch for.
TASK_PREFIX = "pickready."


def _project_tasks() -> list[str]:
    return sorted(n for n in celery_app.tasks if n.startswith(TASK_PREFIX))


def test_the_task_registry_is_not_empty() -> None:
    """A guard on the guard: if the import wiring changed and the registry came
    back empty, every assertion below would pass by vacuum."""
    assert len(_project_tasks()) >= 20


@pytest.mark.parametrize("module_name", ["app.workers.tasks", "app.workers.celery_app"])
def test_every_deferred_import_in_a_worker_module_resolves(module_name: str) -> None:
    """Import every module a worker module imports lazily inside a function.

    Task bodies import lazily on purpose (the API process must not pull worker
    dependencies), which is exactly why a stale one survives to production.
    Walking the AST reaches them without executing a single task.
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
    assert not unresolved, f"{module_name} imports modules that do not exist: {unresolved}"


def test_every_beat_entry_names_a_registered_task() -> None:
    """A schedule pointing at a name nothing registered is a silent no-op at
    best and an hourly traceback at worst."""
    registered = set(celery_app.tasks)
    orphans = {
        entry: config["task"]
        for entry, config in (celery_app.conf.beat_schedule or {}).items()
        if config["task"] not in registered
    }
    assert not orphans, f"beat entries name unregistered tasks: {orphans}"


def test_the_deleted_probe_task_is_gone() -> None:
    """Delete rather than deprecate (spec-doc6 §10.1 rule 4).

    Named rather than left to the sweep above, so a reintroduction fails with
    the reason attached instead of as a generic import error.
    """
    assert "pickready.probe_llm_models" not in celery_app.tasks
    assert "probe-llm-models" not in (celery_app.conf.beat_schedule or {})
    for path in (WORKERS / "tasks.py", WORKERS / "celery_app.py"):
        assert "probe_llm_models" not in path.read_text(encoding="utf-8"), path.name
