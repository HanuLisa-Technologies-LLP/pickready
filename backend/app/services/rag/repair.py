"""Semantic index repair: find chunks whose vector is missing or stale, and fix them.

WHY THE EXISTING SWEEP COULD NOT DO THIS
----------------------------------------
`pickready.reconcile_context_index` asks one question, "which documents have
text and NO chunk rows", and it answers it honestly. It cannot see the other
three ways a chunk stops being searchable by meaning:

  * the vector is NULL. `rag/index` writes a chunk with no vector when the
    embedding service is down, deliberately, so the text stays searchable by
    keyword. Nothing ever came back for it: the document HAS chunks, so the
    reconcile sweep considers it done, and the chunk stayed lexical-only for
    ever (audit Part 2 row 11, Part 3 section 21).
  * the vector came from a RETIRED model or text builder. Before 2026-09-24
    this was not even detectable, because `index_document` never stamped
    `embedding_model` or `embedding_contract_version`.
  * the vector has the wrong WIDTH. The column is `vector(1024)` today, so
    pgvector refuses such a row at write time and this arm cannot match; it is
    asked anyway because the day the column is widened or untyped is the day a
    mixed-width index starts returning distances that mean nothing, and the
    sweep should already be asking.

IT ASKS THE TABLE, NEVER A TIMESTAMP, AND IT STAMPS WHAT IT DID
----------------------------------------------------------------
The predicate is over the provenance columns themselves, compared with the
constants in `config/llm_providers`. A chunk is repaired by re-embedding its
OWN stored text through the one join `rag/contextual.embedding_input` performs
(the stored prefix plus the verbatim content), and the vector and its three
provenance columns move in ONE statement, the rule `scripts/reembed.py` states:
a stamp written apart from its vector is a provenance column that lies for as
long as the gap lasts, and for ever after a crash inside it.

The UPDATE is guarded on `content_sha256`, so a chunk the indexer rewrote
between the read and the write keeps the indexer's vector rather than
receiving one computed from text that is no longer there.

BOUNDED, PAUSABLE, AND NEVER SILENT
-----------------------------------
`settings.retrieval_repair_sweep_batch` caps one pass (default 200, one pass
an hour), and 0 PAUSES the sweep: a setting, not a code change, because the
first passes after this ships re-embed every chunk written before the stamp
existed and an owner watching a vendor bill needs a lever. Every pass logs
`rag.repair.swept`, including the all-zero pass, because a sweep that logs
nothing when it finds nothing is indistinguishable from one that is not
running. A pass whose embedding call failed logs `rag.repair.degraded`, which a
CloudWatch metric filter watches, and leaves the chunks exactly as they were
for the next pass.

IT REFUSES TO RUN ON THE DEVELOPMENT FALLBACK
---------------------------------------------
With no `VOYAGE_CONTEXT_4`, `embeddings.embed` returns pseudo-random vectors.
Writing those over a chunk and stamping `voyage-4` would be the single worst
outcome available here, the one `scripts/reembed.py` refuses by name, so the
pass records `skipped=embedding_not_configured` and touches nothing.

RETRIEVAL IS A RANKING PRIOR, AND THIS MODULE IS PART OF RETRIEVAL
------------------------------------------------------------------
It imports no scorer and moves no grade. A repaired vector changes which
passages an agent reads first, never what a passage is worth
(`tests/test_retrieval_scoring_isolation.py`).
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.llm_providers import EMBEDDING_CONTRACT_VERSION, EMBEDDING_MODEL
from app.services.embeddings import EMBEDDING_DIM, is_semantic
from app.services.rag import contextual
from app.services.rag.index import _embed_batched, _vector_literal, provenance_for

logger = logging.getLogger(__name__)

#: Why a pass did nothing, in the words the log line and the result carry.
SKIPPED_DISABLED = "disabled"
SKIPPED_NOT_CONFIGURED = "embedding_not_configured"

#: The stale predicate, written ONCE and used by the selection and the count so
#: the "remaining" figure a pass reports is the same question it just answered.
#: `IS DISTINCT FROM` rather than `<>`, because a NULL stamp (every chunk
#: written before 2026-09-24) must count as stale and `NULL <> 'x'` is NULL.
_STALE_PREDICATE = """
      embedding IS NULL
   OR embedding_model IS DISTINCT FROM :model
   OR embedding_contract_version IS DISTINCT FROM :contract
   OR vector_dims(embedding) <> :dim
"""


def _stale_params() -> dict[str, object]:
    return {
        "model": EMBEDDING_MODEL,
        "contract": EMBEDDING_CONTRACT_VERSION,
        "dim": EMBEDDING_DIM,
    }


@dataclass(frozen=True)
class StaleChunk:
    """One chunk to re-embed, with exactly what re-embedding it needs."""

    chunk_id: uuid.UUID
    content: str
    context_prefix: str | None
    content_sha256: str


@dataclass(frozen=True)
class RepairResult:
    """What one pass did. Counts only; never a chunk's text."""

    #: Chunks selected as stale by this pass.
    selected: int = 0
    #: Chunks whose vector and provenance were rewritten.
    repaired: int = 0
    #: Chunks selected and left alone because the indexer rewrote their text
    #: between the read and the write. Not a failure: the indexer's own vector
    #: is newer than anything this pass could compute.
    superseded: int = 0
    #: Stale chunks still in the table after this pass, the same predicate.
    remaining_estimate: int = 0
    #: True when an embedding call failed. The affected chunks are untouched
    #: and the next pass picks them up.
    degraded: bool = False
    #: Why the pass did nothing at all, when it did nothing by design.
    skipped: str | None = None


async def stale_chunks(session: AsyncSession, *, limit: int) -> list[StaleChunk]:
    """Up to `limit` chunks whose vector is missing, stale or the wrong width.

    Oldest write first (`updated_at NULLS FIRST, id`), so a backlog drains in a
    stable order and a pass that failed half way picks up where it stopped.
    """
    if limit <= 0:
        return []
    rows = await session.execute(
        text(
            f"""
            SELECT id, content, context_prefix, content_sha256
              FROM context_chunks
             WHERE {_STALE_PREDICATE}
             ORDER BY updated_at NULLS FIRST, id
             LIMIT :limit
            """
        ),
        {**_stale_params(), "limit": limit},
    )
    return [
        StaleChunk(
            chunk_id=row.id,
            content=row.content,
            context_prefix=row.context_prefix,
            content_sha256=row.content_sha256,
        )
        for row in rows
    ]


async def stale_count(session: AsyncSession) -> int:
    """How many chunks the predicate still selects. The pass's own yardstick."""
    return int(
        (
            await session.execute(
                text(f"SELECT count(*) FROM context_chunks WHERE {_STALE_PREDICATE}"),
                _stale_params(),
            )
        ).scalar_one()
    )


def _log(result: RepairResult, *, limit: int) -> None:
    logger.info(
        "rag.repair.swept repaired=%d remaining_estimate=%d degraded=%s "
        "selected=%d superseded=%d limit=%d skipped=%s",
        result.repaired,
        result.remaining_estimate,
        result.degraded,
        result.selected,
        result.superseded,
        limit,
        result.skipped or "none",
    )
    if result.degraded:
        logger.warning(
            "rag.repair.degraded selected=%d repaired=%d remaining_estimate=%d",
            result.selected,
            result.repaired,
            result.remaining_estimate,
        )


async def repair(session: AsyncSession, *, limit: int) -> RepairResult:
    """One bounded pass. The caller commits; nothing here commits.

    Never raises for an embedding outage: that is `degraded=True` with the
    chunks untouched, which is the state they were already in. A database
    error does raise, because a sweep that swallowed one would report a
    repair it never wrote.
    """
    if limit <= 0:
        result = RepairResult(skipped=SKIPPED_DISABLED)
        _log(result, limit=limit)
        return result
    if not is_semantic():
        result = RepairResult(
            remaining_estimate=await stale_count(session),
            skipped=SKIPPED_NOT_CONFIGURED,
        )
        _log(result, limit=limit)
        return result

    chunks = await stale_chunks(session, limit=limit)
    vectors, degraded = await _embed_batched(
        [
            contextual.embedding_input(chunk.context_prefix, chunk.content)
            for chunk in chunks
        ]
    )

    repaired = superseded = 0
    for chunk, vector in zip(chunks, vectors):
        if vector is None:
            continue
        written = (
            await session.execute(
                text(
                    """
                    UPDATE context_chunks
                       SET embedding = CAST(:embedding AS vector),
                           embedding_model = :embedding_model,
                           embedding_contract_version = :embedding_contract_version,
                           embedding_generated_at = :embedding_generated_at
                     WHERE id = :id
                       AND content_sha256 = :content_sha256
                    """
                ),
                {
                    "id": str(chunk.chunk_id),
                    "content_sha256": chunk.content_sha256,
                    "embedding": _vector_literal(vector),
                    **provenance_for(vector),
                },
            )
        ).rowcount or 0
        if written:
            repaired += 1
        else:
            superseded += 1

    result = RepairResult(
        selected=len(chunks),
        repaired=repaired,
        superseded=superseded,
        remaining_estimate=await stale_count(session),
        degraded=degraded,
    )
    _log(result, limit=limit)
    return result


__all__ = [
    "RepairResult",
    "SKIPPED_DISABLED",
    "SKIPPED_NOT_CONFIGURED",
    "StaleChunk",
    "repair",
    "stale_chunks",
    "stale_count",
]
