"""Traffic must SPREAD across the credentials of a quota domain.

THE REGRESSION THIS PINS, measured on live calls 2026-08-24.

The router has always round-robined within a provider tier, for a stated
reason: so concurrent assessments spread instead of hammering key #1 into a
429. Capacity-aware scoring was added on top and silently deleted that
property. Nine consecutive calls all went to `env:groq:1` while its two
siblings sat completely idle.

The mechanism was rich-get-richer, twice over. Scoring read `success_rate` and
`latency` PER CREDENTIAL, so the first key ever called became the only one with
a measurement. A measured-and-good route beats a route sitting on the neutral
prior, so that key won the next call, and the next, and its siblings were never
measured. Self-fulfilling, and invisible: every call succeeded, the scores were
deterministic, and nothing failed.

Both terms are now read at DOMAIN granularity. Siblings on one quota domain
answer the same account with the same model over the same endpoint, so a
difference between them is noise; pooling makes them tie exactly, and `rank`
sorts stably so the existing round-robin decides the tie.

A SHARED TOKEN POOL DOES NOT MAKE PER-KEY RATE LIMITS SHARED. That is the whole
reason spreading still matters once the pool is known to be shared, and it is
why this is asserted rather than left to the scheduler's good intentions.
"""
from __future__ import annotations

import pytest

from app.services import llm_capacity


def _seed(fingerprints: list[str], provider: str = "groq") -> None:
    for fingerprint in fingerprints:
        llm_capacity.route(provider, fingerprint, "test-model")


@pytest.fixture(autouse=True)
def _clean():
    llm_capacity.reset()
    yield
    llm_capacity.reset()


def _order(fingerprints: list[str], provider: str = "groq") -> list[str]:
    scores = llm_capacity.rank_routes(
        [(provider, f) for f in fingerprints],
        task_type="rerank",
        input_tokens=20,
        needs_json=False,
        model_by_provider={provider: "test-model"},
    )
    return [s.fingerprint for s in scores]


def test_one_measured_sibling_does_not_win_forever() -> None:
    """THE regression. A credential with history must not outrank an unmeasured
    sibling on the same pool, or it monopolises the pool it shares."""
    keys = ["k1", "k2", "k3"]
    _seed(keys)
    llm_capacity.observe_success(
        "groq", "k1", latency_ms=400.0, task_type="rerank", model="test-model",
    )
    # The caller's order is the round-robin's, and a tie must preserve it.
    assert _order(["k2", "k3", "k1"])[0] == "k2", (
        "k1 was pulled to the front on its own history, which is the "
        "rich-get-richer loop that deleted the round-robin"
    )


def test_scores_tie_across_siblings_of_one_domain() -> None:
    keys = ["k1", "k2", "k3"]
    _seed(keys)
    for _ in range(3):
        llm_capacity.observe_success(
            "groq", "k1", latency_ms=400.0, task_type="rerank", model="test-model",
        )
    scores = llm_capacity.rank_routes(
        [("groq", f) for f in keys],
        task_type="rerank",
        input_tokens=20,
        needs_json=False,
        model_by_provider={"groq": "test-model"},
    )
    assert len({s.score for s in scores}) == 1, [
        (s.fingerprint, s.score, s.terms) for s in scores
    ]


def test_a_genuinely_worse_domain_still_loses() -> None:
    """Pooling must not flatten the comparison that carries real information.
    Health still differentiates ACROSS domains."""
    _seed(["g1"], provider="groq")
    _seed(["m1"], provider="gemini")
    for _ in range(6):
        llm_capacity.observe_success(
            "groq", "g1", latency_ms=300.0, task_type="rerank", model="test-model",
        )
    for _ in range(6):
        llm_capacity.observe_failure(
            "gemini", "m1", RuntimeError("boom"), latency_ms=9000.0,
            task_type="rerank", requested_tokens=20, model="test-model",
        )
    order = llm_capacity.rank_routes(
        [("gemini", "m1"), ("groq", "g1")],
        task_type="rerank",
        input_tokens=20,
        needs_json=False,
        model_by_provider={"groq": "test-model", "gemini": "test-model"},
    )
    assert order[0].fingerprint == "g1", [(s.fingerprint, s.score) for s in order]


def test_ties_preserve_the_callers_order_exactly() -> None:
    """The round-robin lives in the caller. A tie that reordered would take the
    spreading away again by a different route."""
    _seed(["k1", "k2", "k3"])
    for rotation in (["k1", "k2", "k3"], ["k2", "k3", "k1"], ["k3", "k1", "k2"]):
        assert _order(rotation) == rotation


def test_scoring_is_deterministic() -> None:
    """A sampling scheduler would make a latency regression indistinguishable
    from the scheduler having a different opinion this morning."""
    _seed(["k1", "k2", "k3"])
    llm_capacity.observe_success(
        "groq", "k2", latency_ms=500.0, task_type="rerank", model="test-model",
    )
    assert _order(["k1", "k2", "k3"]) == _order(["k1", "k2", "k3"])
