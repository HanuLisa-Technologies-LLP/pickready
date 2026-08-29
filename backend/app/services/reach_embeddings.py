"""AI Reach role embeddings -- now the platform's ONE embedding model.

WHY THIS MODULE STILL EXISTS AFTER THE CONSOLIDATION
------------------------------------------------------
It used to be a genuinely separate embedding stack: `BAAI/bge-small-en-v1.5`
running on CPU through `fastembed`, 384 dimensions, model weights baked into the
container image. spec-doc5 §B.2 ends that -- voyage-context-4 is the "sole
embedding model for every RAG surface in the platform", and AI Reach's role
search is a RAG surface like any other.

So what is left here is not a model. It is the ROLE-SEARCH VOCABULARY: the
asymmetric query/passage split that role matching depends on, the typed error
that `bd_leads` catches to fall back to exact distinctive-role matching, and the
width constant that `jobs.reach_embedding` is declared against. Those are
`bd_leads`' contract with its embedding layer, and collapsing them into
`services/embeddings` would push a BD-portal concern into the shared module and
leave `bd_leads` catching `EmbeddingError` -- a class raised by resume parsing
and by context-chunk indexing too, which would make its degradation path fire on
failures that have nothing to do with role search.

TWO VECTOR SPACES WERE ONE MODEL TOO MANY
------------------------------------------
`jobs.reach_embedding` was deliberately kept separate from `jobs.embedding`, and
that separation was right for a reason that has not gone away: the two answer
different questions (which role is semantically like this one, versus which
candidate is like this job) and are invalidated by different triggers. The
column stays separate. What is no longer separate is the MODEL underneath it,
because two models mean two vector spaces that look interchangeable in the
schema and are not -- a cosine distance computed across them is a number with no
meaning, and nothing about it looks wrong.

Migration 0058 widens the column to 1024 and NULLs every existing vector. The
NULLing is not data loss to be apologised for: a bge-small vector and a Voyage
vector share nothing but a name, so keeping them would be keeping numbers that
cannot be compared to the new ones. `bd_leads` already re-embeds any candidate
whose vector is NULL on the next search, so the repair is automatic and needs no
backfill job.
"""

from __future__ import annotations

from typing import Sequence

from app.config.llm_providers import EMBEDDING_MODEL
from app.services import embeddings

#: Named for the reader who arrives from `bd_leads` or from migration 0042 and
#: wants to know what is actually being called.
MODEL_NAME = EMBEDDING_MODEL

#: Must equal `jobs.reach_embedding`'s declared width. Sourced from the shared
#: module rather than restated, so the two cannot drift: a mismatch would not
#: raise at write time, it would raise on the first search, on a machine that is
#: not the one where somebody changed a constant.
EMBEDDING_DIM = embeddings.EMBEDDING_DIM


class ReachEmbeddingError(RuntimeError):
    """Role-search embedding was unavailable.

    Deliberately NOT `embeddings.EmbeddingError`, and deliberately not replaced
    by it. `bd_leads` catches this to fall back to exact distinctive-role
    matching, which is the honest degraded answer for role search and the wrong
    answer for anything else. Catching the shared class instead would make that
    fallback fire on a resume-parsing failure.
    """


def _wrap(exc: Exception) -> ReachEmbeddingError:
    # The message carries the failure CLASS and never the underlying text: an
    # embedding request body carries a real job description.
    return ReachEmbeddingError(type(exc).__name__)


async def embed_query(text: str) -> list[float]:
    """Embed the role a BD rep typed.

    Uses the QUERY input type. Voyage embeds a query and a document
    asymmetrically, and getting this backwards is a silent quality regression:
    the search keeps returning roles, they are just less relevant ones.
    """
    try:
        vector = await embeddings.embed_query(text)
    except Exception as exc:  # noqa: BLE001 -- re-raised as the typed error
        raise _wrap(exc) from exc
    if len(vector) != EMBEDDING_DIM:
        raise ReachEmbeddingError("malformed query embedding")
    return vector


async def embed_passages(texts: Sequence[str]) -> list[list[float]]:
    """Embed the roles already in the platform, as DOCUMENTS."""
    if not texts:
        return []
    try:
        vectors = await embeddings.embed(
            list(texts), input_type=embeddings.INPUT_TYPE_DOCUMENT
        )
    except Exception as exc:  # noqa: BLE001
        raise _wrap(exc) from exc
    if len(vectors) != len(texts) or any(
        len(vector) != EMBEDDING_DIM for vector in vectors
    ):
        raise ReachEmbeddingError("malformed passage embedding batch")
    return vectors
