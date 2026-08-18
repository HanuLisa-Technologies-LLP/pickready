"""The context engine: chunk, index, retrieve, assemble.

The one thing worth knowing before reading any of it: the whole-document
vectors this product already had (`profiles.embedding`, `jobs.embedding`) are
untouched and still rank candidates. This package answers a different question
-- which PARTS of these documents bear on this query -- and the two must not be
confused, because a ranking prior that starts depending on chunk retrieval
would change who gets scored, and retrieval must never decide that.
"""
from __future__ import annotations

from app.services.rag import chunking, context, index, retrieval
from app.services.rag.chunking import Chunk, chunk_jd, chunk_resume, chunk_text
from app.services.rag.context import AssembledContext, assemble, estimate_tokens
from app.services.rag.index import IndexResult, index_document
from app.services.rag.retrieval import RetrievedChunk, retrieve

__all__ = [
    "AssembledContext",
    "Chunk",
    "IndexResult",
    "RetrievedChunk",
    "assemble",
    "chunk_jd",
    "chunk_resume",
    "chunk_text",
    "chunking",
    "context",
    "estimate_tokens",
    "index",
    "index_document",
    "retrieval",
    "retrieve",
]
