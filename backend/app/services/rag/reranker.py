"""The second-pass reranker, and the record of when it did not run.

RPN-AI-UP-001 W6.1. `retrieval.rerank` has always taken its scorer as a
PARAMETER and defaulted to a deterministic lexical-affinity pass, and
`retrieval`'s own docstring says why: no cross-encoder service was deployed for
this product, and "pretending otherwise would put a hard dependency on a model
that does not exist behind an interface that silently returns the input order".
This module is the drop-in that design was built for.

WHY VOYAGE, AND WHY `rerank-2.5`
---------------------------------
Voyage rather than Cohere because this platform already treats Voyage as a
vendor for `voyage-4`. A second reranking vendor would be a second credential,
a second breaker and a second outage mode for ONE behaviour, which is the
argument the single-vendor consolidation already made for the chat models.

`rerank-2.5` and not `rerank-3`, which is Preview. A preview model under a
grade-adjacent pipeline is the `voyage-context-4` mistake repeated: an id
enshrined as a hard rule in `claude.md`, cited in nine modules and pinned by
tests, which never existed and never failed loudly because the embedding client
returned pseudo-random unit vectors when the key was absent.

THE DEGRADATION IS RECORDED, NEVER SILENT
------------------------------------------
When the deployment asks for `voyage` and the reranker cannot answer, the
lexical pass runs and the outcome carries `reranker="lexical", degraded=True`
with a reason. A cross-encoder that did not run must not read as one that did:
that is "no template output presented as generation" applied to ranking.

The converse matters just as much and is easy to get backwards. A deployment
configured `RETRIEVAL_RERANKER=lexical` is NOT degraded. It chose the lexical
pass, and reporting a chosen configuration as a degradation would make the
signal that flags a real outage fire constantly and stop being read.

WHICH BACKEND RUNS IS DEPLOYMENT DATA
--------------------------------------
One value per deployment, never a fallback chain, the same shape as
`TASK_DISPATCH_BACKEND` and `email_transport`. `configured_backend` RAISES on a
value outside the closed set, exactly as `retrieval._apply_scan_settings` does
for `hnsw.iterative_scan`: the value came from deployment configuration, a
wrong one is an operator error, and continuing on a default would leave ranking
quietly changed with nothing recording that a choice had been made for the
operator.

WHERE IT RUNS
-------------
`pickready.run_matching` is `Route.ECS` and measured in minutes, so a 50 to
200ms round trip over roughly fifty documents is absorbed with no interactive
cap at risk. This module is not on any interactive path and must not be put on
one without its own timeout budget being re-argued.

THE SDK OWNS NO RETRIES
------------------------
`max_retries=0`. Two retry mechanisms stacked MULTIPLY, which is the lesson
`workers/runtime.run_task` records against the platform's own retry
configuration. One bounded attempt, and a failure becomes a recorded
degradation rather than three silent ones.

WHAT THIS MODULE DOES NOT TOUCH
--------------------------------
Nothing here reads, writes or influences a score, a grade, a band or a matrix.
Reranking is a RANKING PRIOR: it changes which evidence an agent reads first,
never what that evidence is worth. `tests/test_retrieval_scoring_isolation.py`
asserts the import graph in both directions.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Callable, Protocol, Sequence, TypeVar

import voyageai
import voyageai.error

from app.core.config import get_settings

logger = logging.getLogger(__name__)

#: The reranking model. STABLE, deliberately. See the module docstring for why
#: `rerank-3` is refused: it is Preview, and this pipeline is grade-adjacent.
RERANK_MODEL = "rerank-2.5"

#: The two backends, and the closed set `configured_backend` validates against.
RERANKER_VOYAGE = "voyage"
RERANKER_LEXICAL = "lexical"
BACKENDS: frozenset[str] = frozenset({RERANKER_VOYAGE, RERANKER_LEXICAL})

#: The `Settings` attribute holding the choice, populated from the environment
#: variable of the same name uppercased (`RETRIEVAL_RERANKER`). Named here
#: rather than spelled at three call sites so the error message and the reader
#: cannot disagree about which variable is wrong.
SETTING_ATTR = "retrieval_reranker"

#: The credential, named after the model it unlocks. Same convention as
#: `VOYAGE_CONTEXT_4` for `voyage-4` and `OPENAI_GPT_TERRA` for the reasoning
#: tier: spec-doc5 B.2 made every credential name the thing it opens, so an
#: absent key names the capability that is missing rather than a vendor.
CREDENTIAL_ATTR = "voyage_rerank_2_5"

#: One bounded attempt. Generous against the 50 to 200ms the vendor documents
#: for this document count, tight enough that a hung endpoint cannot hold an
#: ECS task open: an unreachable endpoint that HANGS defeats every `try/except`
#: around the call, because nothing is ever raised for the handler to catch.
REQUEST_TIMEOUT_SECONDS = 10.0

#: Reasons a `voyage` deployment fell back to the lexical pass. Named constants
#: rather than free strings, because these land in a run record a person reads
#: and two spellings of one condition are two conditions to them.
REASON_NO_CREDENTIAL = "credential_not_configured"
REASON_VENDOR_ERROR = "vendor_error"
REASON_TIMEOUT = "timeout"
REASON_MALFORMED = "malformed_response"


class Rerankable(Protocol):
    """What the reranker needs from a chunk, and nothing else.

    A structural type rather than an import of `retrieval.RetrievedChunk`,
    for one concrete reason: `retrieval` calls this module, so importing its
    dataclass back would be a cycle. It also keeps the contract honest --
    reranking reads TEXT and a fused rank, and has no business seeing a tenant,
    a candidate or a source id.
    """

    @property
    def content(self) -> str: ...

    @property
    def score(self) -> float: ...


ChunkT = TypeVar("ChunkT", bound=Rerankable)

#: The lexical pass's shape: (query, chunk) -> affinity. Supplied by the caller
#: rather than imported from `retrieval`, for the cycle reason above and
#: because `retrieval.rerank` already established that the scorer is a
#: parameter.
LexicalScorer = Callable[[str, ChunkT], float]


@dataclass(frozen=True)
class RerankOutcome:
    """The reordered chunks, and an honest statement of what produced them."""

    chunks: list
    #: `voyage` or `lexical`. What ACTUALLY ran, never what was configured.
    reranker: str
    #: True only when a `voyage` deployment could not reach the cross-encoder.
    #: A `lexical` deployment running the lexical pass is not degraded.
    degraded: bool = False
    #: Why, when degraded. None otherwise; there is nothing to explain.
    reason: str | None = None

    def as_dict(self) -> dict[str, object]:
        """The run record W6.1 asks for: `reranker` and `degraded`, always both.

        `reason` is included so an operator reading a degraded run does not
        have to correlate it with a log line to find out which of "no
        credential" and "the vendor answered 500" happened.
        """
        return {
            "reranker": self.reranker,
            "degraded": self.degraded,
            "reason": self.reason,
        }


class RerankUnavailable(RuntimeError):
    """The cross-encoder could not answer this call.

    Its own type so the caller can tell a vendor problem from a programming
    error. It is caught in exactly one place, `rerank_chunks`, and turned into
    a recorded degradation; it is never swallowed anywhere else.
    """

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason


def configured_backend() -> str:
    """Which reranker THIS deployment runs. Raises on anything else.

    `getattr` with an empty default followed by an unconditional membership
    check is not a fallback: an ABSENT setting and a MISSPELLED one produce the
    same loud error naming the variable, which is the outcome an operator can
    act on. The alternative -- defaulting to the lexical pass -- would let a
    deployment that believes it runs a cross-encoder run the placeholder
    forever, which is the precise failure this workstream exists to end.
    """
    raw = str(getattr(get_settings(), SETTING_ATTR, "") or "").strip().lower()
    if raw not in BACKENDS:
        raise ValueError(
            f"{SETTING_ATTR.upper()} must be one of {sorted(BACKENDS)}, not "
            f"{raw!r}. It is deployment data, one value per deployment, never "
            "a fallback chain."
        )
    return raw


def _credential() -> str:
    return str(getattr(get_settings(), CREDENTIAL_ATTR, "") or "").strip()


def _client(api_key: str) -> voyageai.AsyncClient:
    """A client with an explicit timeout and no retries of its own.

    Both arguments are stated rather than defaulted. The SDK's default is zero
    retries today, and a dependency bump that changed it would silently
    multiply against `workers/runtime`'s loop; stating it means the bump
    changes nothing here.
    """
    return voyageai.AsyncClient(
        api_key=api_key,
        max_retries=0,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )


async def _voyage_order(
    query: str, documents: Sequence[str], *, top_k: int
) -> list[int]:
    """Ask the cross-encoder to order `documents`, returning INDEXES into them.

    Indexes rather than the reranked text: the caller holds the chunk objects
    with their provenance, and matching returned strings back to chunks would
    fail on two chunks that happen to share text, which is exactly what a
    chunker with deliberate overlap produces.

    Raises `RerankUnavailable` for every condition under which the vendor could
    not answer. Nothing here returns the input order on failure -- that
    decision, and its record, belong to `rerank_chunks`.
    """
    api_key = _credential()
    if not api_key:
        raise RerankUnavailable(
            REASON_NO_CREDENTIAL,
            f"{CREDENTIAL_ATTR.upper()} is not set",
        )

    client = _client(api_key)
    try:
        result = await client.rerank(
            query=query,
            documents=list(documents),
            model=RERANK_MODEL,
            top_k=top_k,
            # The vendor truncates an over-long document rather than refusing
            # the batch. A refused batch would degrade the whole retrieval
            # because one resume paragraph ran long, which is a worse trade
            # than reranking that paragraph on its first few thousand tokens.
            truncation=True,
        )
    except voyageai.error.VoyageError as exc:
        # The message names the failure CLASS and never the response body: a
        # rerank request carries a real candidate's resume text, and this
        # string reaches a log sink far more widely readable than the database.
        raise RerankUnavailable(REASON_VENDOR_ERROR, type(exc).__name__) from exc
    except asyncio.TimeoutError as exc:
        raise RerankUnavailable(REASON_TIMEOUT, type(exc).__name__) from exc

    try:
        order = [int(item.index) for item in result.results]
    except (AttributeError, TypeError, ValueError) as exc:
        raise RerankUnavailable(REASON_MALFORMED, type(exc).__name__) from exc

    # A returned index outside the batch would silently attach one candidate's
    # ranking to another candidate's chunk, which is a data-correctness fault
    # that no test of the happy path would show. Duplicates are refused for the
    # same reason: they sort perfectly well and yield one chunk twice, which an
    # agent counting corroboration would count as two sources.
    if any(index < 0 or index >= len(documents) for index in order) or len(
        set(order)
    ) != len(order):
        raise RerankUnavailable(
            REASON_MALFORMED,
            f"index set is not a subset of 0..{len(documents) - 1}",
        )
    return order


def lexical_order(
    query: str, chunks: Sequence[ChunkT], scorer: LexicalScorer, *, top_k: int
) -> list[ChunkT]:
    """The deterministic pass. THE one implementation, not a second copy.

    This is `retrieval.rerank`'s body, moved rather than duplicated: two
    implementations of one behaviour is the fault `services/tiers.py` cost this
    platform, and a lexical pass that drifted from the one the degraded path
    falls back to would make a degradation change the ranking twice over.
    Public for that reason -- callers that want the lexical pass alone, and the
    tests that pin its behaviour, come here.

    Ties fall back to the fused score, so this can only ever reorder within
    what fusion already considered plausible, and a scorer returning 0 for
    everything degrades to fusion order rather than to arbitrary order.
    """
    ordered = sorted(
        chunks,
        key=lambda chunk: (scorer(query, chunk), chunk.score),
        reverse=True,
    )
    return list(ordered[:top_k])


async def rerank_chunks(
    query: str,
    chunks: Sequence[ChunkT],
    *,
    top_k: int,
    lexical_scorer: LexicalScorer,
) -> RerankOutcome:
    """Reorder `chunks` for `query`, and say which reranker did it.

    The ONE entry point. Callers do not choose a backend: the deployment does,
    and a caller that could choose would be a second answer to a question this
    platform answers with configuration.
    """
    backend = configured_backend()
    candidates = list(chunks)
    if not candidates:
        # Not a degradation and not a special case: nothing was retrieved, so
        # nothing needed ordering. The backend is still reported, because a run
        # record that omits it on empty retrievals is a record with a hole in
        # exactly the case somebody is investigating.
        return RerankOutcome(chunks=[], reranker=backend)

    if backend == RERANKER_LEXICAL:
        return RerankOutcome(
            chunks=lexical_order(query, candidates, lexical_scorer, top_k=top_k),
            reranker=RERANKER_LEXICAL,
        )

    try:
        order = await _voyage_order(
            query, [chunk.content for chunk in candidates], top_k=top_k
        )
    except RerankUnavailable as exc:
        logger.warning(
            "rag.reranker.degraded backend=%s reason=%s detail=%s",
            backend,
            exc.reason,
            exc,
        )
        return RerankOutcome(
            chunks=lexical_order(query, candidates, lexical_scorer, top_k=top_k),
            reranker=RERANKER_LEXICAL,
            degraded=True,
            reason=exc.reason,
        )

    return RerankOutcome(
        chunks=[candidates[index] for index in order][:top_k],
        reranker=RERANKER_VOYAGE,
    )
