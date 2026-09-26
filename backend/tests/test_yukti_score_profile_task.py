"""`pickready.yukti_score_profile`: a parsed resume is read against every OPEN
job it is applied to, and nothing else (PLAN-p2 section 5.1, test 8).

It replaces the pre-screen grade the parse used to write, so a new applicant is
ranked without anybody re-running AI Matching. A link is read only when its job
is published, not closed, not archived and has saved skills; a link another
transaction is already reading is skipped, never waited on. Committed state is
read from a SECOND connection.

Mutation check recorded in the Phase 2 WP-B report: dropping the saved-skills
check in `matching.score_profile` fails
`test_only_links_on_open_jobs_with_saved_skills_are_read`.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text

from app.core.db import superadmin_scope
from app.services import llm_router, locks, matching
from app.services.yukti import config
from app.workers import registry
from tests import skills_fixtures as fx
from tests.test_yukti_run_matching_db import Router, _drop, _link_row, _seed


async def _second_link(world: fx.World, ids: dict, *, ratified: bool) -> uuid.UUID:
    """A link on `world`'s job made with the same profile as `ids`'s link."""
    link = uuid.uuid4()
    async with fx.sessions()() as session:
        async with session.begin():
            async with superadmin_scope(session):
                if ratified:
                    await session.execute(
                        text(
                            "UPDATE jobs SET ratified_at = now(), status = 'ratified', "
                            "lifecycle_state = 'PUBLISHED' WHERE id = :j"
                        ),
                        {"j": world.job},
                    )
                await session.execute(
                    text(
                        "INSERT INTO job_candidate_links (id, tenant_id, job_id, candidate_id, "
                        "profile_id, source) VALUES (:l, :t, :j, :c, :p, 'fresh')"
                    ),
                    {"l": link, "t": world.tenant, "j": world.job,
                     "c": ids["candidate"], "p": ids["linked_profile"]},
                )
    return link


async def _score(profile_id: uuid.UUID, *, hold: uuid.UUID | None = None) -> int:
    maker = fx.sessions()
    async with maker() as holder, maker() as session:
        await holder.begin()
        try:
            if hold is not None:
                assert await locks.try_advisory_lock(holder, locks.YUKTI_LINK, hold)
            await session.begin()
            async with superadmin_scope(session):
                return await matching.score_profile(session, profile_id)
        finally:
            await holder.rollback()


async def test_only_links_on_open_jobs_with_saved_skills_are_read(monkeypatch) -> None:
    router = Router()
    monkeypatch.setattr(llm_router, "chat_completion", router)
    ids = await _seed()
    unsaved = await fx.seed(saved=False)
    unpublished = await fx.seed(saved=True)
    try:
        on_unsaved = await _second_link(unsaved, ids, ratified=True)
        on_unpublished = await _second_link(unpublished, ids, ratified=False)
        scored = await _score(ids["linked_profile"])
        open_row = await _link_row(ids["link"])
        unsaved_row = await _link_row(on_unsaved)
        unpublished_row = await _link_row(on_unpublished)
    finally:
        await fx.drop(unsaved)
        await fx.drop(unpublished)
        await _drop(ids)
    assert scored == 1
    assert open_row["yukti_status"] == config.STATUS_SCORED
    assert open_row["yukti_profile_id"] == ids["linked_profile"]
    assert unsaved_row["yukti_status"] == config.STATUS_PENDING
    assert unpublished_row["yukti_status"] == config.STATUS_PENDING
    assert len(router.calls) == 1, "one reading, for the one open job"


async def test_a_link_another_transaction_is_reading_is_skipped(monkeypatch) -> None:
    router = Router()
    monkeypatch.setattr(llm_router, "chat_completion", router)
    ids = await _seed()
    try:
        scored = await _score(ids["linked_profile"], hold=ids["link"])
        row = await _link_row(ids["link"])
    finally:
        await _drop(ids)
    assert scored == 0
    assert router.calls == []
    assert row["yukti_status"] == config.STATUS_PENDING


async def test_a_closed_job_is_not_read(monkeypatch) -> None:
    router = Router()
    monkeypatch.setattr(llm_router, "chat_completion", router)
    ids = await _seed()
    try:
        async with fx.sessions()() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await session.execute(
                        text("UPDATE jobs SET closed_at = now() WHERE id = :j"),
                        {"j": ids["world"].job},
                    )
        scored = await _score(ids["linked_profile"])
        row = await _link_row(ids["link"])
    finally:
        await _drop(ids)
    assert scored == 0 and router.calls == []
    assert row["yukti_status"] == config.STATUS_PENDING


def test_the_task_is_a_short_lambda_task_with_one_retry() -> None:
    from app.workers import tasks  # noqa: F401  (registers every task)

    spec = registry.resolve("pickready.yukti_score_profile")
    assert spec.route is registry.Route.LAMBDA
    assert spec.max_attempts == 2
