"""Hybrid retrieval over the chunk index: semantic, keyword, fused, reranked.

WHY BOTH HALVES
---------------
Semantic search finds the paragraph about queue partitioning when the query says
"message broker" and the resume never uses that phrase. Keyword search finds the
paragraph that says "Kafka" when the query says "Kafka", which sounds trivial
until you notice that a dense model will happily rank "RabbitMQ" above it. In
recruitment the exact token usually IS the requirement -- a JD asking for Kafka
means Kafka -- so dropping the lexical half to simplify would lose the case the
product is actually built around.

FUSION IS RANK-BASED, NOT SCORE-BASED
--------------------------------------
Reciprocal Rank Fusion, not a weighted sum of the two scores. A cosine distance
and a `ts_rank` are not on the same scale, are not on the same scale as each
other across two different queries, and are not even monotonically comparable --
so any fixed weighting is a number nobody can justify and everybody eventually
tunes by feel. RRF only reads the ORDER each retriever produced, which is the
only thing both of them actually agree is meaningful.

THE RERANKER IS DETERMINISTIC, AND SAYS SO
-------------------------------------------
The specification asks for a `bge-reranker` cross-encoder. No such service is
deployed for this product, and pretending otherwise would put a hard dependency
on a model that does not exist behind an interface that silently returns the
input order. What runs instead is a lexical affinity pass -- query term coverage
plus a section-type prior -- which is a real improvement over fusion alone and
is honest about what it is. `rerank` takes the scorer as a parameter, so
introducing a cross-encoder later is a one-line change at the call site rather
than a rewrite.

FRESHNESS AND VERSION ARE FILTERS, APPLIED BEFORE RANKING
----------------------------------------------------------
Superseded JD chunks are excluded by `source_version` BEFORE anything is ranked,
never trimmed afterwards. Filtering after ranking means a stale chunk can
occupy a slot in the top-k and push a live one out of it -- the retrieved set
would then be shorter than requested for a reason nobody can see.
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from typing import Callable, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.embeddings import EmbeddingError, embed
from app.services.rag import chunking

logger = logging.getLogger(__name__)

#: RRF's smoothing constant. 60 is the value from the original formulation and
#: the one every implementation uses; it is here as a named constant so nobody
#: has to wonder whether this one was tuned.
RRF_K = 60

#: How deep each retriever goes before fusion. Wider than the final k on
#: purpose: fusion can only promote a chunk that at least one retriever
#: surfaced, so a narrow candidate pool makes the two halves agree by
#: construction rather than by evidence.
CANDIDATE_DEPTH = 20

DEFAULT_TOP_K = 5

_WORD = re.compile(r"[a-z0-9+#.]+")

#: Section types worth more for a given query intent. A skills list matching a
#: skills query is nearly free information -- every skills list matches every
#: skills query -- while an EXPERIENCE paragraph mentioning the same skill is
#: evidence that the person used it.
_SECTION_PRIOR: dict[str, float] = {
    chunking.SECTION_EXPERIENCE: 1.0,
    chunking.SECTION_QA: 1.0,
    chunking.SECTION_RESPONSIBILITIES: 0.9,
    chunking.SECTION_PROSE: 0.85,
    chunking.SECTION_EDUCATION: 0.8,
    chunking.SECTION_SKILLS: 0.7,
}


@dataclass(frozen=True)
class RetrievedChunk:
    """One retrieved piece, with enough provenance for an agent to cite it."""

    chunk_id: uuid.UUID
    content: str
    source_type: str
    source_id: uuid.UUID
    section_type: str
    ordinal: int
    #: Fused rank score. Ordering information only; never shown to anyone and
    #: never mistaken for a relevance percentage.
    score: float = 0.0
    #: Which retrievers found it. `("semantic",)` alone during an embedding
    #: outage is the visible form of a degraded retrieval.
    retrievers: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "chunk_id": str(self.chunk_id),
            "content": self.content,
            "source_type": self.source_type,
            "source_id": str(self.source_id),
            "section_type": self.section_type,
            "ordinal": self.ordinal,
            "retrievers": list(self.retrievers),
        }


def _terms(value: str) -> set[str]:
    return {token for token in _WORD.findall(str(value or "").casefold()) if len(token) > 1}


def _vector_literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(f"{value:.7f}" for value in vector) + "]"


def _filters(
    source_type: str | None,
    source_ids: Sequence[uuid.UUID] | None,
    section_types: Sequence[str] | None,
    source_version: str | None,
) -> tuple[str, dict[str, object]]:
    clauses: list[str] = []
    params: dict[str, object] = {}
    if source_type:
        clauses.append("source_type = :source_type")
        params["source_type"] = source_type
    if source_ids:
        clauses.append("source_id = ANY(CAST(:source_ids AS uuid[]))")
        params["source_ids"] = [str(value) for value in source_ids]
    if section_types:
        clauses.append("section_type = ANY(:section_types)")
        params["section_types"] = list(section_types)
    if source_version:
        clauses.append("source_version = :source_version")
        params["source_version"] = source_version
    return (" AND " + " AND ".join(clauses) if clauses else ""), params


async def _semantic(
    session: AsyncSession, query: str, where: str, params: dict[str, object], depth: int
) -> list[uuid.UUID]:
    try:
        vectors = await embed([query])
    except EmbeddingError as exc:
        # Degraded, not failed. The keyword half still answers, and an agent
        # getting lexical-only context is enormously better than one getting
        # none because a GPU service restarted.
        logger.warning("rag.retrieval.embedding_unavailable err=%s", type(exc).__name__)
        return []

    rows = await session.execute(
        text(
            f"""
            SELECT id FROM context_chunks
             WHERE embedding IS NOT NULL {where}
             ORDER BY embedding <=> CAST(:query_vector AS vector)
             LIMIT :depth
            """
        ),
        {**params, "query_vector": _vector_literal(vectors[0]), "depth": depth},
    )
    return [row.id for row in rows]


def _tsquery(query: str) -> str:
    """Build an OR tsquery from a natural-language string.

    `plainto_tsquery` ANDs every term, and that is wrong for this retriever.
    Measured on the live index while verifying: the query "kafka partition
    rebalance migration" matched NOTHING in a resume containing Kafka,
    partition and migration, because it did not contain the word "rebalance".
    The lexical half exists to catch the exact token a JD demands; requiring the
    candidate document to contain every word an agent happened to phrase its
    query with means it almost never fires, and the failure is silent -- fusion
    still returns the semantic hits, so retrieval looks like it worked.

    OR is the right operator because precision is fusion's job, not this
    retriever's: a chunk matching one rare term ranks low on `ts_rank` and low
    in the fused order unless the semantic half agrees with it.

    Terms are reduced to alphanumerics before they reach `to_tsquery`, which
    parses operators from its input -- an unsanitised query containing `&` or
    `!` would be a syntax error at best.
    """
    terms = [term for term in _WORD.findall(str(query or "").casefold()) if len(term) > 1]
    return " | ".join(dict.fromkeys(term.replace(".", "").replace("#", "") for term in terms if term.strip(".#")))


async def _keyword(
    session: AsyncSession, query: str, where: str, params: dict[str, object], depth: int
) -> list[uuid.UUID]:
    tsquery = _tsquery(query)
    if not tsquery:
        return []
    rows = await session.execute(
        text(
            f"""
            SELECT id FROM context_chunks
             WHERE content_tsv @@ to_tsquery('english', :tsquery) {where}
             ORDER BY ts_rank(content_tsv, to_tsquery('english', :tsquery)) DESC
             LIMIT :depth
            """
        ),
        {**params, "tsquery": tsquery, "depth": depth},
    )
    return [row.id for row in rows]


def fuse(ranked_lists: dict[str, Sequence[uuid.UUID]]) -> dict[uuid.UUID, tuple[float, tuple[str, ...]]]:
    """Reciprocal Rank Fusion over any number of ranked id lists."""
    fused: dict[uuid.UUID, float] = {}
    found_by: dict[uuid.UUID, list[str]] = {}
    for retriever, ids in ranked_lists.items():
        for rank, chunk_id in enumerate(ids, start=1):
            fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
            found_by.setdefault(chunk_id, []).append(retriever)
    return {
        chunk_id: (score, tuple(found_by[chunk_id])) for chunk_id, score in fused.items()
    }


def lexical_affinity(query: str, chunk: RetrievedChunk) -> float:
    """The default reranking scorer: query term coverage, weighted by section.

    Coverage of the QUERY's terms rather than of the chunk's, so a long chunk
    does not win by containing many words. Multiplied by a section prior for the
    reason in the module docstring: a skills list matching a skills query proves
    nothing that its existence did not already prove.
    """
    query_terms = _terms(query)
    if not query_terms:
        return 0.0
    covered = len(query_terms & _terms(chunk.content)) / len(query_terms)
    return round(covered * _SECTION_PRIOR.get(chunk.section_type, 0.85), 6)


def rerank(
    query: str,
    chunks: Sequence[RetrievedChunk],
    *,
    top_k: int = DEFAULT_TOP_K,
    scorer: Callable[[str, RetrievedChunk], float] = lexical_affinity,
) -> list[RetrievedChunk]:
    """Reorder by a second, content-aware pass and take the top k.

    Ties fall back to the fused score, so reranking can only ever reorder within
    what fusion already considered plausible. A scorer that returns 0 for
    everything therefore degrades to fusion order rather than to arbitrary order.
    """
    scored = sorted(
        chunks,
        key=lambda chunk: (scorer(query, chunk), chunk.score),
        reverse=True,
    )
    return list(scored[:top_k])


async def retrieve(
    session: AsyncSession,
    query: str,
    *,
    source_type: str | None = None,
    source_ids: Sequence[uuid.UUID] | None = None,
    section_types: Sequence[str] | None = None,
    source_version: str | None = None,
    top_k: int = DEFAULT_TOP_K,
    depth: int = CANDIDATE_DEPTH,
    scorer: Callable[[str, RetrievedChunk], float] = lexical_affinity,
) -> list[RetrievedChunk]:
    """Hybrid retrieve, fuse, rerank. Returns at most `top_k` chunks."""
    query = " ".join(str(query or "").split())
    if not query:
        return []

    where, params = _filters(source_type, source_ids, section_types, source_version)

    semantic_ids = await _semantic(session, query, where, params, depth)
    keyword_ids = await _keyword(session, query, where, params, depth)

    fused = fuse({"semantic": semantic_ids, "keyword": keyword_ids})
    if not fused:
        return []

    rows = await session.execute(
        text(
            """
            SELECT id, content, source_type, source_id, section_type, ordinal
              FROM context_chunks
             WHERE id = ANY(CAST(:ids AS uuid[]))
            """
        ),
        {"ids": [str(chunk_id) for chunk_id in fused]},
    )
    candidates = [
        RetrievedChunk(
            chunk_id=row.id,
            content=row.content,
            source_type=row.source_type,
            source_id=row.source_id,
            section_type=row.section_type,
            ordinal=row.ordinal,
            score=fused[row.id][0],
            retrievers=fused[row.id][1],
        )
        for row in rows
    ]
    return rerank(query, candidates, top_k=top_k, scorer=scorer)
