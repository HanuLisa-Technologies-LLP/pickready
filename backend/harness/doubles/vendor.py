"""Fixture backed transports for the two vendors this product calls.

THE FIXTURES ARE THE ONES THAT ALREADY EXIST, AND NO NEW SHAPE IS AUTHORED HERE
--------------------------------------------------------------------------------
`tests/fixtures/vendor/` holds eight hand authored OpenAI envelopes and four
Voyage ones, each carrying its provenance, its status, the headers a classifier
reads, and its body. `tests/fixtures/vendor/PROVENANCE.md` states the rule they
live under: every one is written from a published schema, `observed` is false in
all of them, and a recording would have to say so in the same edit.

This module SERVES those files and authors nothing. Where a fault kind has no
fixture, it raises `FixtureMissing` naming the file somebody would have to write,
rather than inventing a payload. A harness that quietly makes up a vendor
response is asserting a contract nobody agreed to, which is the exact failure the
fixture directory was built to prevent.

TWO KINDS ARE DERIVED FROM A SUCCESS FIXTURE, AND SAY SO
----------------------------------------------------------
`malformed` and `partial` have no fixture of their own and are each ONE named
mutation of the authored success body:

  malformed  the `object` field becomes the streaming chunk value. That is the
             disagreement `vendor_contract.check_openai_response` names in its
             own error text, because a chunk carries a `delta` where the parser
             reads a `message` and yields an empty string rather than raising.
  partial    `finish_reason` becomes "length" and the content is cut. This is
             not a refusal (`REFUSAL_FINISH_REASONS` is refusal and
             content_filter), so the router ACCEPTS it, which is the finding
             worth being able to inject: truncation arrives as a short answer.

Deriving rather than authoring keeps one source of truth for the envelope. If
the success fixture changes shape, these two change with it instead of becoming
a second, stale statement of what OpenAI sends.

WHY A TRANSPORT AND NOT A PATCHED FUNCTION
--------------------------------------------
`tests/test_router_recovery.py` and `tests/test_vendor_contracts.py` already
drive the real router over `httpx.MockTransport`, so the request is really
serialized, `raise_for_status` really raises and `resp.json()` really parses.
Following that seam rather than patching `_call_openai` is what makes an
injected 429 a thing the REAL classifier classifies, instead of a thing a mock
was told to return.
"""
from __future__ import annotations

import copy
import json
import pathlib
from dataclasses import dataclass
from typing import Any, Callable, Mapping

import httpx

from app.config.llm_providers import (
    MODEL_LUNA,
    OPENAI_CHAT_COMPLETIONS_URL,
    VOYAGE_EMBEDDINGS_URL,
)

FIXTURE_DIR = (
    pathlib.Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "vendor"
)

#: The hosts each seam answers on, derived from the product's own URL constants
#: rather than written out, so a vendor endpoint move cannot leave the fault
#: layer routing on a hostname nothing calls any more.
OPENAI_HOST = httpx.URL(OPENAI_CHAT_COMPLETIONS_URL).host
VOYAGE_HOST = httpx.URL(VOYAGE_EMBEDDINGS_URL).host


class FixtureMissing(LookupError):
    """A fault kind was asked for that no authored fixture covers.

    Raised rather than filled in. The message names the file that would have to
    be written and the provenance rule it would have to satisfy, so the gap is
    closed by authoring a contract rather than by a harness inventing one.
    """


class VendorFixtureError(RuntimeError):
    """A fixture on disk does not satisfy the envelope contract."""


@dataclass(frozen=True)
class VendorFixture:
    """One envelope: where it came from, and what it is a response to."""

    relative: str
    status: int
    headers: Mapping[str, str]
    body: Mapping[str, Any]

    def response(self, request: httpx.Request) -> httpx.Response:
        """Build the httpx response this envelope describes.

        The headers travel, not just the body. `retry_after_seconds` reads
        `retry-after` off the 429, and a fixture served without its headers
        would make the router guess a backoff while the test still looked like
        it was exercising the vendor's own number.
        """
        return httpx.Response(
            self.status,
            headers=dict(self.headers),
            json=copy.deepcopy(dict(self.body)),
            request=request,
        )

    def with_body(self, body: Mapping[str, Any]) -> "VendorFixture":
        """A derived envelope. See the module docstring for the two uses."""
        return VendorFixture(
            relative=self.relative,
            status=self.status,
            headers=self.headers,
            body=body,
        )


def load_fixture(relative: str) -> VendorFixture:
    """Read one envelope, asserting its provenance on the way through.

    `tests/test_vendor_contracts.load` reads the same files for a different
    purpose: it wants the BODY to check a declared shape against, and this wants
    the whole envelope to build a response from. They are deliberately not
    merged, because merging means the harness importing a pytest module, which
    would run collection to get at a helper. If they are ever unified, this is
    the one to keep and `tests/test_vendor_contracts.py` is the edit.
    """
    path = FIXTURE_DIR / relative
    if not path.is_file():
        raise FixtureMissing(
            f"no vendor fixture at {relative}. Author it under "
            f"tests/fixtures/vendor/ from the vendor's published schema, with "
            f"_provenance.observed false, rather than returning a shape from "
            f"here."
        )
    envelope = json.loads(path.read_text(encoding="utf-8"))
    provenance = envelope.get("_provenance", {})
    if provenance.get("observed") is not False:
        raise VendorFixtureError(
            f"{relative} does not declare _provenance.observed as false. Every "
            f"fixture in this directory is hand authored from a published "
            f"schema; if one was recorded from live traffic, the honesty rules "
            f"in claude.md and PROVENANCE.md need updating in the same change."
        )
    body = envelope.get("body")
    if not isinstance(body, dict):
        raise VendorFixtureError(f"{relative} carries no object body")
    return VendorFixture(
        relative=relative,
        status=int(envelope["status"]),
        headers={str(k): str(v) for k, v in dict(envelope.get("headers", {})).items()},
        body=body,
    )


# -- OpenAI ------------------------------------------------------------------

#: The fault kinds that map straight onto an authored error fixture.
OPENAI_ERROR_FIXTURES: Mapping[str, str] = {
    "rate_limited": "openai/error_429_rate_limit.json",
    "server_error": "openai/error_500_server_error.json",
    "unavailable": "openai/error_503_overloaded.json",
    "credential": "openai/error_401_authentication.json",
}

#: The success envelopes, one per model. Which one is served is decided from the
#: model named in the REQUEST rather than fixed, because the JSON mode contract
#: check refuses text that does not open with a brace: serving the prose fixture
#: to a JSON mode call would raise a contract violation that is an artefact of
#: the harness rather than the fault being injected.
OPENAI_SUCCESS_FIXTURES: Mapping[str, str] = {
    "luna": "openai/chat_completion_luna_json.json",
    "terra": "openai/chat_completion_terra_reasoning.json",
}

#: The streaming chunk object value. Not a new shape: it is the exact value
#: `vendor_contract.check_openai_response` names in its own refusal.
_CHUNK_OBJECT = "chat.completion.chunk"

#: `finish_reason` for a response the model ran out of room to finish. Outside
#: `REFUSAL_FINISH_REASONS` on purpose, which is what makes it a partial rather
#: than a decline.
_LENGTH_FINISH_REASON = "length"

#: How much of the authored content survives a `partial`. Enough that the
#: truncation is visible as a cut sentence rather than as an empty answer, which
#: would be indistinguishable from a model that had nothing to say.
_PARTIAL_CHARS = 40


def openai_success_for(request: httpx.Request) -> VendorFixture:
    """The success envelope matching the model this request names."""
    model = _requested_model(request)
    key = "luna" if model == MODEL_LUNA else "terra"
    return load_fixture(OPENAI_SUCCESS_FIXTURES[key])


def _requested_model(request: httpx.Request) -> str:
    try:
        payload = json.loads(request.content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return ""
    return str(payload.get("model", "")) if isinstance(payload, dict) else ""


def openai_responder(kind: str) -> Callable[[httpx.Request], httpx.Response]:
    """A handler for `httpx.MockTransport` that injects `kind` on every call.

    Every call, not the first: a router with a retry budget that met the fault
    once and success afterwards would report that it recovered, which is a
    different claim from the one a fault scenario is making.
    """
    if kind == "timeout":
        def timed_out(request: httpx.Request) -> httpx.Response:
            # No fixture, and none is possible: a timeout is the ABSENCE of a
            # response. `classify_failure` separates it from a transport error
            # because the request may have been received and served, and we
            # simply did not hear the answer.
            raise httpx.ReadTimeout("harness fault: model timeout", request=request)

        return timed_out

    if kind in OPENAI_ERROR_FIXTURES:
        fixture = load_fixture(OPENAI_ERROR_FIXTURES[kind])

        def errored(request: httpx.Request) -> httpx.Response:
            return fixture.response(request)

        return errored

    if kind == "malformed":
        def malformed(request: httpx.Request) -> httpx.Response:
            fixture = openai_success_for(request)
            body = copy.deepcopy(dict(fixture.body))
            body["object"] = _CHUNK_OBJECT
            return fixture.with_body(body).response(request)

        return malformed

    if kind == "partial":
        def partial(request: httpx.Request) -> httpx.Response:
            fixture = openai_success_for(request)
            body = copy.deepcopy(dict(fixture.body))
            choice = body["choices"][0]
            choice["finish_reason"] = _LENGTH_FINISH_REASON
            content = str(choice["message"]["content"])
            choice["message"]["content"] = content[:_PARTIAL_CHARS]
            return fixture.with_body(body).response(request)

        return partial

    raise FixtureMissing(
        f"no OpenAI fault of kind {kind!r}. The kinds this seam serves are "
        f"{sorted(OPENAI_FAULT_KINDS)}."
    )


#: Every model fault kind, as data, so the registry and a test can agree without
#: either restating the list.
OPENAI_FAULT_KINDS: frozenset[str] = frozenset(
    set(OPENAI_ERROR_FIXTURES) | {"timeout", "malformed", "partial"}
)


# -- Voyage ------------------------------------------------------------------

VOYAGE_ERROR_FIXTURES: Mapping[str, str] = {
    "rate_limited": "voyage/error_429_rate_limit.json",
    "credential": "voyage/error_401_authentication.json",
}

VOYAGE_SUCCESS_FIXTURE = "voyage/embeddings_response_document.json"

#: The kinds this seam refuses, with the file that would have to be authored.
#:
#: PROVENANCE.md records a deliberate decision here: Voyage error BODIES are not
#: reproduced, because `embeddings._embed_batch` classifies on the HTTP status
#: and never parses or logs the body, since an embedding request carries a real
#: candidate's resume text. So there is no 500 or 503 envelope to serve, and
#: making one up would be authoring a vendor contract from the harness.
VOYAGE_UNFIXTURED: Mapping[str, str] = {
    "server_error": "voyage/error_500_server_error.json",
    "unavailable": "voyage/error_503_overloaded.json",
}


def voyage_responder(kind: str) -> Callable[[httpx.Request], httpx.Response]:
    """A handler for `httpx.MockTransport` on the embeddings endpoint."""
    if kind == "timeout":
        def timed_out(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout(
                "harness fault: embedding timeout", request=request
            )

        return timed_out

    if kind in VOYAGE_ERROR_FIXTURES:
        fixture = load_fixture(VOYAGE_ERROR_FIXTURES[kind])

        def errored(request: httpx.Request) -> httpx.Response:
            return fixture.response(request)

        return errored

    if kind == "malformed":
        fixture = load_fixture(VOYAGE_SUCCESS_FIXTURE)

        def malformed(request: httpx.Request) -> httpx.Response:
            # The `data` list removed. `_embed_batch` raises EmbeddingError
            # naming a malformed payload, which is the honest failure; the
            # alternative it guards against is a vector of the right width that
            # means nothing.
            body = {k: v for k, v in fixture.body.items() if k != "data"}
            return fixture.with_body(body).response(request)

        return malformed

    if kind in VOYAGE_UNFIXTURED:
        raise FixtureMissing(
            f"the embeddings seam has no {kind!r} fixture. PROVENANCE.md records "
            f"why: nothing in this codebase parses a Voyage error body, so none "
            f"was authored. To inject it, write {VOYAGE_UNFIXTURED[kind]} from "
            f"Voyage's published behaviour, with _provenance.observed false."
        )

    raise FixtureMissing(
        f"no embedding fault of kind {kind!r}. The kinds this seam serves are "
        f"{sorted(VOYAGE_FAULT_KINDS)}."
    )


VOYAGE_FAULT_KINDS: frozenset[str] = frozenset(
    set(VOYAGE_ERROR_FIXTURES) | {"timeout", "malformed"}
)
