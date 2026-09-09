"""One bounded second look before a thin retrieval becomes an empty prompt.

RPN-AI-UP-001 W6.5, and the shape of the problem first: the sufficiency gates
in `services/generation_sufficiency` decide whether a generator RUNS, and their
insufficient verdict returns a fixed key from `EMPTY_STATE_COPY`. That contract
is correct and this module does not touch it. What was missing sits one layer
earlier, at ACQUISITION: when retrieval hands back nothing, or less than the
caller's floor, the product went straight to the thin result without ever
asking whether a slightly wider net would have caught what exists.

INVESTIGATED BEFORE BUILT, AND THE INVESTIGATION MOVED THE MODULE
-------------------------------------------------------------------
The gated generators' evidence does not come from `rag/retrieval` at all: the
company profile reads a web-research pack, the JD reads a typed brief, the
lifecycle emails read record fields. The one place the brief's broadening
moves (a wider pool, a raised top_k, the section filter dropped) actually
EXIST is this retrieval stack, whose consumer is the `retrieve_context` tool.
So the loop lives here, where the levers are, and not inside the gates, which
receive evidence as input and have no second source to ask.

BOUNDED BY STRUCTURE, NOT BY A COUNTER
----------------------------------------
There are exactly two attempts, written out in straight-line code: the
caller's own request, and one broadened retry. No loop exists to bound. The
deadline follows the standing predictive rule (`agent_loop`, `llm_router`,
the worker runtime): the broadened attempt is REFUSED, before it starts, when
the time already spent plus the first attempt's own duration would overrun
the budget, because an attempt that cannot finish inside its budget is worse
than no attempt: it spends the whole budget AND returns nothing.

WHAT BROADENING IS, PRECISELY
-------------------------------
The section filter is dropped and the pool is widened (top_k and candidate
depth doubled, capped). The QUERY is never rewritten: this module calls no
model, so a rewritten query would be a heuristic mangling the one thing the
caller stated precisely, and a model call here would put a vendor's latency
and failure modes inside every retrieval. The tenant boundary, the source
type, the source ids and the version pin are NEVER broadened: those are
scope, and a second attempt that widened whose documents may be read would be
an isolation bug wearing a recall improvement's clothes.

THE OUTCOME IS RECORDED, NEVER SILENT
---------------------------------------
`AcquisitionOutcome` says how many attempts ran, whether the second was
broadened or refused and why, and whether the floor was met, in the same
spirit as `RerankOutcome`. A still-insufficient result returns whatever WAS
found: the downstream sufficiency gate keeps its own authority to say "not
enough", and this module never manufactures a sufficiency it did not achieve.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.rag import reranker
from app.services.rag.retrieval import (
    CANDIDATE_DEPTH,
    DEFAULT_TOP_K,
    RetrievedChunk,
    lexical_affinity,
    retrieve_with_record,
)

logger = logging.getLogger(__name__)

__all__ = ["AcquisitionOutcome", "BROADENED_TOP_K_CAP", "acquire"]

#: The broadened attempt doubles the caller's ask, to here and no further. A
#: cap because "broaden" must not become "fetch the tenant's whole index":
#: context assembly drops whole chunks over budget anyway, so an unbounded
#: widening would spend database time fetching rows assembly then discards.
BROADENED_TOP_K_CAP = 20

#: Why the second attempt did not run, named so an operator reading the record
#: does not have to guess which of two different facts happened.
REFUSED_DEADLINE = "deadline_would_be_exceeded"
NOT_NEEDED = "first_attempt_sufficient"


@dataclass(frozen=True)
class AcquisitionOutcome:
    """What acquisition did, in the words a record should carry."""

    chunks: list[RetrievedChunk]
    rerank: reranker.RerankOutcome
    #: 1 or 2. There is no third value and no code path that could make one.
    attempts: int
    #: True when the broadened retry actually ran.
    broadened: bool
    #: Whether the FINAL result met the caller's floor. False stops nothing:
    #: the chunks still return, and the downstream sufficiency gate keeps its
    #: own authority over what to do with a thin result.
    sufficient: bool
    #: Why the second attempt did not run, when it did not. None when it ran.
    skipped_reason: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "attempts": self.attempts,
            "broadened": self.broadened,
            "sufficient": self.sufficient,
            "skipped_reason": self.skipped_reason,
            "rerank": self.rerank.as_dict(),
        }


async def acquire(
    session: AsyncSession,
    query: str,
    *,
    min_chunks: int = 1,
    source_type: str | None = None,
    source_ids: Sequence[uuid.UUID] | None = None,
    section_types: Sequence[str] | None = None,
    source_version: str | None = None,
    top_k: int = DEFAULT_TOP_K,
    deadline_seconds: float = 4.0,
    scorer: Callable[[str, RetrievedChunk], float] = lexical_affinity,
) -> AcquisitionOutcome:
    """Retrieve; if thinner than `min_chunks`, one broadened retry, bounded.

    `min_chunks` defaults to 1: the floor every caller shares is "never build
    a prompt from nothing without one wider look". A caller with a stronger
    floor states it.

    `deadline_seconds` is the budget for the WHOLE acquisition. The first
    attempt is the caller's own request and always runs; the broadened attempt
    is the extra work this module adds, and is what the deadline refuses.
    """
    started = time.monotonic()
    chunks, rerank_record = await retrieve_with_record(
        session,
        query,
        source_type=source_type,
        source_ids=source_ids,
        section_types=section_types,
        source_version=source_version,
        top_k=top_k,
        scorer=scorer,
    )
    first_duration = time.monotonic() - started

    if len(chunks) >= min_chunks:
        return AcquisitionOutcome(
            chunks=chunks,
            rerank=rerank_record,
            attempts=1,
            broadened=False,
            sufficient=True,
            skipped_reason=NOT_NEEDED,
        )

    # The predictive refusal, BEFORE the attempt rather than after it. The
    # broadened pass does strictly more work than the first, so the first
    # attempt's own duration is the honest floor for what the second will
    # cost. `elapsed + longest_attempt_so_far >= budget` is the same rule the
    # router and the agent loop already state, for the same reason.
    elapsed = time.monotonic() - started
    if elapsed + first_duration >= deadline_seconds:
        logger.info(
            "rag.acquisition.broadening_refused elapsed_ms=%d first_ms=%d "
            "deadline_ms=%d found=%d floor=%d",
            elapsed * 1000, first_duration * 1000, deadline_seconds * 1000,
            len(chunks), min_chunks,
        )
        return AcquisitionOutcome(
            chunks=chunks,
            rerank=rerank_record,
            attempts=1,
            broadened=False,
            sufficient=False,
            skipped_reason=REFUSED_DEADLINE,
        )

    # THE ONE BROADENED ATTEMPT. Wider pool, section filter dropped. The
    # query, the tenant boundary (RLS), the source type, the source ids and
    # the version pin are all UNCHANGED: those are scope, never broadened.
    broadened_top_k = min(max(top_k * 2, min_chunks), BROADENED_TOP_K_CAP)
    broadened_chunks, broadened_record = await retrieve_with_record(
        session,
        query,
        source_type=source_type,
        source_ids=source_ids,
        section_types=None,
        source_version=source_version,
        top_k=broadened_top_k,
        depth=CANDIDATE_DEPTH * 2,
        scorer=scorer,
    )

    # The broadened ask is a superset, so its result REPLACES rather than
    # merges: merging two ranked lists would need a second fusion step, and
    # the first list's members are still candidates in the second's pool.
    # On a tie the first result wins, because it honoured the caller's own
    # filters.
    final = broadened_chunks if len(broadened_chunks) > len(chunks) else chunks
    final_record = broadened_record if final is broadened_chunks else rerank_record
    sufficient = len(final) >= min_chunks
    logger.info(
        "rag.acquisition.broadened found=%d after=%d floor=%d sufficient=%s",
        len(chunks), len(final), min_chunks, sufficient,
    )
    return AcquisitionOutcome(
        chunks=final,
        rerank=final_record,
        attempts=2,
        broadened=True,
        sufficient=sufficient,
        skipped_reason=None,
    )
