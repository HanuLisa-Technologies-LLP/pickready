"""A derived vector must not outlive the text it was derived from.

THE DEFECT
----------
`jobs.embedding` is built from the job description. Editing a JD wrote
`jd_markdown` and `jd_json` and left the vector alone.

It is invisible in the matching pipeline, which is why it survived:
`matching.run_matching` re-embeds the JD on every run and overwrites the
column, so it never reads the stale value. It is NOT invisible on the
candidate's New Jobs board -- `services/job_relevance` reads `jobs.embedding`
directly and never recomputes it. So after a JD edit, every candidate kept
being ranked against a job description nobody could read any more, and there
was no point at which that would correct itself.

Nothing failed. No error, no log line, just a quietly wrong ranking on the one
screen a candidate actually uses to find work.

WHY NULL AND NOT A RECOMPUTE
-----------------------------
`job_relevance` documents that a job with no embedding falls back to keyword
scoring, and keywords are read from the CURRENT text. Ranking on current words
beats ranking on a stale vector, it costs no model call on the request path,
and `run_matching` writes a fresh vector on its next run.

WHY THESE HIT A REAL DATABASE
-----------------------------
Because the thing being asserted is that a SQL statement reaches one. The first
version of the fix set an attribute in a `before_update` listener, which would
have changed a Python object and never touched the column -- `embedding` is
pgvector and is not mapped on `Job`. A test with a fake session would have
passed against that.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.db import superadmin_scope
from app.models.job import _EMBEDDING_SOURCE_FIELDS, Job

pytestmark = pytest.mark.asyncio


async def _factory_or_skip():
    """A FRESH engine per test, disposed by the caller.

    Same pattern as `test_rls.py`, for the same reason: pytest-asyncio gives
    each test its own event loop, and a pooled connection created under a
    previous loop raises "attached to a different loop" when it is reused.
    Sharing the application engine here fails intermittently rather than
    cleanly, which is worse than not running at all.
    """
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001 - any connect failure means "no DB here"
        await engine.dispose()
        pytest.skip("no database reachable")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seeded_job(session) -> tuple[uuid.UUID, Job]:
    """Any existing job, given a vector written the way matching writes one."""
    row = (await session.execute(text("SELECT id FROM jobs LIMIT 1"))).first()
    # Deliberately an assertion and not a skip. A migrated database always has
    # jobs, so the skip this replaces could not fire and enforced nothing -- and
    # if that ever stops being true, the honest answer is that these tests did
    # not run, not that they passed.
    assert row is not None, (
        "No job rows exist, so nothing here was exercised. Run "
        "`alembic upgrade head` against the test database first."
    )
    job_id = row[0]
    vector = "[" + ",".join("0.1" for _ in range(1024)) + "]"
    await session.execute(
        text("UPDATE jobs SET embedding = CAST(:v AS vector) WHERE id = :id"),
        {"v": vector, "id": str(job_id)},
    )
    job = await session.get(Job, job_id)
    return job_id, job


async def _embedding_is_null(session, job_id) -> bool:
    row = (
        await session.execute(
            text("SELECT embedding IS NULL FROM jobs WHERE id = :id"),
            {"id": str(job_id)},
        )
    ).first()
    return bool(row[0])


async def _mutate_and_check(mutate) -> bool:
    """Apply `mutate(job)` inside a rolled-back transaction and report whether
    the embedding ended up NULL."""
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            async with superadmin_scope(session):
                job_id, job = await _seeded_job(session)
                assert not await _embedding_is_null(session, job_id), (
                    "the fixture did not write a vector to begin with"
                )
                mutate(job)
                await session.flush()
                result = await _embedding_is_null(session, job_id)
                await session.rollback()
                return result
    finally:
        await engine.dispose()


async def test_editing_the_job_description_clears_the_vector() -> None:
    """The reported defect, stated directly."""
    cleared = await _mutate_and_check(
        lambda job: setattr(
            job, "jd_json", dict(job.jd_json or {}, role="A completely different role.")
        )
    )
    assert cleared, "the JD changed and the vector built from it survived"


@pytest.mark.parametrize(
    "field,value",
    [
        ("title", "Staff Platform Engineer"),
        ("department", "Platform"),
        ("level", "senior"),
    ],
)
async def test_every_field_the_vector_is_built_from_invalidates_it(
    field: str, value: str
) -> None:
    """`matching._jd_text` reads all of these, so all of them are sources."""
    cleared = await _mutate_and_check(lambda job: setattr(job, field, value))
    assert cleared, f"changing {field} left a stale vector in place"


async def test_an_unrelated_edit_leaves_the_vector_alone() -> None:
    """The other direction, and the one that makes this worth having.

    Invalidating on every UPDATE would discard the vector whenever anything on
    the row moved, and the board would fall back to keyword scoring
    permanently. That is a worse outcome reached by being careless in the
    safe-looking direction.
    """
    # A real, ordinary edit that has nothing to do with the JD text.
    cleared = await _mutate_and_check(
        lambda job: setattr(job, "assessment_status", job.assessment_status)
    )
    assert not cleared, "an edit that does not touch the JD discarded the vector"


async def test_a_rolled_back_edit_does_not_lose_the_embedding() -> None:
    """The invalidation runs on the flush's own connection, so it lives and
    dies with the caller's transaction."""
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            async with superadmin_scope(session):
                job_id, _ = await _seeded_job(session)
                await session.commit()

        async with factory() as session:
            async with superadmin_scope(session):
                job = await session.get(Job, job_id)
                job.title = "Rolled back title"
                await session.flush()
                assert await _embedding_is_null(session, job_id)
                await session.rollback()

        async with factory() as session:
            async with superadmin_scope(session):
                survived = not await _embedding_is_null(session, job_id)
                # Leave the row as it was found, whatever the assertion says.
                await session.execute(
                    text("UPDATE jobs SET embedding = NULL WHERE id = :id"),
                    {"id": str(job_id)},
                )
                await session.commit()
        assert survived, "a rolled-back edit still discarded the vector"
    finally:
        await engine.dispose()


async def test_the_source_field_list_matches_what_the_embedding_reads() -> None:
    """A field added to `matching._jd_text` without being added to
    `_EMBEDDING_SOURCE_FIELDS` reintroduces exactly this bug, silently."""
    import inspect

    from app.services import matching

    source = inspect.getsource(matching._jd_text)
    for field in _EMBEDDING_SOURCE_FIELDS:
        assert field in source, (
            f"{field} is listed as an embedding source but _jd_text does not read it"
        )
    for attribute in ("title", "department", "level"):
        assert f"job.{attribute}" in source
        assert attribute in _EMBEDDING_SOURCE_FIELDS
