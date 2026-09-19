"""The scoring lock: it must exclude across PROCESSES, or it excludes nothing.

RPN-AI-UP-001 named duplicate scoring as a P1, and the finding behind it was
that `pg_advisory_lock`, `FOR UPDATE` and `with_for_update` appeared nowhere in
the tree. `services/coalescing` is not a counter-example and says so itself: it
is in-process by design, and `Route.ECS` gives every dispatch its own container.

The properties below are the ones that would each independently make the lock
useless while leaving every single-process test green.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.services import locks

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
REPO_BACKEND = APP_ROOT.parent


def test_the_key_is_stable_across_processes() -> None:
    """A key computed in another interpreter must equal this one's.

    THIS IS THE TEST THAT MATTERS MOST, and it is the one an in-process
    assertion cannot make. Python salts `hash()` per process by default, so a
    lock key built on it would differ between two Fargate containers, each
    would take a lock nobody else was holding, and both would score. That
    failure is invisible in every test that computes both keys in one
    interpreter.

    A subprocess with an explicit and DIFFERENT `PYTHONHASHSEED` is what makes
    the assertion real rather than tautological.
    """
    subject = "3f9a6c21-0000-4000-8000-000000000abc"
    here = locks.advisory_key(locks.SCORING, subject)

    program = (
        "from app.services import locks;"
        f"print(locks.advisory_key(locks.SCORING, {subject!r}))"
    )
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = "12345"
    env["PYTHONPATH"] = str(REPO_BACKEND)
    out = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        cwd=str(REPO_BACKEND),
        env=env,
        check=True,
    )
    assert int(out.stdout.strip()) == here


def test_the_key_fits_a_postgres_advisory_lock_argument() -> None:
    """Signed 64-bit, or Postgres refuses the call outright.

    `pg_try_advisory_xact_lock` takes a bigint. A key one bit too wide raises
    at the database rather than failing to exclude, but it raises inside a
    scoring run, which is a worse place to find out.
    """
    for _ in range(200):
        key = locks.advisory_key(locks.SCORING, uuid.uuid4())
        assert -(2**63) <= key < 2**63, key


def test_the_namespace_changes_the_key() -> None:
    """Two features keying on one application id must not collide.

    Without the namespace in the hash, a future lock on the same link id would
    silently exclude scoring, and scoring would silently exclude it.
    """
    subject = uuid.uuid4()
    assert locks.advisory_key(locks.SCORING, subject) != locks.advisory_key(
        "some.other.feature", subject
    )


@pytest.mark.asyncio
async def test_a_second_session_cannot_take_a_held_lock() -> None:
    """Two connections, one subject: the second is refused, and freed on rollback.

    Two SEPARATE sessions rather than two calls on one, because
    `pg_try_advisory_xact_lock` is re-entrant within a transaction: asking twice
    on one connection returns true twice and would pass a test that proves
    nothing about two containers.
    """
    engine = create_async_engine(
        get_settings().database_url, pool_size=2, max_overflow=0
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    subject = str(uuid.uuid4())
    try:
        async with factory() as first, factory() as second:
            assert await locks.try_advisory_lock(first, locks.SCORING, subject) is True
            assert (
                await locks.try_advisory_lock(second, locks.SCORING, subject) is False
            ), "a second process took a lock the first was holding"

            # A DIFFERENT subject is unaffected: the lock is per application,
            # never a global scoring mutex. A global one would serialise every
            # candidate on the platform behind one run.
            assert (
                await locks.try_advisory_lock(second, locks.SCORING, str(uuid.uuid4()))
                is True
            )

            # Releasing the holder frees it. This is what makes the
            # transaction-scoped variant safe without a `finally`.
            await first.rollback()
            assert (
                await locks.try_advisory_lock(second, locks.SCORING, subject) is True
            ), "the lock outlived its transaction, which would strand the row"
    finally:
        await engine.dispose()


def test_the_scoring_task_takes_the_lock_before_it_spends_anything() -> None:
    """The lock must precede the credit check, the loads and the model chain.

    Asserted over the SOURCE rather than by running the task, because what is
    pinned here is ORDER, and a mock-based test would pass just as happily with
    the lock taken after `run_assessment` had already been paid for. Ordering is
    the whole value: a lock taken afterwards reports a duplicate instead of
    preventing one.
    """
    source = (APP_ROOT / "workers" / "tasks.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    target = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == "run_functional_assessment"
        ):
            target = node
            break
    assert target is not None, "run_functional_assessment is gone or was renamed"

    # CALL sites, never substrings. The first version of this test compared
    # `body.find(name)` positions and failed on `from
    # app.services.functional_assessment import run_assessment` -- the import
    # block, which necessarily precedes every statement in the body. A test
    # that cannot tell an import from a call would also have passed a body
    # that mentioned the lock only in a comment.
    def called_at(name: str) -> int | None:
        lines = [
            node.lineno
            for node in ast.walk(target)
            if isinstance(node, ast.Call)
            and (
                getattr(node.func, "attr", None) == name
                or getattr(node.func, "id", None) == name
            )
        ]
        return min(lines) if lines else None

    lock_line = called_at("try_advisory_lock")
    assert lock_line is not None, (
        "run_functional_assessment no longer CALLS the scoring lock. Five call "
        "sites dispatch it and each gets its own container; without this a "
        "duplicate is only caught by uq_functional_report_link, at COMMIT, "
        "after both runs have paid for the whole model chain."
    )
    for later in ("has_positive_balance", "run_assessment", "persist_skill_evidence"):
        line = called_at(later)
        assert line is None or lock_line < line, (
            f"the scoring lock is taken AFTER the call to {later}, so a "
            "duplicate run has already spent work by the time it is refused"
        )


def test_release_held_assessments_is_actually_scheduled() -> None:
    """Registered is not running, and this task was registered for a whole phase.

    Its only dispatchers were the two credit-grant call sites, so it repaired a
    hold that a top-up cleared and nothing else. A report lost to a dispatch
    that never arrived, or to a container killed mid-scoring, stayed lost, for a
    candidate who had done the work and a customer who had been charged.

    `tests/test_schedule_parity.py` separately proves Terraform mirrors this;
    together they are the two halves the beat-entry incident cost this platform.
    """
    from app.workers import schedule

    tasks = {entry.task for entry in schedule.SCHEDULE}
    assert "pickready.release_held_assessments" in tasks, sorted(tasks)
