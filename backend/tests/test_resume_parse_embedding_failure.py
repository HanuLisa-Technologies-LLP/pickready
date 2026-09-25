"""An embedding outage never costs the parse (audit Part 1 #14, PLAN-p2 test 7).

`resume_parsing.parse_resume` used to compute the embedding BEFORE its commit
and let `EmbeddingError` escape, so nothing committed and the task retried the
paid extraction. Now the text and the parsed fields commit with a NULL vector,
and the task dispatches the index and Yukti's reading AFTER the commit.

Every stored-state assertion reads from a SECOND connection.

Mutation check recorded in the Phase 2 WP-B report: restoring the unguarded
`profile.embedding = (await embed([resume_text]))[0]` fails
`test_the_parse_commits_its_text_and_fields_when_the_embedding_fails` (the row
is never committed) and `test_the_task_dispatches_the_index_and_yukti_after_its_commit`.
"""
from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import text

from app.core.db import superadmin_scope
from app.services import resume_parsing
from app.services.embeddings import EmbeddingError
from app.workers import dispatch
from tests import skills_fixtures as fx

RESUME = "Data engineer\nOwned the Kafka ingestion pipeline serving the fraud team"
PARSED = {
    "skills": ["Kafka"],
    "total_experience_years": 6,
    "education": [],
    "employment_history": [],
}


async def _down(texts, *args, **kwargs):
    raise EmbeddingError("the embedding provider is down")


async def _extracted(resume_text, *args, **kwargs):
    return dict(PARSED)


async def _seed() -> tuple[uuid.UUID, uuid.UUID]:
    candidate, profile = uuid.uuid4(), uuid.uuid4()
    async with fx.sessions()() as session:
        async with session.begin():
            async with superadmin_scope(session):
                await session.execute(
                    text(
                        "INSERT INTO candidates (id, full_name, email) "
                        "VALUES (:c, 'Parse Person', :e)"
                    ),
                    {"c": candidate, "e": f"{candidate}@parse.test"},
                )
                await session.execute(
                    text(
                        "INSERT INTO profiles (id, candidate_id, resume_text) "
                        "VALUES (:p, :c, :r)"
                    ),
                    {"p": profile, "c": candidate, "r": RESUME},
                )
    return candidate, profile


async def _drop(candidate: uuid.UUID) -> None:
    async with fx.sessions()() as session:
        async with session.begin():
            async with superadmin_scope(session):
                await session.execute(
                    text("DELETE FROM candidates WHERE id = :c"), {"c": candidate}
                )


async def _stored(profile: uuid.UUID) -> dict:
    async with fx.sessions()() as session:
        async with superadmin_scope(session):
            row = (
                await session.execute(
                    text(
                        "SELECT resume_text, parsed_fields_json, embedding IS NULL AS no_vector "
                        "FROM profiles WHERE id = :p"
                    ),
                    {"p": profile},
                )
            ).mappings().one()
    return dict(row)


async def test_the_parse_commits_its_text_and_fields_when_the_embedding_fails(
    monkeypatch,
) -> None:
    monkeypatch.setattr(resume_parsing, "embed", _down)
    monkeypatch.setattr(resume_parsing, "extract_structured_fields", _extracted)
    candidate, profile = await _seed()
    try:
        async with fx.sessions()() as session:
            await session.begin()
            async with superadmin_scope(session):
                await resume_parsing.parse_resume(session, profile)
        stored = await _stored(profile)
    finally:
        await _drop(candidate)
    assert stored["resume_text"] == RESUME
    assert stored["parsed_fields_json"] == PARSED
    assert stored["no_vector"] is True


def test_the_task_dispatches_the_index_and_yukti_after_its_commit(monkeypatch) -> None:
    """A SYNC test, because the task body runs its own event loop, as it does in
    a Lambda. The dispatches come after the parse's commit, in this order."""
    from app.workers import tasks

    monkeypatch.setattr(resume_parsing, "embed", _down)
    monkeypatch.setattr(resume_parsing, "extract_structured_fields", _extracted)
    candidate, profile = asyncio.run(_seed())
    dispatch.clear_recorded()
    try:
        tasks.parse_resume(str(profile))
        names = dispatch.recorded_names()
        stored = asyncio.run(_stored(profile))
    finally:
        asyncio.run(_drop(candidate))
    assert names == ["pickready.index_document", "pickready.yukti_score_profile"]
    assert stored["parsed_fields_json"] == PARSED, "the task did not raise past its commit"
