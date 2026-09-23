"""Pseudo-random vectors are refused in production, and that is not cosmetic.

THE FAILURE THIS PREVENTS ALREADY HAPPENED. `voyage-context-4` was enshrined in
`claude.md` as a hard rule, cited in nine modules and pinned by tests, for a
whole phase. It did not exist. Nothing ever failed, because with no credential
`embed` returned deterministic pseudo-random unit vectors of the right width,
with no exception and no log line, and there was never a credential.

A wrong model id and a missing key both produced plausible numbers. Retrieval
over pseudo-random vectors is not DEGRADED retrieval: cosine distances come
back, an ordering exists, every count on the page is populated, and the rows
are unrelated to the query. There is no honest way to serve that.
"""
from __future__ import annotations

import asyncio

import pytest

from app.core.config import get_settings
from app.services import embeddings


def _settings(monkeypatch, *, key: str, production: bool):
    settings = get_settings()
    monkeypatch.setattr(settings, "voyage_context_4", key, raising=False)
    monkeypatch.setattr(
        type(settings), "is_production", property(lambda _self: production)
    )
    return settings


def test_production_without_a_credential_raises_rather_than_inventing(
    monkeypatch,
) -> None:
    _settings(monkeypatch, key="", production=True)
    with pytest.raises(embeddings.EmbeddingUnavailable) as caught:
        asyncio.run(embeddings.embed(["a resume paragraph"]))
    # The message must name the VARIABLE, which is the actionable half, and say
    # what the fallback would have done, which is the half that explains why
    # this is worth raising over.
    assert "VOYAGE_CONTEXT_4" in str(caught.value)
    assert "pseudo-random" in str(caught.value)


def test_development_without_a_credential_still_works_and_warns(
    monkeypatch, caplog
) -> None:
    """The fallback survives OUTSIDE production, deliberately.

    Removing it would make a laptop with no vendor key unable to run the suite
    or the seed, which is a real cost for no safety gain: a developer's
    retrieval results were never going to reach a candidate.

    It warns on EVERY call rather than once. A one-shot warning is read by
    whoever happened to tail the log at start-up and by nobody afterwards, and
    this is the state in which every stored vector is meaningless.
    """
    _settings(monkeypatch, key="", production=False)
    with caplog.at_level("WARNING"):
        first = asyncio.run(embeddings.embed(["a", "b"]))
        second = asyncio.run(embeddings.embed(["a", "b"]))

    assert len(first) == 2
    warnings = [r for r in caplog.records if "dev_fallback" in r.getMessage()]
    assert len(warnings) == 2, "the fallback warned once and then went quiet"

    # Deterministic, so a dev run is reproducible. That is the one property the
    # fallback is allowed to have.
    assert first == second


def test_an_empty_input_is_not_a_missing_credential(monkeypatch) -> None:
    """No texts means no work, in production too.

    Raising here would make an empty batch, a perfectly ordinary state, look
    like a configuration fault on every sweep that had nothing to index.
    """
    _settings(monkeypatch, key="", production=True)
    assert asyncio.run(embeddings.embed([])) == []


def test_the_guard_reads_the_credential_and_not_the_environment_name(
    monkeypatch,
) -> None:
    """A configured production deployment is not refused.

    The complementary direction, and the one that would catch a guard written
    as `if is_production: raise`. With a key present the function proceeds to
    the vendor call; here that call is replaced, so nothing leaves the machine.
    """
    _settings(monkeypatch, key="a-key-shaped-string", production=True)

    async def fake_batch(_client, _key, texts, _input_type):
        return [[0.0] * 1024 for _ in texts]

    monkeypatch.setattr(embeddings, "_embed_batch", fake_batch)
    vectors = asyncio.run(embeddings.embed(["one", "two"]))
    assert len(vectors) == 2
    assert len(vectors[0]) == 1024
