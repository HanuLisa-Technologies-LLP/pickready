"""One matching run per job, across processes (services/locks.MATCHING).

WHY THIS EXISTS
-----------------
`pickready.run_matching` is Route.ECS: every dispatch is its own Fargate
container, so in-process coalescing cannot see a duplicate. The trigger is a
button any staff member with `trigger_matching` can press, plus four pipeline
call sites, and nothing refused the second press: two concurrent runs embedded
the same resumes, spent the model chain twice, and raced writes to the same
rows. Scoring got this lock first (test_scoring_lock.py); this is the sibling,
keyed on the JOB rather than the application.

WHAT IS PINNED
----------------
Order and behaviour, separately. Order over the SOURCE, because a lock taken
after the JD embedding has been paid for reports a duplicate instead of
preventing one, and a mock-based test would pass either way. Behaviour against
the real database, because the refusal must return an honest zero with the
stage record saying WHY, never a run that looks finished with no results and
no explanation.
"""
from __future__ import annotations

import ast
import pathlib
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.services import locks

APP_ROOT = pathlib.Path(__file__).resolve().parents[1] / "app"


def test_the_matching_namespace_is_distinct_from_scoring() -> None:
    """Two features keying on overlapping id spaces must not share a lock:
    a matching run must never block a scoring run or vice versa."""
    subject = uuid.uuid4()
    assert locks.advisory_key(locks.MATCHING, subject) != locks.advisory_key(
        locks.SCORING, subject
    )


def test_the_lock_is_taken_before_anything_is_spent() -> None:
    """Pinned over the source of `matching.run_matching`, the way the scoring
    ordering is pinned: the lock call must precede the first embed call and
    the retrieval stages. CALL sites, never substrings, so an import line or
    a comment cannot satisfy it."""
    source = (APP_ROOT / "services" / "matching.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    target = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "run_matching":
            target = node
            break
    assert target is not None, "matching.run_matching is gone or was renamed"

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
        "run_matching no longer CALLS the matching lock; a duplicate dispatch "
        "runs the whole pipeline twice"
    )
    for later in ("embed", "_semantic_stage", "_keyword_stage"):
        line = called_at(later)
        assert line is None or lock_line < line, (
            f"the matching lock is taken AFTER {later}, so a duplicate run has "
            "already spent work by the time it is refused"
        )


async def test_a_held_lock_makes_the_second_run_an_honest_no_op() -> None:
    """Against the real database: session A holds (MATCHING, job), a full
    `run_matching` call on session B returns 0 having embedded nothing, and
    the stage record carries the reason a reader can act on."""
    from app.services import matching, matching_progress

    engine = create_async_engine(
        get_settings().database_url, pool_size=2, max_overflow=0
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id, job_id = uuid.uuid4(), uuid.uuid4()
    try:
        async with factory() as setup:
            await setup.execute(
                text("SELECT set_config('app.bypass_rls','on',false)")
            )
            await setup.execute(
                text(
                    "INSERT INTO tenants (id, name, domain, spf_dkim_status) "
                    "VALUES (:id, :name, :domain, 'pending')"
                ),
                {
                    "id": str(tenant_id),
                    "name": "Matching-Lock",
                    "domain": f"{tenant_id}.lock.test",
                },
            )
            await setup.execute(
                text(
                    "INSERT INTO jobs (id, tenant_id, title, jd_json, status) "
                    "VALUES (:id, :tid, 'Lock probe', '{}'::jsonb, 'draft')"
                ),
                {"id": str(job_id), "tid": str(tenant_id)},
            )
            await setup.commit()

        async with factory() as holder, factory() as second:
            await holder.execute(
                text("SELECT set_config('app.bypass_rls','on',false)")
            )
            await second.execute(
                text("SELECT set_config('app.bypass_rls','on',false)")
            )
            assert (
                await locks.try_advisory_lock(holder, locks.MATCHING, job_id)
                is True
            )

            progress = matching_progress.Progress()
            scored = await matching.run_matching(second, job_id, progress=progress)
            assert scored == 0

            payload = progress.payload()
            understanding = next(
                stage
                for stage in payload["stages"]
                if stage["key"] == "understanding"
            )
            assert understanding["status"] == "skipped"
            assert "already in progress" in understanding["detail"]
            # Nothing later ran: the run stopped before the first vendor call.
            jd_stage = next(
                stage
                for stage in payload["stages"]
                if stage["key"] == "jd_embedding"
            )
            assert jd_stage["status"] not in ("complete", "running"), jd_stage
    except Exception:
        raise
    finally:
        async with factory() as cleanup:
            await cleanup.execute(
                text("SELECT set_config('app.bypass_rls','on',false)")
            )
            await cleanup.execute(
                text("DELETE FROM tenants WHERE id = :id"), {"id": str(tenant_id)}
            )
            await cleanup.commit()
        await engine.dispose()
