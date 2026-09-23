"""The reranker actually reranks, and it never pretends it did (W6.1).

Two properties, and the second is the one that matters more.

FIRST: a cross-encoder must be able to BEAT the lexical pass. A test where the
placeholder and the real reranker agree proves nothing about either. The
fixture below is one where the lexical pass is KNOWN WRONG -- a skills list
containing every query word beats the experience paragraph that is the actual
evidence -- so a reranker that changed nothing would fail here.

SECOND: when the cross-encoder cannot answer, the run says so. `retrieval`'s
own docstring already names the failure being prevented: a service that does
not exist "behind an interface that silently returns the input order". A
degradation that is not recorded is exactly that, one layer up.

There is NO live Voyage credential in this environment, so the transport is
exercised by a fake standing in for `voyageai.AsyncClient`. What that proves is
the shape this module reads (`results[].index`) and every branch around it. It
does NOT prove that `rerank-2.5` answers, or that a real response has that
shape. See the report accompanying this change.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest
import voyageai.error

from app.services.rag import chunking, reranker, retrieval

# ── The fixture where lexical affinity is known wrong ─────────────────────────
#
# The query names four things. The SKILLS chunk contains all four words and no
# evidence: every skills list matches every skills query, which is why
# `retrieval._SECTION_PRIOR` discounts it in the first place. The EXPERIENCE
# chunk is the real evidence and shares only one of the four query terms,
# because a person describing what they did does not repeat the JD's vocabulary.
#
# The discount is not enough here, and that is the point: 4/4 coverage times the
# 0.7 skills prior beats 1/4 coverage times the 1.0 experience prior. The
# placeholder gets this wrong by construction, and no tuning of the prior fixes
# it without breaking the cases the prior exists for.

_QUERY = "kafka partition rebalance migration"

_SKILLS = (
    "Kafka, partition, rebalance, migration, Terraform, Airflow, Postgres, "
    "Redis, Kubernetes, Python, Go"
)
_EXPERIENCE = (
    "Owned the streaming ingestion platform across three regions. Took the "
    "cluster split from a single broker set to three, moved every consumer "
    "group over without dropping a message, and cut nightly replay from hours "
    "to minutes by repartitioning on tenant."
)


def _chunk(content: str, section: str, score: float) -> retrieval.RetrievedChunk:
    return retrieval.RetrievedChunk(
        chunk_id=uuid.uuid4(),
        content=content,
        source_type=chunking.SOURCE_RESUME,
        source_id=uuid.uuid4(),
        section_type=section,
        ordinal=0,
        score=score,
        retrievers=("semantic", "keyword"),
    )


@pytest.fixture
def candidates() -> list[retrieval.RetrievedChunk]:
    # The skills chunk also carries the HIGHER fused score, so nothing except a
    # content-aware second pass can move it: fusion order and lexical order
    # agree with each other and are both wrong.
    return [
        _chunk(_SKILLS, chunking.SECTION_SKILLS, score=0.9),
        _chunk(_EXPERIENCE, chunking.SECTION_EXPERIENCE, score=0.2),
    ]


class _Settings:
    """Only the two attributes this module reads."""

    def __init__(self, backend: str = reranker.RERANKER_VOYAGE, key: str = "vk-test"):
        self.retrieval_reranker = backend
        self.voyage_rerank_2_5 = key


def _settings(monkeypatch: pytest.MonkeyPatch, **kwargs) -> None:
    monkeypatch.setattr(reranker, "get_settings", lambda: _Settings(**kwargs))


# ── A fake cross-encoder, shaped like the SDK's real return value ────────────


@dataclass(frozen=True)
class _FakeResult:
    index: int
    document: str
    relevance_score: float


class _FakeReranking:
    def __init__(self, results: list[_FakeResult]) -> None:
        self.results = results
        self.total_tokens = 0


class _FakeClient:
    """Stands in for `voyageai.AsyncClient`. Records what it was asked."""

    def __init__(self, order: list[int] | None = None, raises: Exception | None = None):
        self._order = order
        self._raises = raises
        self.calls: list[dict] = []

    async def rerank(self, *, query, documents, model, top_k, truncation):
        self.calls.append(
            {
                "query": query,
                "documents": list(documents),
                "model": model,
                "top_k": top_k,
                "truncation": truncation,
            }
        )
        if self._raises is not None:
            raise self._raises
        order = self._order if self._order is not None else list(range(len(documents)))
        # `document` is deliberately not looked up from `documents`: the
        # malformed cases below simulate a vendor returning an index outside
        # the batch, and a fake that raised on that would test itself rather
        # than the module's own guard.
        return _FakeReranking(
            [
                _FakeResult(index=index, document="", relevance_score=1.0)
                for index in order
            ]
        )


def _install_client(monkeypatch: pytest.MonkeyPatch, client: _FakeClient) -> None:
    monkeypatch.setattr(reranker, "_client", lambda api_key: client)


# ── The lexical pass is wrong here, demonstrably ─────────────────────────────


def test_the_lexical_pass_gets_this_fixture_wrong(candidates) -> None:
    """Stated as its own test rather than assumed by the next one.

    If the placeholder ever started ranking this correctly, the reranking test
    below would pass for the wrong reason and stop measuring anything.
    """
    ordered = sorted(
        candidates,
        key=lambda chunk: (retrieval.lexical_affinity(_QUERY, chunk), chunk.score),
        reverse=True,
    )
    assert ordered[0].section_type == chunking.SECTION_SKILLS


@pytest.mark.asyncio
async def test_reranking_changes_the_order_the_lexical_pass_produced(
    monkeypatch, candidates
) -> None:
    _settings(monkeypatch)
    _install_client(monkeypatch, _FakeClient(order=[1, 0]))

    outcome = await reranker.rerank_chunks(
        _QUERY,
        candidates,
        top_k=2,
        lexical_scorer=retrieval.lexical_affinity,
    )

    assert outcome.reranker == reranker.RERANKER_VOYAGE
    assert outcome.degraded is False
    assert outcome.reason is None
    assert outcome.chunks[0].section_type == chunking.SECTION_EXPERIENCE
    assert outcome.chunks[1].section_type == chunking.SECTION_SKILLS


@pytest.mark.asyncio
async def test_the_model_is_the_stable_one_and_top_k_reaches_the_vendor(
    monkeypatch, candidates
) -> None:
    """`rerank-3` is Preview. A preview model under a grade-adjacent pipeline
    is the `voyage-context-4` mistake repeated, so the id is pinned here as
    well as in the module."""
    _settings(monkeypatch)
    client = _FakeClient(order=[1, 0])
    _install_client(monkeypatch, client)

    await reranker.rerank_chunks(
        _QUERY, candidates, top_k=1, lexical_scorer=retrieval.lexical_affinity
    )

    assert reranker.RERANK_MODEL == "rerank-2.5"
    assert client.calls[0]["model"] == "rerank-2.5"
    assert client.calls[0]["top_k"] == 1
    assert client.calls[0]["truncation"] is True
    # The VERBATIM chunk text is what gets scored. Nothing derived, nothing
    # summarised, and no identifier.
    assert client.calls[0]["documents"] == [chunk.content for chunk in candidates]


@pytest.mark.asyncio
async def test_top_k_bounds_the_returned_list(monkeypatch, candidates) -> None:
    """A vendor honouring top_k and a vendor ignoring it must produce the same
    number of chunks here, or the caller's budget depends on the vendor."""
    _settings(monkeypatch)
    _install_client(monkeypatch, _FakeClient(order=[1, 0]))

    outcome = await reranker.rerank_chunks(
        _QUERY, candidates, top_k=1, lexical_scorer=retrieval.lexical_affinity
    )
    assert len(outcome.chunks) == 1


# ── Unavailability is RECORDED, never a silent substitution ──────────────────


@pytest.mark.asyncio
async def test_a_missing_credential_records_a_degradation(
    monkeypatch, candidates
) -> None:
    _settings(monkeypatch, key="")

    outcome = await reranker.rerank_chunks(
        _QUERY, candidates, top_k=2, lexical_scorer=retrieval.lexical_affinity
    )

    assert outcome.degraded is True
    assert outcome.reranker == reranker.RERANKER_LEXICAL
    assert outcome.reason == reranker.REASON_NO_CREDENTIAL
    # And the order it fell back to is the lexical one, not an arbitrary one.
    assert outcome.chunks[0].section_type == chunking.SECTION_SKILLS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error, reason",
    [
        (voyageai.error.AuthenticationError("nope"), reranker.REASON_VENDOR_ERROR),
        (voyageai.error.RateLimitError("slow down"), reranker.REASON_VENDOR_ERROR),
        (voyageai.error.ServiceUnavailableError("503"), reranker.REASON_VENDOR_ERROR),
        (voyageai.error.APIConnectionError("dns"), reranker.REASON_VENDOR_ERROR),
        (TimeoutError("hung"), reranker.REASON_TIMEOUT),
    ],
    ids=["auth", "rate_limit", "unavailable", "connection", "timeout"],
)
async def test_every_vendor_failure_records_a_degradation(
    monkeypatch, candidates, error, reason
) -> None:
    """Each branch separately, because a single `except Exception` would pass a
    one-case test and lose the reason the operator needs."""
    _settings(monkeypatch)
    _install_client(monkeypatch, _FakeClient(raises=error))

    outcome = await reranker.rerank_chunks(
        _QUERY, candidates, top_k=2, lexical_scorer=retrieval.lexical_affinity
    )

    assert outcome.degraded is True
    assert outcome.reranker == reranker.RERANKER_LEXICAL
    assert outcome.reason == reason
    assert len(outcome.chunks) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "order", [[0, 5], [-1, 0], [1, 1]], ids=["out_of_range", "negative", "duplicate"]
)
async def test_a_malformed_index_set_degrades_rather_than_misattributing(
    monkeypatch, candidates, order
) -> None:
    """An index outside the batch attaches one candidate's ranking to another
    candidate's chunk, and a duplicate hands the same chunk back twice, which
    an agent counting corroboration would count as two sources. Both are data
    faults no happy-path test would show."""
    _settings(monkeypatch)
    _install_client(monkeypatch, _FakeClient(order=order))

    outcome = await reranker.rerank_chunks(
        _QUERY, candidates, top_k=2, lexical_scorer=retrieval.lexical_affinity
    )

    assert outcome.degraded is True
    assert outcome.reason == reranker.REASON_MALFORMED


@pytest.mark.asyncio
async def test_the_run_record_carries_both_fields_the_spec_names(
    monkeypatch, candidates
) -> None:
    """W6.1: the run records `reranker: "lexical", degraded: true`."""
    _settings(monkeypatch, key="")

    outcome = await reranker.rerank_chunks(
        _QUERY, candidates, top_k=2, lexical_scorer=retrieval.lexical_affinity
    )

    record = outcome.as_dict()
    assert record["reranker"] == "lexical"
    assert record["degraded"] is True
    assert record["reason"] == reranker.REASON_NO_CREDENTIAL


# ── A chosen backend is not a degradation ────────────────────────────────────


@pytest.mark.asyncio
async def test_a_lexical_deployment_is_not_reported_as_degraded(
    monkeypatch, candidates
) -> None:
    """The direction that is easy to get backwards. A deployment that chose the
    lexical pass has not failed at anything, and reporting it as degraded would
    make the signal fire constantly and stop being read."""
    _settings(monkeypatch, backend=reranker.RERANKER_LEXICAL)

    outcome = await reranker.rerank_chunks(
        _QUERY, candidates, top_k=2, lexical_scorer=retrieval.lexical_affinity
    )

    assert outcome.reranker == reranker.RERANKER_LEXICAL
    assert outcome.degraded is False
    assert outcome.reason is None


@pytest.mark.asyncio
async def test_a_lexical_deployment_never_reaches_the_vendor(
    monkeypatch, candidates
) -> None:
    """One value per deployment, never a fallback chain. `lexical` must not
    call Voyage and then fall back, which would be both backends running."""
    _settings(monkeypatch, backend=reranker.RERANKER_LEXICAL)
    client = _FakeClient(order=[1, 0])
    _install_client(monkeypatch, client)

    await reranker.rerank_chunks(
        _QUERY, candidates, top_k=2, lexical_scorer=retrieval.lexical_affinity
    )

    assert client.calls == []


# ── The flag is deployment data, and a wrong value is loud ───────────────────


@pytest.mark.parametrize(
    "value", ["", "voyage-ai", "cohere", "true", None], ids=lambda v: repr(v)
)
def test_an_unrecognised_backend_raises_rather_than_defaulting(
    monkeypatch, value
) -> None:
    """Absent and misspelled produce the SAME loud error naming the variable.
    Defaulting to the lexical pass would let a deployment that believes it runs
    a cross-encoder run the placeholder forever."""
    monkeypatch.setattr(
        reranker, "get_settings", lambda: _Settings(backend=value)  # type: ignore[arg-type]
    )
    with pytest.raises(ValueError) as excinfo:
        reranker.configured_backend()
    assert "RETRIEVAL_RERANKER" in str(excinfo.value)


def test_the_backend_set_is_closed() -> None:
    assert reranker.BACKENDS == frozenset({"voyage", "lexical"})


@pytest.mark.asyncio
async def test_an_empty_retrieval_still_reports_which_backend_is_configured(
    monkeypatch,
) -> None:
    """A run record that omits the backend on empty retrievals has a hole in
    exactly the case somebody is investigating."""
    _settings(monkeypatch)

    outcome = await reranker.rerank_chunks(
        _QUERY, [], top_k=5, lexical_scorer=retrieval.lexical_affinity
    )

    assert outcome.chunks == []
    assert outcome.reranker == reranker.RERANKER_VOYAGE
    assert outcome.degraded is False


# ── The transport's own settings ─────────────────────────────────────────────


def test_the_client_carries_an_explicit_timeout_and_no_retries_of_its_own(
    monkeypatch,
) -> None:
    """Two retry mechanisms stacked MULTIPLY, which is the lesson
    `workers/runtime.run_task` records. An unreachable endpoint that HANGS
    defeats every try/except around it, so the timeout is stated too."""
    captured: dict = {}

    class _Recorder:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("voyageai.AsyncClient", _Recorder)
    reranker._client("vk-test")

    assert captured["max_retries"] == 0
    assert captured["timeout"] == reranker.REQUEST_TIMEOUT_SECONDS
    assert captured["api_key"] == "vk-test"


def test_a_vendor_error_message_never_carries_the_response_body() -> None:
    """A rerank request carries a real candidate's resume text, and this string
    reaches a log sink far more widely readable than the database."""
    error = reranker.RerankUnavailable(
        reranker.REASON_VENDOR_ERROR, "AuthenticationError"
    )
    assert str(error) == "vendor_error: AuthenticationError"
    assert error.reason == reranker.REASON_VENDOR_ERROR
