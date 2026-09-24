"""The semantic index repair sweep finds what the reconcile sweep cannot see.

`pickready.reconcile_context_index` asks "which documents have NO chunks". A
chunk written with a NULL vector during an embedding outage, or embedded by a
model this platform has retired, belongs to a document that HAS chunks, so that
sweep calls it done and the chunk stays keyword-only for ever (audit Part 2 row
11). `rag.repair` asks the provenance columns instead, and these tests pin the
states that matter, on the TABLE:

  * a NULL vector, a retired model and a retired contract version are all
    selected and repaired, and the repair stamps model, contract and time in
    the same statement as the vector;
  * a chunk that is already current is not selected at all;
  * an embedding outage during a pass leaves every selected chunk exactly as
    it was, records `degraded`, and logs the line the CloudWatch alarm reads;
  * a chunk the indexer rewrote between the read and the write keeps the
    indexer's vector (the UPDATE is guarded on `content_sha256`);
  * 0 pauses the sweep, and no key refuses it, and BOTH still log, because a
    sweep that logs nothing when it does nothing is indistinguishable from one
    that is not running.

The sweep is GLOBAL by design (it repairs every tenant's index), so these
assertions are about this module's own rows and never about table-wide counts.
"""
from __future__ import annotations

import logging

import pytest
from sqlalchemy import text

from app.config.llm_providers import EMBEDDING_CONTRACT_VERSION, EMBEDDING_MODEL
from app.services.embeddings import EMBEDDING_DIM, EmbeddingError
from tests import rag_world

#: Large enough that this module's own stale rows are always inside one pass,
#: whatever another module left behind in the shared database.
_LIMIT = 10_000


def _fake_embed(value: float = 0.002):
    async def embed(texts, *, input_type="document"):
        return [[value] * EMBEDDING_DIM for _ in texts]

    return embed


async def _failing_embed(texts, *, input_type="document"):
    raise EmbeddingError("provider down")


@pytest.fixture(autouse=True)
def _no_prefix_calls(monkeypatch):
    rag_world.disable_prefixes(monkeypatch)


@pytest.fixture
async def world(monkeypatch):
    """A resume and a transcript indexed while the embedding service was DOWN,
    so every chunk carries a NULL vector: the exact state the sweep exists for."""
    from app.services.rag import index

    engine, factory = await rag_world.factory_or_skip()
    fx = rag_world.World()
    await rag_world.seed(factory, fx)
    monkeypatch.setattr(index, "is_semantic", lambda: True)
    monkeypatch.setattr(index, "embed", _failing_embed)
    await rag_world.index_all(factory, fx)
    try:
        yield factory, fx
    finally:
        await rag_world.cleanup(factory, fx)
        await engine.dispose()


async def _repair(factory, *, limit: int = _LIMIT):
    from app.core.db import superadmin_scope
    from app.services.rag import repair

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                return await repair.repair(s, limit=limit)


def _semantic(monkeypatch, *, embed) -> None:
    """A configured key and a stand-in vendor, in both modules that read them."""
    from app.services.rag import index, repair

    monkeypatch.setattr(index, "is_semantic", lambda: True)
    monkeypatch.setattr(repair, "is_semantic", lambda: True)
    monkeypatch.setattr(index, "embed", embed)


async def test_null_vectors_are_repaired_and_stamped(world, monkeypatch) -> None:
    factory, fx = world
    before = await rag_world.chunk_rows(factory, fx.link_id)
    assert before and not any(row.has_vector for row in before), (
        "the fixture must start from chunks written during an outage"
    )

    _semantic(monkeypatch, embed=_fake_embed())
    result = await _repair(factory)

    assert result.degraded is False
    assert result.skipped is None
    for source_id in (fx.link_id, fx.profile_id):
        rows = await rag_world.chunk_rows(factory, source_id)
        assert rows
        for row in rows:
            assert row.has_vector
            assert row.embedding_model == EMBEDDING_MODEL
            assert row.embedding_contract_version == EMBEDDING_CONTRACT_VERSION
            assert row.embedding_generated_at is not None
    assert result.repaired >= len(before)


async def test_a_retired_model_and_a_retired_contract_are_both_stale(
    world, monkeypatch
) -> None:
    """The two arms `reembed.py` could not see because nothing stamped them."""
    from app.core.db import superadmin_scope
    from app.services.rag import repair

    factory, fx = world
    _semantic(monkeypatch, embed=_fake_embed())
    await _repair(factory)

    rows = await rag_world.chunk_rows(factory, fx.link_id)
    assert len(rows) >= 2
    retired_model, retired_contract = rows[0].id, rows[1].id
    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                await s.execute(
                    text("UPDATE context_chunks SET embedding_model = 'bge-m3' "
                         "WHERE id = :id"),
                    {"id": str(retired_model)},
                )
                await s.execute(
                    text("UPDATE context_chunks "
                         "SET embedding_contract_version = 'v0-legacy' "
                         "WHERE id = :id"),
                    {"id": str(retired_contract)},
                )
                stale_ids = {
                    chunk.chunk_id
                    for chunk in await repair.stale_chunks(s, limit=_LIMIT)
                }
    assert retired_model in stale_ids
    assert retired_contract in stale_ids
    current = {row.id for row in rows} - {retired_model, retired_contract}
    assert not (current & stale_ids), "a current chunk was selected as stale"

    await _repair(factory)
    after = {row.id: row for row in await rag_world.chunk_rows(factory, fx.link_id)}
    assert after[retired_model].embedding_model == EMBEDDING_MODEL
    assert after[retired_contract].embedding_contract_version == (
        EMBEDDING_CONTRACT_VERSION
    )


async def test_an_outage_during_a_pass_changes_nothing_and_says_so(
    world, monkeypatch, caplog
) -> None:
    factory, fx = world
    _semantic(monkeypatch, embed=_failing_embed)

    with caplog.at_level(logging.INFO, logger="app.services.rag.repair"):
        result = await _repair(factory)

    assert result.degraded is True
    assert result.repaired == 0
    rows = await rag_world.chunk_rows(factory, fx.link_id)
    assert rows and not any(row.has_vector for row in rows)
    assert not any(row.embedding_model for row in rows)
    messages = [record.getMessage() for record in caplog.records]
    assert any(m.startswith("rag.repair.degraded") for m in messages), messages
    assert any(m.startswith("rag.repair.swept") for m in messages), messages

    # And the next pass, with the vendor back, picks the same chunks up.
    _semantic(monkeypatch, embed=_fake_embed())
    await _repair(factory)
    rows = await rag_world.chunk_rows(factory, fx.link_id)
    assert all(row.has_vector for row in rows)


async def test_a_chunk_rewritten_mid_pass_keeps_the_indexers_vector(
    world, monkeypatch
) -> None:
    """The UPDATE is guarded on `content_sha256`: a vector computed from text
    that is no longer there must not overwrite the indexer's newer one."""
    from app.core.db import superadmin_scope
    from app.services.rag import repair

    factory, fx = world
    _semantic(monkeypatch, embed=_fake_embed())
    target = (await rag_world.chunk_rows(factory, fx.link_id))[0].id
    original = repair._embed_batched

    async def rewrite_then_embed(texts):
        # Another writer lands between this pass's SELECT and its UPDATE.
        async with factory() as other:
            async with other.begin():
                async with superadmin_scope(other):
                    await other.execute(
                        text("UPDATE context_chunks SET content_sha256 = 'rewritten' "
                             "WHERE id = :id"),
                        {"id": str(target)},
                    )
        return await original(texts)

    monkeypatch.setattr(repair, "_embed_batched", rewrite_then_embed)
    result = await _repair(factory)

    assert result.superseded >= 1
    rows = {row.id: row for row in await rag_world.chunk_rows(factory, fx.link_id)}
    assert rows[target].content_sha256 == "rewritten"
    assert not rows[target].has_vector
    assert rows[target].embedding_model is None


async def test_zero_pauses_the_sweep_and_it_still_logs(
    world, monkeypatch, caplog
) -> None:
    factory, fx = world
    _semantic(monkeypatch, embed=_fake_embed())

    with caplog.at_level(logging.INFO, logger="app.services.rag.repair"):
        result = await _repair(factory, limit=0)

    from app.services.rag import repair

    assert result.skipped == repair.SKIPPED_DISABLED
    assert result.selected == 0
    rows = await rag_world.chunk_rows(factory, fx.link_id)
    assert not any(row.has_vector for row in rows)
    assert any(
        record.getMessage().startswith("rag.repair.swept")
        and "skipped=disabled" in record.getMessage()
        for record in caplog.records
    )


async def test_no_embedding_key_refuses_rather_than_stamping_fallback_vectors(
    world, monkeypatch
) -> None:
    """With no key `embeddings.embed` returns pseudo-random vectors. Writing
    them and stamping `voyage-4` is the outcome `scripts/reembed.py` refuses
    by name, so the pass touches nothing and says why."""
    from app.services.rag import repair

    factory, fx = world
    monkeypatch.setattr(repair, "is_semantic", lambda: False)

    result = await _repair(factory)

    assert result.skipped == repair.SKIPPED_NOT_CONFIGURED
    assert result.remaining_estimate >= 1
    rows = await rag_world.chunk_rows(factory, fx.link_id)
    assert not any(row.has_vector for row in rows)


def test_the_task_reads_its_batch_from_settings_and_zero_pauses_it(
    monkeypatch,
) -> None:
    """The scheduled entry point, run for real against the database: a paused
    sweep returns `skipped=disabled` without touching the index."""
    from app.core.config import get_settings
    from app.workers import tasks_retrieval

    monkeypatch.setattr(get_settings(), "retrieval_repair_sweep_batch", 0)
    outcome = tasks_retrieval.repair_semantic_index()
    assert outcome["skipped"] == "disabled"
    assert outcome["selected"] == 0


def test_the_sweep_is_scheduled_hourly_in_python() -> None:
    from app.workers.schedule import by_rule

    entry = by_rule("readypick-repair-semantic-index")
    assert entry.task == "pickready.repair_semantic_index"
    assert entry.interval_minutes == 60
