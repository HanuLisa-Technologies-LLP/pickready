"""Contract tests for the Anthropic and Voyage clients (spec-doc6 §12.2).

WHAT THESE TESTS PROVE, AND WHAT THEY DO NOT
---------------------------------------------
**This proves the code's logic without proving the vendor's behaviour.** Every
fixture under `tests/fixtures/vendor/` was hand-authored from the vendors'
published API schemas and has never been checked against a live call; there is
no Anthropic key and no Voyage key in this phase, so nothing here is a
recording of traffic. What is asserted below is that this codebase parses,
classifies, backs off, times out and trips its breaker correctly GIVEN those
shapes. If the published documentation is wrong, or a schema changes, every
test in this file still passes and the product is still broken.

The mechanism that covers that gap is deliberately not a test. It is
`app/services/reliability/vendor_contract.py`, which checks the FIRST live
response on each path against the same contract these fixtures satisfy and
raises `VendorContractViolation` naming the fixture it disagreed with
(spec-doc6 §12.5). `scripts/verify_live.py` is the command that would exercise
it, and it has never been run.

The honest framing for all of it: built and tested against recorded fixtures
and a stub provider; not executed against a live provider.

WHY THE CONTRACT IS DECLARED IN CODE AND THE PAYLOADS SIT IN FIXTURES
----------------------------------------------------------------------
So the two cannot drift. `vendor_contract` declares the required keys and names
the fixture each was authored against; the first test below asserts every
fixture satisfies the contract that names it. Editing the contract without
editing the fixture fails, and so does the reverse. Same discipline
`test_runbook_parity.py` applies to the Runbook.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import time
from typing import Any

import httpx
import pytest

from app.config import llm_providers
from app.services import embeddings, llm_router
from app.services.llm_router import LLMUnavailableError, _RouterKey
from app.services.reliability import vendor_contract
from app.services.reliability.vendor_contract import VendorContractViolation

FIXTURE_DIR = pathlib.Path(__file__).resolve().parent / "fixtures" / "vendor"


# ── Fixture loading ──────────────────────────────────────────────────────────


def load(relative: str) -> dict[str, Any]:
    """One recorded envelope: provenance, status, headers, body.

    Asserts `observed` is false on the way through. If someone eventually
    records a real response they have to say so in the same edit, which is what
    keeps "recorded" and "hand-authored" distinguishable in a diff rather than a
    matter of memory.
    """
    envelope = json.loads((FIXTURE_DIR / relative).read_text(encoding="utf-8"))
    assert envelope["_provenance"]["observed"] is False, (
        f"{relative} claims to be observed. Nothing in this phase has been "
        f"executed against a live provider; if that changed, the honesty rules "
        f"in CLAUDE.md and VERIFICATION_PENDING.md need updating too."
    )
    return envelope


def body(relative: str) -> dict[str, Any]:
    payload = load(relative)["body"]
    assert isinstance(payload, dict)
    return payload


def http_error(relative: str) -> httpx.HTTPStatusError:
    """The `httpx` error the transport would raise for a recorded failure."""
    envelope = load(relative)
    request = httpx.Request("POST", llm_providers.ANTHROPIC_MESSAGES_URL)
    response = httpx.Response(
        int(envelope["status"]),
        request=request,
        headers={k: v for k, v in envelope["headers"].items() if k != "content-type"},
        json=envelope["body"],
    )
    return httpx.HTTPStatusError("recorded", request=request, response=response)


ALL_FIXTURES = sorted(
    p.relative_to(FIXTURE_DIR).as_posix() for p in FIXTURE_DIR.rglob("*.json")
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> Any:
    llm_router.reset_provider_stats()
    llm_router.clear_provider_breaker()
    vendor_contract.reset_first_use()
    monkeypatch.setattr(
        llm_router, "_load_key", lambda: _RouterKey(api_key="k-test", fingerprint="fp1")
    )
    yield
    llm_router.reset_provider_stats()
    llm_router.clear_provider_breaker()
    vendor_contract.reset_first_use()


# ── The fixtures are real fixtures, and they say what they are ───────────────


def test_there_are_fixtures_for_both_vendors_and_every_failure_branch() -> None:
    """A contract suite with nothing recorded in it passes for the wrong reason."""
    assert len(ALL_FIXTURES) >= 10
    statuses = {load(name)["status"] for name in ALL_FIXTURES}
    # Success, credential, permission, rate limit, provider fault, overload, and
    # the two documented request rejections.
    assert {200, 400, 401, 403, 429, 500, 529} <= statuses


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_every_fixture_declares_its_provenance_and_claims_no_live_call(
    name: str,
) -> None:
    envelope = load(name)
    provenance = envelope["_provenance"]
    assert provenance["authored_from"].strip(), name
    assert provenance["observed"] is False, name
    assert isinstance(envelope["status"], int), name
    assert isinstance(envelope["headers"], dict), name


def test_the_provenance_note_states_what_the_fixtures_do_not_prove() -> None:
    """The distinction is an explicit spec requirement, not a nicety.

    spec-doc6 §12.2: "This proves the code's logic without proving the vendor's
    behaviour, and the distinction must be stated in the test module
    docstring." It is stated in this module's docstring and in the fixture
    directory's own note, and this test keeps both from being quietly softened.
    """
    note = " ".join((FIXTURE_DIR / "PROVENANCE.md").read_text(encoding="utf-8").split())
    assert "never been checked against a live call" in note
    assert "not executed against a live provider" in note
    assert __doc__ is not None
    assert "without proving the vendor's behaviour" in " ".join(__doc__.split())


# ── The declared contract and the recorded payloads agree ────────────────────


def test_the_anthropic_fixture_satisfies_the_contract_that_names_it() -> None:
    contract = vendor_contract.ANTHROPIC_MESSAGES
    vendor_contract.check_anthropic_response(
        body(contract.fixture),
        model=llm_providers.MODEL_SONNET,
        json_mode=False,
        force=True,
    )


def test_the_voyage_fixture_satisfies_the_contract_that_names_it() -> None:
    contract = vendor_contract.VOYAGE_EMBEDDINGS
    # `expected_dimension=None`: the recorded vectors are truncated to eight
    # components so the file can be read, and its provenance block says so. The
    # width rule is exercised on its own below.
    vendor_contract.check_voyage_response(
        body(contract.fixture),
        expected_rows=2,
        expected_dimension=None,
        force=True,
    )


# ── Parsing ──────────────────────────────────────────────────────────────────


def test_the_reasoning_response_parses_into_text_and_usage() -> None:
    result = llm_router.parse_response(
        body("anthropic/messages_response_sonnet_reasoning.json"), json_mode=False
    )
    assert result.content.startswith("The evidence supports")
    assert result.prompt_tokens == 2095
    assert result.completion_tokens == 218
    assert result.had_usage is True


def test_json_mode_prepends_the_prefill_and_the_result_is_one_top_level_object() -> None:
    """JSON mode is a PREFILL, not an instruction.

    The Messages API has no `response_format`, so the router seeds the assistant
    turn with an opening brace and prepends it back. The recorded response is
    therefore the REMAINDER of the object, and the test that matters is that
    what the caller receives parses as a top-level dict: every JSON-mode caller
    in this codebase does `json.loads(...)` and then subscripts it.
    """
    result = llm_router.parse_response(
        body("anthropic/messages_response_haiku_json_prefill.json"), json_mode=True
    )
    assert result.content.startswith("{")
    decoded = json.loads(result.content)
    assert isinstance(decoded, dict)
    assert decoded["skills"] == 72
    assert "comments" in decoded


def test_the_same_body_without_the_prefill_would_not_have_parsed() -> None:
    """The negative direction, which is what makes the test above mean anything.

    Without the prepend the response is a bare fragment. If `parse_response`
    ever stopped prepending, the assertion above would still describe a shape
    nothing produces, so the failure is pinned from both sides.
    """
    raw = llm_router.parse_response(
        body("anthropic/messages_response_haiku_json_prefill.json"), json_mode=False
    )
    with pytest.raises(json.JSONDecodeError):
        json.loads(raw.content)


def test_a_response_that_omits_usage_under_reports_rather_than_crashing() -> None:
    payload = body("anthropic/messages_response_sonnet_reasoning.json")
    del payload["usage"]
    result = llm_router.parse_response(payload, json_mode=False)
    assert result.prompt_tokens == 0
    assert result.had_usage is False
    assert result.content


# ── Failure classification ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("fixture", "kind", "retryable", "credential"),
    [
        ("anthropic/error_401_authentication.json", "credential", False, True),
        ("anthropic/error_403_permission.json", "credential", False, True),
        ("anthropic/error_429_rate_limit.json", "rate_limit", True, False),
        ("anthropic/error_500_api_error.json", "provider_error", True, False),
        ("anthropic/error_529_overloaded.json", "provider_error", True, False),
    ],
)
def test_each_recorded_failure_classifies_the_way_the_router_assumes(
    fixture: str, kind: str, retryable: bool, credential: bool
) -> None:
    exc = http_error(fixture)
    status = llm_router.status_of(exc)
    assert status is not None
    assert llm_providers.classify_status(status) == kind
    assert llm_router.is_retryable(exc) is retryable
    assert llm_router.is_account_level_failure(exc) is credential


def test_a_recorded_400_is_not_retried() -> None:
    """A non-429 4xx is OUR bug and fails identically on retry.

    Spending the budget on it delays the caller's deterministic fallback for
    nothing, which is the whole reason the classification splits this way.
    """
    exc = http_error("anthropic/error_400_prefill_rejected.json")
    assert llm_router.is_retryable(exc) is False
    assert llm_router.is_account_level_failure(exc) is False


def test_the_vendors_retry_after_is_read_rather_than_guessed_at() -> None:
    exc = http_error("anthropic/error_429_rate_limit.json")
    assert llm_router.retry_after_seconds(exc) == 3.0


def test_a_failure_carrying_no_retry_after_falls_back_to_the_local_curve() -> None:
    exc = http_error("anthropic/error_500_api_error.json")
    assert llm_router.retry_after_seconds(exc) is None
    assert llm_providers.backoff_seconds(2) == llm_providers.BACKOFF_BASE_SECONDS
    assert llm_providers.backoff_seconds(99) == llm_providers.BACKOFF_MAX_SECONDS


# ── The circuit breaker ──────────────────────────────────────────────────────


def _stub(monkeypatch: pytest.MonkeyPatch, results: list[Any]) -> list[int]:
    """Replace the HTTP layer with a scripted sequence, counting the calls."""
    calls: list[int] = []
    queue = list(results)

    async def fake_call(*_args: Any, **_kwargs: Any) -> Any:
        calls.append(1)
        outcome = queue.pop(0) if queue else queue
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(llm_router, "_call_anthropic", fake_call)
    return calls


@pytest.mark.asyncio
async def test_a_credential_failure_trips_the_breaker_on_the_first_occurrence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No amount of waiting fixes a revoked key.

    A 429 or a 5xx needs two consecutive failures to open the breaker, because
    both clear on their own. A 401 does not clear on its own, so the caller's
    deterministic fallback should start one attempt sooner rather than three.
    """
    calls = _stub(monkeypatch, [http_error("anthropic/error_401_authentication.json")])
    with pytest.raises(LLMUnavailableError):
        await llm_router.invoke_llm("rerank", [{"role": "user", "content": "hi"}])
    assert len(calls) == 1

    # The breaker is now open, so the NEXT call does not reach the transport at
    # all. That is the observable half: a closed breaker that still spends an
    # attempt is a breaker in name only.
    second = _stub(monkeypatch, [body("anthropic/messages_response_sonnet_reasoning.json")])
    with pytest.raises(LLMUnavailableError):
        await llm_router.invoke_llm("rerank", [{"role": "user", "content": "hi"}])
    assert second == []


@pytest.mark.asyncio
async def test_a_rate_limit_does_not_trip_the_breaker_on_one_occurrence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The contrast that makes the credential rule a rule rather than an accident."""
    _stub(
        monkeypatch,
        [
            http_error("anthropic/error_429_rate_limit.json"),
            llm_router._Result(content="recovered"),
        ],
    )
    result = await llm_router.invoke_llm("rerank", [{"role": "user", "content": "hi"}])
    assert result == "recovered"


@pytest.mark.asyncio
async def test_the_breaker_reopens_after_the_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A breaker with no way back is a permanent outage wearing a feature's name.

    This product has already shipped that bug once: a persisted `healthy=false`
    with no expiry left every credential skipped forever.
    """
    _stub(monkeypatch, [http_error("anthropic/error_401_authentication.json")])
    with pytest.raises(LLMUnavailableError):
        await llm_router.invoke_llm("rerank", [{"role": "user", "content": "hi"}])

    real_monotonic = time.monotonic
    monkeypatch.setattr(
        llm_router.time,
        "monotonic",
        lambda: real_monotonic() + llm_router._COOLDOWN_SECONDS + 1,
    )
    _stub(monkeypatch, [llm_router._Result(content="half-open probe succeeded")])
    assert (
        await llm_router.invoke_llm("rerank", [{"role": "user", "content": "hi"}])
        == "half-open probe succeeded"
    )


# ── Timeout and the predictive deadline ──────────────────────────────────────


@pytest.mark.asyncio
async def test_a_timeout_is_a_transient_and_is_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _stub(
        monkeypatch,
        [
            httpx.ReadTimeout("attempt one ran out of time"),
            llm_router._Result(content="second attempt landed"),
        ],
    )
    assert (
        await llm_router.invoke_llm("rerank", [{"role": "user", "content": "hi"}])
        == "second attempt landed"
    )
    assert len(calls) == 2


def test_the_deadline_predicts_rather_than_merely_observing() -> None:
    """`elapsed >= deadline` starts an attempt that cannot finish.

    With the longest observed attempt at 20s and 15s of budget left, the next
    attempt would end 5 seconds past the deadline with a candidate watching a
    text box. The check is `remaining < longest_attempt`.
    """
    ctx = llm_router._RouteContext(
        task_type="rerank",
        model=llm_providers.MODEL_HAIKU,
        key=_RouterKey(api_key="k", fingerprint="fp"),
        messages=[],
        json_mode=False,
        client=None,  # type: ignore[arg-type]  # never dereferenced here
        retry_budget=3,
        max_tokens=1024,
        temperature=0.0,
        attempt_timeout=20.0,
        deadline=time.monotonic() + 15.0,
    )
    ctx.longest_attempt = 20.0
    assert llm_router._budget_exhausted(ctx) is True

    ctx.longest_attempt = 2.0
    assert llm_router._budget_exhausted(ctx) is False


@pytest.mark.asyncio
async def test_a_retry_after_longer_than_the_remaining_budget_stops_the_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Waiting out a backoff you can no longer use is worse than failing now."""
    request = httpx.Request("POST", llm_providers.ANTHROPIC_MESSAGES_URL)
    response = httpx.Response(429, request=request, headers={"retry-after": "600"})
    exc = httpx.HTTPStatusError("slow down", request=request, response=response)
    calls = _stub(monkeypatch, [exc, llm_router._Result(content="never reached")])

    started = time.monotonic()
    with pytest.raises(LLMUnavailableError):
        await llm_router.invoke_llm(
            "rerank", [{"role": "user", "content": "hi"}], total_budget=20.0
        )
    assert len(calls) == 1
    assert time.monotonic() - started < 10.0


# ── The interactive caps ─────────────────────────────────────────────────────


def test_the_interactive_cap_is_fifteen_and_thirty_seconds() -> None:
    assert llm_providers.timeout_for("rerank") == 15.0
    assert llm_providers.total_budget_for("rerank") == 30.0


def test_jd_generation_gets_the_wider_interactive_tier() -> None:
    """25s/50s, and the reason is arithmetic rather than preference.

    A multi-thousand-token JD cannot finish in 15 seconds on Sonnet. Holding the
    flat cap would not make the button faster; it would make every generation
    time out and fall back to the template, permanently.
    """
    assert llm_providers.timeout_for("jd_generation") == 25.0
    assert llm_providers.total_budget_for("jd_generation") == 50.0


def test_a_background_task_is_not_held_to_the_interactive_cap() -> None:
    assert llm_providers.timeout_for("report_synthesis") > 30.0


# ── First-live-use enforcement (spec-doc6 §12.5) ─────────────────────────────


def test_a_response_shaped_differently_raises_and_names_the_fixture() -> None:
    payload = body("anthropic/messages_response_sonnet_reasoning.json")
    payload["content"] = "a bare string where the contract requires a list"
    with pytest.raises(VendorContractViolation) as excinfo:
        vendor_contract.check_anthropic_response(
            payload, model=llm_providers.MODEL_SONNET, json_mode=False
        )
    assert excinfo.value.fixture == vendor_contract.ANTHROPIC_MESSAGES.fixture
    assert "messages_response_sonnet_reasoning.json" in str(excinfo.value)
    assert "'content' is a str" in str(excinfo.value)


def test_a_response_with_no_text_block_raises_rather_than_parsing_to_empty() -> None:
    """This is the case `parse_response` alone cannot catch.

    It joins the text of every block whose type is `text`, so a body carrying
    only other block types yields "" -- which reads downstream exactly like a
    model that had nothing to say, and a report written from it looks thin
    rather than broken.
    """
    payload = body("anthropic/messages_response_sonnet_reasoning.json")
    payload["content"] = [{"type": "thinking", "thinking": ""}]
    assert llm_router.parse_response(payload, json_mode=False).content == ""
    with pytest.raises(VendorContractViolation) as excinfo:
        vendor_contract.check_anthropic_response(
            payload, model=llm_providers.MODEL_SONNET, json_mode=False
        )
    assert "no content block has type 'text'" in str(excinfo.value)


def test_a_json_mode_response_that_opens_with_a_fence_raises() -> None:
    """If the prefill did not take, prepending a brace produces text, not JSON."""
    payload = body("anthropic/messages_response_haiku_json_prefill.json")
    payload["content"] = [{"type": "text", "text": "```json\n{\"skills\": 1}\n```"}]
    with pytest.raises(VendorContractViolation) as excinfo:
        vendor_contract.check_anthropic_response(
            payload, model=llm_providers.MODEL_HAIKU, json_mode=True
        )
    assert "prefill did not constrain the response" in str(excinfo.value)


def test_the_check_runs_once_per_path_and_not_once_per_call() -> None:
    good = body("anthropic/messages_response_sonnet_reasoning.json")
    vendor_contract.check_anthropic_response(
        good, model=llm_providers.MODEL_SONNET, json_mode=False
    )
    assert vendor_contract.already_checked(
        vendor_contract.anthropic_path(llm_providers.MODEL_SONNET)
    )
    # A later malformed response on the same path is NOT re-checked. The
    # tradeoff is deliberate: an undocumented schema change shows up on the
    # first response as readily as on the thousandth, and a per-call assertion
    # is a per-call chance for a validator bug to break a working integration.
    broken = dict(good)
    broken.pop("content")
    vendor_contract.check_anthropic_response(
        broken, model=llm_providers.MODEL_SONNET, json_mode=False
    )
    # The other model is a different path and is still unchecked.
    assert not vendor_contract.already_checked(
        vendor_contract.anthropic_path(llm_providers.MODEL_HAIKU)
    )


def test_every_model_in_the_roster_has_a_first_use_path() -> None:
    paths = {vendor_contract.anthropic_path(m) for m in llm_providers.ALLOWED_MODELS}
    assert len(paths) == len(llm_providers.ALLOWED_MODELS)
    assert vendor_contract.voyage_path().endswith(llm_providers.EMBEDDING_MODEL)


# ── The Voyage contract ──────────────────────────────────────────────────────


def test_an_out_of_order_batch_is_restored_by_index_not_by_position() -> None:
    """An out-of-order batch attaches every vector to the wrong row.

    Nothing about the result looks wrong: the column is the right width, the
    row count is right, and only the meaning is scrambled. It is checked here
    against a fixture whose rows are deliberately reversed.
    """
    payload = body("voyage/embeddings_response_out_of_order.json")
    rows = sorted(payload["data"], key=lambda row: int(row["index"]))
    assert [int(r["index"]) for r in rows] == [0, 1]
    reference = body("voyage/embeddings_response_document.json")
    assert [r["embedding"] for r in rows] == [
        r["embedding"] for r in reference["data"]
    ]


def test_a_short_batch_raises_rather_than_writing_fewer_vectors_than_rows() -> None:
    payload = body("voyage/embeddings_response_document.json")
    payload["data"] = payload["data"][:1]
    with pytest.raises(VendorContractViolation) as excinfo:
        vendor_contract.check_voyage_response(
            payload, expected_rows=2, expected_dimension=None
        )
    assert "asked for 2 vectors" in str(excinfo.value)


def test_a_wrong_width_vector_raises_before_it_reaches_a_vector_1024_column() -> None:
    payload = body("voyage/embeddings_response_document.json")
    # Expand one row to the real contract width and leave the other truncated,
    # which is the shape a partially-honoured `output_dimension` would produce.
    payload["data"][0]["embedding"] = [0.01] * embeddings.EMBEDDING_DIM
    with pytest.raises(VendorContractViolation) as excinfo:
        vendor_contract.check_voyage_response(
            payload,
            expected_rows=2,
            expected_dimension=embeddings.EMBEDDING_DIM,
            force=True,
        )
    assert f"vector({embeddings.EMBEDDING_DIM})" in str(excinfo.value)


def test_a_full_width_response_passes_the_width_check() -> None:
    """The positive direction, so the test above is not passing for free."""
    payload = body("voyage/embeddings_response_document.json")
    for row in payload["data"]:
        row["embedding"] = [0.01] * embeddings.EMBEDDING_DIM
    vendor_contract.check_voyage_response(
        payload,
        expected_rows=2,
        expected_dimension=embeddings.EMBEDDING_DIM,
        force=True,
    )


def test_a_duplicate_index_raises_rather_than_returning_one_vector_twice() -> None:
    """The hole the parse cannot see, which is why the contract check exists.

    `_embed_batch` sorts on `index`. Two rows both claiming index 0 sort
    perfectly well, yield the right NUMBER of vectors of the right WIDTH, and
    quietly attach one candidate's embedding to two rows. Nothing downstream
    looks wrong.
    """
    payload = body("voyage/embeddings_response_document.json")
    payload["data"][1]["index"] = 0
    rows = sorted(payload["data"], key=lambda row: int(row["index"]))
    assert len(rows) == 2  # the parse is happy
    with pytest.raises(VendorContractViolation) as excinfo:
        vendor_contract.check_voyage_response(
            payload, expected_rows=2, expected_dimension=None
        )
    assert "not 0..n-1" in str(excinfo.value)


def test_a_response_from_a_different_model_raises() -> None:
    """Two models' vectors share a column width and nothing else."""
    payload = body("voyage/embeddings_response_document.json")
    payload["model"] = "BAAI/bge-small-en-v1.5"
    with pytest.raises(VendorContractViolation) as excinfo:
        vendor_contract.check_voyage_response(
            payload, expected_rows=2, expected_dimension=None
        )
    assert "share a column width and nothing else" in str(excinfo.value)


def test_the_embedding_client_raises_on_a_contract_violation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The check is wired into the client, not merely available to it."""
    envelope = load("voyage/embeddings_response_document.json")
    payload = envelope["body"]
    payload["data"][1]["index"] = 0

    class _Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return payload

    class _Client:
        async def post(self, *_a: Any, **_k: Any) -> _Response:
            return _Response()

    with pytest.raises(VendorContractViolation) as excinfo:
        asyncio.run(
            embeddings._embed_batch(
                _Client(),  # type: ignore[arg-type]  # duck-typed transport
                "voyage-key",
                ["one", "two"],
                embeddings.INPUT_TYPE_DOCUMENT,
            )
        )
    assert excinfo.value.vendor == "Voyage"
    assert "not 0..n-1" in str(excinfo.value)


# ── The documented request hazards ───────────────────────────────────────────
#
# These are the two published constraints the request this codebase builds does
# not satisfy on the Sonnet path. They are recorded rather than worked around:
# changing the request shape changes what every Sonnet call sends, which is an
# owner decision. See VERIFICATION_PENDING.md rows 10 and 11.


def test_the_json_mode_payload_ends_on_an_assistant_turn() -> None:
    """The prefill, stated as the property the hazard check keys on."""
    payload = llm_router.build_payload(
        model=llm_providers.MODEL_SONNET,
        messages=[{"role": "user", "content": "grade this"}],
        json_mode=True,
        max_tokens=1024,
        temperature=0.0,
    )
    assert payload["messages"][-1] == {"role": "assistant", "content": "{"}


def test_the_prefill_hazard_is_recorded_for_the_model_that_rejects_it() -> None:
    payload = llm_router.build_payload(
        model=llm_providers.MODEL_SONNET,
        messages=[{"role": "user", "content": "grade this"}],
        json_mode=True,
        max_tokens=1024,
        temperature=llm_providers.DEFAULT_TEMPERATURE,
    )
    hazards = vendor_contract.describe_request_hazards(
        llm_providers.MODEL_SONNET, payload
    )
    assert any("prefill" in h for h in hazards)


def test_the_sampling_hazard_is_recorded_for_a_non_default_temperature() -> None:
    payload = llm_router.build_payload(
        model=llm_providers.MODEL_SONNET,
        messages=[{"role": "user", "content": "grade this"}],
        json_mode=False,
        max_tokens=1024,
        temperature=0.0,
    )
    hazards = vendor_contract.describe_request_hazards(
        llm_providers.MODEL_SONNET, payload
    )
    assert any("temperature" in h for h in hazards)


def test_the_haiku_path_carries_neither_hazard() -> None:
    """Not a formality: it is why the two paths are recorded separately.

    Haiku 4.5 predates both constraints, so the extraction path's request shape
    is unaffected. If the hazard check flagged everything it would be telling
    nobody anything.
    """
    payload = llm_router.build_payload(
        model=llm_providers.MODEL_HAIKU,
        messages=[{"role": "user", "content": "extract"}],
        json_mode=True,
        max_tokens=1024,
        temperature=0.0,
    )
    assert (
        vendor_contract.describe_request_hazards(llm_providers.MODEL_HAIKU, payload)
        == ()
    )


def test_a_request_that_omits_the_hazards_is_reported_clean() -> None:
    """The remedy, stated executably, so it is clear what would settle this."""
    payload = llm_router.build_payload(
        model=llm_providers.MODEL_SONNET,
        messages=[{"role": "user", "content": "grade this"}],
        json_mode=False,
        max_tokens=1024,
        temperature=vendor_contract.DEFAULT_TEMPERATURE,
    )
    assert (
        vendor_contract.describe_request_hazards(llm_providers.MODEL_SONNET, payload)
        == ()
    )


def test_a_hazard_sentence_never_quotes_the_request(
) -> None:
    """A Messages request carries a real candidate's answers."""
    payload = llm_router.build_payload(
        model=llm_providers.MODEL_SONNET,
        messages=[{"role": "user", "content": "SECRET-ANSWER-TEXT"}],
        json_mode=True,
        max_tokens=1024,
        temperature=0.0,
    )
    for hazard in vendor_contract.describe_request_hazards(
        llm_providers.MODEL_SONNET, payload
    ):
        assert "SECRET-ANSWER-TEXT" not in hazard


@pytest.mark.asyncio
async def test_a_four_hundred_on_the_sonnet_path_names_the_hazard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The point of recording the hazards at all.

    Without this, the documented failure arrives as "invalid_request (400)",
    is correctly classified as our bug, is correctly not retried, and every
    caller correctly degrades to its deterministic fallback -- which is exactly
    what an outage looks like, forever, with nothing naming the cause.
    """
    _stub(monkeypatch, [http_error("anthropic/error_400_prefill_rejected.json")])
    with pytest.raises(LLMUnavailableError) as excinfo:
        await llm_router.invoke_llm(
            "report_synthesis",
            [{"role": "user", "content": "write the report"}],
            response_format_json=True,
        )
    message = str(excinfo.value)
    assert "known request hazard" in message
    assert "prefill" in message
    assert "write the report" not in message
