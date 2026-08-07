"""CPU-local semantic embeddings dedicated to AI Reach role search."""

from __future__ import annotations

import asyncio
import os
from typing import Sequence

MODEL_NAME = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIM = 384
_model_instance = None


class ReachEmbeddingError(RuntimeError):
    pass


def _model():
    global _model_instance
    if _model_instance is None:
        from fastembed import TextEmbedding

        _model_instance = TextEmbedding(
            model_name=MODEL_NAME,
            cache_dir=os.getenv("FASTEMBED_CACHE_PATH") or None,
        )
    return _model_instance


async def embed_query(text: str) -> list[float]:
    def run() -> list[float]:
        return list(next(iter(_model().query_embed(text))).tolist())

    try:
        vector = await asyncio.to_thread(run)
    except Exception as exc:  # noqa: BLE001
        raise ReachEmbeddingError(type(exc).__name__) from exc
    if len(vector) != EMBEDDING_DIM:
        raise ReachEmbeddingError("malformed query embedding")
    return vector


async def embed_passages(texts: Sequence[str]) -> list[list[float]]:
    if not texts:
        return []

    def run() -> list[list[float]]:
        return [list(vector.tolist()) for vector in _model().passage_embed(list(texts))]

    try:
        vectors = await asyncio.to_thread(run)
    except Exception as exc:  # noqa: BLE001
        raise ReachEmbeddingError(type(exc).__name__) from exc
    if len(vectors) != len(texts) or any(
        len(vector) != EMBEDDING_DIM for vector in vectors
    ):
        raise ReachEmbeddingError("malformed passage embedding batch")
    return vectors
