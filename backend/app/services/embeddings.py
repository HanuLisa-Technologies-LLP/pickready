"""The platform's ONE embedding client: voyage-context-4 (spec-doc5 §B.2).

Every RAG surface in ReadyPick embeds through this module -- candidate profile
vectors, job vectors, `context_chunks`, the department competency models, the
Company DNA artifacts, the rubric anchors, the skills ontology and the
validation probe bank. One model, one place, no per-surface choice, because two
embedding models in one platform means two vector spaces that look
interchangeable in the schema and are not: a cosine distance computed between a
vector from model A and a vector from model B is a number with no meaning, and
nothing about it looks wrong.

WHAT THIS REPLACED
------------------
A self-hosted BGE-M3 endpoint (`BGE_M3_ENDPOINT`), with a deterministic
pseudo-random dev fallback when it was unset. The endpoint is gone; the fallback
is NOT, and the reason is worth stating because deleting it would have been the
obvious tidy-up.

1024 DIMENSIONS, AND WHY THAT IS NOT A PREFERENCE
--------------------------------------------------
`profiles.embedding`, `jobs.embedding` and `context_chunks.embedding` are
`vector(1024)` columns that already hold rows. Voyage's context family supports
an explicit `output_dimension`, and 1024 is both its default for this model and
what the schema is built for, so the swap needs no migration and no re-embed of
historical rows to remain STORABLE.

It does not follow that the old and new vectors are COMPARABLE, and they are
not -- a BGE-M3 vector and a Voyage vector share a width and nothing else.
`scripts/reembed_corpus.py` is the migration for that, and until it has run over
a given tenant, retrieval on that tenant mixes two spaces. This is called out
here rather than buried in a script because a same-width swap is exactly the
kind of change that looks free and silently degrades ranking; `stale_embeddings`
already exists to find rows that need re-embedding, and this is one more reason
a row can be stale.

THE DEV FALLBACK IS KEPT, DELIBERATELY
---------------------------------------
With no `VOYAGE_API_KEY` the module returns deterministic pseudo-random unit
vectors seeded from a hash of the text. That is not laziness: without it the
entire matching pipeline, the whole test suite and every local dev run would
require a paid credential to execute at all, and CI would become a thing that
can fail because a vendor is down. The vectors carry NO semantic meaning --
similarity ordering is arbitrary but stable -- and `is_semantic()` exists so any
caller that must not present a fallback ranking as a result can ask.

Never rely on the fallback in production. `scripts/validate_stack.py` reports it
as unconfigured for exactly that reason.
"""
from __future__ import annotations

import hashlib
import logging
import math
import random

import httpx

from app.config.llm_providers import EMBEDDING_MODEL, VOYAGE_EMBEDDINGS_URL
from app.core.config import get_settings
from app.services.reliability import vendor_contract

#: Must equal the width of every `vector(N)` column in the schema. Asserted in
#: `tests/test_embeddings.py` against the model definitions rather than trusted,
#: because a mismatch here does not raise at write time on every backend -- it
#: raises at query time, on a machine that is not the one that changed it.
EMBEDDING_DIM = 1024

#: Voyage's documented per-request batch ceiling for the context family. Batches
#: larger than this are split and re-joined in order, so a caller embedding a
#: whole resume corpus never has to know the limit exists.
MAX_BATCH = 128

_REQUEST_TIMEOUT = 60.0

#: What the vectors are FOR. Voyage asymmetric models embed a query and a
#: document differently, and getting this backwards is a silent quality
#: regression: the retrieval still returns results, they are just worse.
INPUT_TYPE_DOCUMENT = "document"
INPUT_TYPE_QUERY = "query"

logger = logging.getLogger(__name__)


class EmbeddingError(RuntimeError):
    """The embedding endpoint failed or returned a malformed response."""


def is_semantic() -> bool:
    """True when a real embedding model is configured.

    Callers that would otherwise present a fallback ordering as a judgement --
    `matching`'s reasoning trace, the AI Reach segment, `validate_stack` -- read
    this rather than inferring it, so "we could not embed" never renders as "we
    embedded and this is the order".
    """
    return bool(get_settings().voyage_api_key)


def _dev_fallback_vector(text: str) -> list[float]:
    """DEV FALLBACK ONLY -- a deterministic pseudo-random unit vector.

    Seeded from SHA-256 of the text so the same text always embeds to the same
    vector (idempotent seeds, reproducible tests). Not semantically meaningful.
    """
    seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")
    rng = random.Random(seed)
    vec = [rng.gauss(0.0, 1.0) for _ in range(EMBEDDING_DIM)]
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


async def _embed_batch(
    client: httpx.AsyncClient, api_key: str, texts: list[str], input_type: str
) -> list[list[float]]:
    try:
        resp = await client.post(
            VOYAGE_EMBEDDINGS_URL,
            # The key travels in a header, never a query string, for the same
            # reason it does in `llm_router`: a query parameter lands in httpx's
            # own INFO log line and from there into the log sink in plain text.
            headers={
                "Authorization": f"Bearer {api_key}",
                "content-type": "application/json",
            },
            json={
                "model": EMBEDDING_MODEL,
                "input": texts,
                "input_type": input_type,
                # Explicit rather than defaulted. The schema depends on this
                # number, so it must be stated at the call rather than inherited
                # from whatever the vendor's default happens to be next year.
                "output_dimension": EMBEDDING_DIM,
            },
        )
        resp.raise_for_status()
        payload = resp.json()
    except httpx.HTTPError as exc:
        # The message names the failure class and never the response body: an
        # embedding request carries a real candidate's resume text.
        raise EmbeddingError(f"Voyage endpoint failure: {type(exc).__name__}") from exc
    except ValueError as exc:
        raise EmbeddingError("Voyage returned a non-JSON response") from exc

    try:
        # Voyage returns `data` ordered by an explicit `index`, and it is sorted
        # rather than trusted to arrive in order. An out-of-order batch would
        # attach every candidate's vector to the next candidate's row, which is
        # a data corruption that no test of the happy path would ever show.
        rows = sorted(payload["data"], key=lambda row: int(row["index"]))
        vectors = [list(row["embedding"]) for row in rows]
    except (KeyError, TypeError, ValueError) as exc:
        raise EmbeddingError("Voyage returned a malformed payload") from exc

    # FAIL LOUD ON FIRST LIVE USE (spec-doc6 §12.5). Everything above was
    # written against Voyage's PUBLISHED schema and has never been seen from
    # the endpoint. This runs once per process and catches what the parse above
    # cannot: an index set that is not 0..n-1 (duplicate indices sort perfectly
    # well and silently yield the same vector twice), a short batch, and a
    # response echoing a different model id -- which is a vector-space
    # corruption that leaves every column the right width and every distance
    # computable. It runs AFTER the parse so the typed `EmbeddingError` above
    # keeps its meaning for the shapes it already covers.
    #
    # `expected_dimension=None` on purpose: `embed` already enforces the width
    # with a typed error, and one rule implemented twice is the fault
    # `services/tiers.py` cost this platform.
    vendor_contract.check_voyage_response(
        payload, expected_rows=len(texts), expected_dimension=None
    )
    return vectors


async def embed(
    texts: list[str], *, input_type: str = INPUT_TYPE_DOCUMENT
) -> list[list[float]]:
    """Embed a batch of texts into `EMBEDDING_DIM`-dim vectors.

    Uses voyage-context-4 when `VOYAGE_API_KEY` is set, otherwise the
    deterministic dev fallback (see the module docstring). Order is preserved:
    `embed(texts)[i]` is always the vector for `texts[i]`.
    """
    if not texts:
        return []

    api_key = (get_settings().voyage_api_key or "").strip()
    if not api_key:
        # ── DEV FALLBACK (no VOYAGE_API_KEY configured) ──
        return [_dev_fallback_vector(t) for t in texts]

    embeddings: list[list[float]] = []
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
        for batch in _chunks(texts, MAX_BATCH):
            embeddings.extend(await _embed_batch(client, api_key, batch, input_type))

    if len(embeddings) != len(texts) or any(len(e) != EMBEDDING_DIM for e in embeddings):
        raise EmbeddingError(
            f"Voyage returned a malformed batch "
            f"(expected {len(texts)} x {EMBEDDING_DIM})"
        )
    return embeddings


async def embed_query(text: str) -> list[float]:
    """Embed one retrieval QUERY.

    Split from `embed` rather than left to a keyword argument at every call
    site, because the document/query distinction is easy to forget and its
    failure mode is invisible: retrieval keeps working and simply gets worse.
    """
    return (await embed([text], input_type=INPUT_TYPE_QUERY))[0]
