"""BGE-M3 embedding client (ESD §8.1) — 1024-dim dense vectors.

If BGE_M3_ENDPOINT is configured, POSTs {"texts": [...]} and expects
{"embeddings": [[...1024 floats...], ...]} back.

If the endpoint is NOT configured (local dev without a GPU service), a
clearly-marked DETERMINISTIC DEV FALLBACK produces stable pseudo-random unit
vectors seeded from a hash of the text, so the whole matching pipeline runs
end-to-end locally. These vectors carry no semantic meaning — similarity
ordering is arbitrary but stable. Never rely on the fallback in production.
"""
from __future__ import annotations

import hashlib
import math
import random

import httpx

from app.core.config import get_settings

EMBEDDING_DIM = 1024
_REQUEST_TIMEOUT = 120.0


class EmbeddingError(RuntimeError):
    """The embedding endpoint failed or returned a malformed response."""


def _dev_fallback_vector(text: str) -> list[float]:
    """DEV FALLBACK ONLY — deterministic pseudo-random unit vector.

    Seeded from SHA-256 of the text so the same text always embeds to the same
    vector (idempotent seeds, reproducible tests). Not semantically meaningful.
    """
    seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")
    rng = random.Random(seed)
    vec = [rng.gauss(0.0, 1.0) for _ in range(EMBEDDING_DIM)]
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


async def embed(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts into 1024-dim vectors.

    Uses the real BGE-M3 service when BGE_M3_ENDPOINT is set, otherwise the
    deterministic dev fallback (see module docstring).
    """
    if not texts:
        return []

    endpoint = get_settings().bge_m3_endpoint
    if not endpoint:
        # ── DEV FALLBACK (no BGE_M3_ENDPOINT configured) ──
        return [_dev_fallback_vector(t) for t in texts]

    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
        try:
            resp = await client.post(endpoint, json={"texts": texts})
            resp.raise_for_status()
            embeddings = resp.json()["embeddings"]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise EmbeddingError(f"BGE-M3 endpoint failure: {type(exc).__name__}") from exc

    if len(embeddings) != len(texts) or any(len(e) != EMBEDDING_DIM for e in embeddings):
        raise EmbeddingError(
            f"BGE-M3 returned a malformed batch (expected {len(texts)} x {EMBEDDING_DIM})"
        )
    return embeddings
