"""Every chunk the indexer embeds says which model and contract produced it.

Migration 0062 added `embedding_model`, `embedding_contract_version` and
`embedding_generated_at` to `context_chunks`, and until 2026-09-24 the only
writer of new chunks never filled them. So "was this vector produced by the
current model" had no answer anywhere, and the repair sweep had nothing to
compare against. Asserted on the TABLE, not on the indexer's report of itself.

The three states, each pinned:

  * a real embedding (a key is configured): all three stamped, in the same
    statement as the vector;
  * the development fallback (no key): a vector, and NO stamp, because a
    `voyage-4` stamp on a pseudo-random vector is the lie `scripts/reembed.py`
    refuses by name;
  * an embedding failure: no vector and no stamp, because a model name beside
    no vector asserts work that did not happen.
"""
from __future__ import annotations

import pytest

from app.config.llm_providers import EMBEDDING_CONTRACT_VERSION, EMBEDDING_MODEL
from app.services.embeddings import EMBEDDING_DIM, EmbeddingError
from tests import rag_world


def _fake_embed(calls: list[int] | None = None):
    async def embed(texts, *, input_type="document"):
        if calls is not None:
            calls.append(len(texts))
        return [[0.001 * (i + 1)] * EMBEDDING_DIM for i, _ in enumerate(texts)]

    return embed


@pytest.fixture(autouse=True)
def _no_prefix_calls(monkeypatch):
    rag_world.disable_prefixes(monkeypatch)


@pytest.fixture
async def world():
    engine, factory = await rag_world.factory_or_skip()
    fx = rag_world.World()
    await rag_world.seed(factory, fx)
    try:
        yield factory, fx
    finally:
        await rag_world.cleanup(factory, fx)
        await engine.dispose()


async def test_a_real_embedding_is_stamped_with_model_contract_and_time(
    world, monkeypatch
) -> None:
    from app.services.rag import index

    factory, fx = world
    monkeypatch.setattr(index, "is_semantic", lambda: True)
    monkeypatch.setattr(index, "embed", _fake_embed())

    await rag_world.index_all(factory, fx)

    rows = await rag_world.chunk_rows(factory, fx.profile_id)
    assert rows, "the resume produced no chunks"
    for row in rows:
        assert row.has_vector
        assert row.embedding_model == EMBEDDING_MODEL
        assert row.embedding_contract_version == EMBEDDING_CONTRACT_VERSION
        assert row.embedding_generated_at is not None


async def test_the_development_fallback_is_never_stamped_as_a_real_model(
    world, monkeypatch
) -> None:
    """No key: `embeddings.embed` returns pseudo-random vectors. Stored, and
    honestly unstamped, so the repair sweep (which refuses to run without a
    key) re-embeds them the day a key arrives."""
    from app.services.rag import index

    factory, fx = world
    monkeypatch.setattr(index, "is_semantic", lambda: False)

    await rag_world.index_all(factory, fx)

    rows = await rag_world.chunk_rows(factory, fx.profile_id)
    assert rows
    for row in rows:
        assert row.has_vector, "the development fallback still writes a vector"
        assert row.embedding_model is None
        assert row.embedding_contract_version is None
        assert row.embedding_generated_at is None


async def test_an_embedding_failure_leaves_no_vector_and_no_stamp(
    world, monkeypatch
) -> None:
    from app.services.rag import index

    factory, fx = world

    async def failing(texts, *, input_type="document"):
        raise EmbeddingError("provider down")

    monkeypatch.setattr(index, "is_semantic", lambda: True)
    monkeypatch.setattr(index, "embed", failing)

    await rag_world.index_all(factory, fx)

    rows = await rag_world.chunk_rows(factory, fx.link_id)
    assert rows, "the transcript must still be indexed for keyword retrieval"
    for row in rows:
        assert not row.has_vector
        assert row.embedding_model is None
        assert row.embedding_contract_version is None
        assert row.embedding_generated_at is None


async def test_a_reindex_after_an_outage_stamps_the_rewritten_chunk(
    world, monkeypatch
) -> None:
    """The upsert carries the stamp too, so a chunk rewritten with a vector
    does not keep the NULL provenance of the row it replaced."""
    from app.core.db import superadmin_scope
    from app.services.rag import index

    factory, fx = world

    async def failing(texts, *, input_type="document"):
        raise EmbeddingError("provider down")

    monkeypatch.setattr(index, "is_semantic", lambda: True)
    monkeypatch.setattr(index, "embed", failing)
    await rag_world.index_all(factory, fx)

    monkeypatch.setattr(index, "embed", _fake_embed())
    edited = rag_world.RESUME.replace("three regions", "four regions")
    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                result = await index.index_document(
                    s,
                    tenant_id=fx.tenant_id,
                    source_type="resume",
                    source_id=fx.profile_id,
                    document=edited,
                )
    assert result.written >= 1
    assert result.stamped == result.written

    rows = await rag_world.chunk_rows(factory, fx.profile_id)
    stamped = [row for row in rows if row.embedding_model == EMBEDDING_MODEL]
    assert len(stamped) == result.written
    assert all(row.has_vector for row in stamped)
