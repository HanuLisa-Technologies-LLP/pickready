"""The response shapes this codebase assumes its two vendors return, and the
first-live-use check that makes a wrong assumption announce itself.

spec-doc6 §12.5. There is no OpenAI key and no Voyage key in this phase, so
every statement this module makes about the vendors is derived from their
PUBLISHED API SCHEMAS and has never been observed against a live endpoint. The
two model ids are additionally the owner's strings and have never been resolved
against a models endpoint, so "does this id exist" is unproven here alongside
"is this the shape it returns".

That is not a caveat to skim: a shape derived from documentation is a guess with
a citation, and the failure mode of a wrong guess here is silent.
`parse_response` in `llm_router` reads `choices[0].message.content` and returns
"" when the choice list is shaped differently or the content is null;
`embeddings._embed_batch` sorts on `data[].index` and would raise only if the
key were absent entirely. Neither notices a response that is merely DIFFERENT
from what was expected, and a report written from an empty string reads exactly
like a report written from a model that had little to say.

WHAT THIS MODULE DOES
---------------------
    declare the shape -> check it ONCE per path on first live use -> raise an
    error that names the fixture the response disagreed with.

Once per path, not once per call. The check is a few dict lookups, but a
per-call assertion is a per-call opportunity for a validator bug to take down a
working integration, and the thing being guarded against -- an undocumented
schema change, or documentation that was wrong to begin with -- shows up on the
first response as readily as on the thousandth.

WHY THE FIXTURES LIVE IN `tests/` AND THE CONTRACT LIVES HERE
--------------------------------------------------------------
The recorded payloads are test data and belong with the tests. Production code
that read them at import time would ship test fixtures in the image and fail at
startup if they were pruned. So the CONTRACT -- the required keys, their types,
and the fixture filename each was authored against -- is declared here in code,
and `tests/test_vendor_contracts.py` asserts that every fixture satisfies the
contract it names. The two therefore cannot drift: editing the contract without
editing the fixture fails the suite, and so does the reverse. Same discipline
`test_runbook_parity.py` applies to the Runbook.

THE KNOWN REQUEST HAZARD IS NOT SPECULATION
--------------------------------------------
`describe_request_hazards` records the one published constraint on the Chat
Completions request this codebase builds that a future edit could plausibly
break: `response_format: {"type": "json_object"}` is rejected with a 400 unless
the token "json" appears somewhere in the messages. `llm_router`'s
`_JSON_SYSTEM_SUFFIX` satisfies it today, which is precisely why the check is
worth having -- the constant reads like ordinary prompt wording and nothing
about it announces that deleting a word from it turns every JSON-mode call in
the product into a 400.

A 400 is classified as OUR bug and is correctly not retried, so without this the
symptom would be every caller degrading to its deterministic fallback with
nothing naming the cause. That is what an outage looks like, forever.

TWO HAZARDS WERE DELETED HERE ON 2026-08-31, and they are worth naming so a
reader does not go looking for them: `PREFILL_REJECTING_MODELS` and
`SAMPLING_REJECTING_MODELS` recorded that the Anthropic 5-series rejected a
last-assistant-turn prefill and a non-default temperature. The prefill no longer
exists -- JSON mode is `response_format` now -- and the temperature constraint
was a property of that vendor's models, not of this transport. Both are gone
rather than disabled.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.config.llm_providers import (
    EMBEDDING_MODEL,
    JSON_MODE_REQUIRED_TOKEN,
    JSON_OBJECT_RESPONSE_FORMAT,
)

# ── The fixture directory, named once ────────────────────────────────────────

#: Path fragment, relative to `backend/tests/`, where the recorded payloads sit.
#: Declared here so an error message can tell a reader where to look without the
#: test package having to inject it.
FIXTURE_ROOT = "fixtures/vendor"


# ── Failure ──────────────────────────────────────────────────────────────────


class VendorContractViolation(RuntimeError):
    """A live response did not match the shape this codebase was built against.

    Raised on the FIRST call of a path only. It is deliberately not a subclass
    of `LLMUnavailableError`: every caller in this codebase catches that one and
    degrades to a deterministic fallback, which is the correct answer to an
    outage and precisely the wrong answer to "the vendor's response is not the
    shape we parse". A contract violation must reach a human.
    """

    def __init__(
        self, *, vendor: str, path: str, fixture: str, detail: str
    ) -> None:
        self.vendor = vendor
        self.path = path
        self.fixture = fixture
        self.detail = detail
        super().__init__(
            f"{vendor} response on path '{path}' disagrees with the recorded "
            f"contract fixture '{FIXTURE_ROOT}/{fixture}': {detail}. That "
            f"fixture was hand-authored from the vendor's published API schema "
            f"and has never been checked against a live call, so either the "
            f"documentation was wrong or the schema has changed. Do not widen "
            f"the parser until it is clear which."
        )


# ── The contracts ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ResponseContract:
    """One vendor response shape, and the fixture it was authored against.

    `required_top_level` is a mapping of key to the type the parser needs it to
    be, not merely to be present: `choices` arriving as an object rather than a
    list is the exact shape difference that makes `parse_response` return an
    empty string instead of raising.
    """

    name: str
    vendor: str
    fixture: str
    authored_from: str
    required_top_level: dict[str, type | tuple[type, ...]]
    #: Keys that must be present on every element of the list named by
    #: `element_list_key`, with the type each must carry.
    element_list_key: str | None = None
    required_per_element: dict[str, type | tuple[type, ...]] = field(
        default_factory=dict
    )


#: What a non-streaming Chat Completions body declares itself to be. Checked
#: rather than merely parsed, because the near-miss is a streaming chunk: it
#: carries `choices[0].delta` where this codebase reads `choices[0].message`,
#: and the parser's answer to that is an empty string.
CHAT_COMPLETION_OBJECT = "chat.completion"

#: The Chat Completions response. One contract for both models: the endpoint,
#: the choice list and the usage object are identical, and the split this
#: codebase cares about (Terra judges and writes, Luna extracts and classifies)
#: is a routing decision rather than a transport one.
#:
#: `object` is required and is not read by anything, for the same reason
#: `model` is required on the Voyage contract: it is the one cheap assertion
#: that the body is the KIND of body it claims to be. A streaming chunk carries
#: `chat.completion.chunk` and a choice with a `delta` rather than a `message`,
#: and `parse_response` would quietly return "" for one.
OPENAI_CHAT_COMPLETIONS = ResponseContract(
    name="openai.chat_completions",
    vendor="OpenAI",
    fixture="openai/chat_completion_terra_reasoning.json",
    authored_from=(
        "OpenAI Chat Completions published response schema: an object carrying "
        "id, object 'chat.completion', created, model, a choices list whose "
        "elements carry index, a message with role and content, and "
        "finish_reason, plus usage with prompt_tokens and completion_tokens."
    ),
    required_top_level={
        "object": str,
        "model": str,
        "choices": list,
        "usage": dict,
    },
    element_list_key="choices",
    required_per_element={"message": dict},
)

#: The Voyage embeddings response. `index` is load-bearing and is why this
#: contract exists at all: `embeddings._embed_batch` SORTS on it rather than
#: trusting arrival order, because an out-of-order batch attaches every
#: candidate's vector to the next candidate's row, and no test of the happy
#: path would ever show it.
VOYAGE_EMBEDDINGS = ResponseContract(
    name="voyage.embeddings",
    vendor="Voyage",
    fixture="voyage/embeddings_response_document.json",
    authored_from=(
        "Voyage embeddings API published response schema: an object with a "
        "data list of {object, embedding, index} rows, the model id, and a "
        "usage object carrying total_tokens."
    ),
    # NARROWER THAN THE RECORDED FIXTURE, DELIBERATELY. The fixture carries
    # `object` and `usage` because a real response does; they are not required
    # here because nothing in this codebase reads them, and a check that
    # rejected a perfectly usable response over a field no caller touches would
    # be a false positive taking down a working integration -- the exact
    # failure this module's own docstring warns about.
    #
    # `model` IS required, and is the one field here that is checked without
    # being read. A response echoing a different model id than the one
    # requested is a silent vector-space corruption: the column is the right
    # width, every cosine distance still computes, and the numbers mean
    # nothing. That is the same failure the BGE-to-Voyage swap already cost
    # this platform once.
    required_top_level={
        "data": list,
        "model": str,
    },
    element_list_key="data",
    required_per_element={"embedding": list, "index": int},
)

CONTRACTS: tuple[ResponseContract, ...] = (OPENAI_CHAT_COMPLETIONS, VOYAGE_EMBEDDINGS)


# ── Known request hazards, derived from published documentation ──────────────


def describe_request_hazards(model: str, payload: dict[str, Any]) -> tuple[str, ...]:
    """Published constraints this request body does not satisfy, if any.

    Returns sentences, not codes, because the one place this is read is a log
    line and an error message a human is looking at after a 400 they did not
    expect. NEVER quotes the payload: a chat request carries a real candidate's
    answers and a real job description, so the sentence is built from the model
    id and the SHAPE of the body rather than from its content.

    One hazard today. The published API rejects
    `response_format: {"type": "json_object"}` with a 400 unless the token
    "json" appears somewhere in the messages, and `llm_router`'s
    `_JSON_SYSTEM_SUFFIX` is the only thing in this codebase that satisfies it.
    That constant reads like ordinary prompt wording, so the check is here to
    make the day somebody rewords it a sentence rather than a silent, permanent
    degradation of every extraction call in the product.
    """
    hazards: list[str] = []

    response_format = payload.get("response_format")
    asks_for_json = (
        isinstance(response_format, dict)
        and response_format.get("type") == JSON_OBJECT_RESPONSE_FORMAT["type"]
    )
    if asks_for_json:
        messages = payload.get("messages")
        contents = (
            [str(m.get("content") or "") for m in messages if isinstance(m, dict)]
            if isinstance(messages, list)
            else []
        )
        if not any(JSON_MODE_REQUIRED_TOKEN in text.lower() for text in contents):
            hazards.append(
                f"the request asks for the "
                f"{JSON_OBJECT_RESPONSE_FORMAT['type']} response format and no "
                f"message contains the token {JSON_MODE_REQUIRED_TOKEN!r}, "
                f"which the published API requires before it will accept that "
                f"format; the call to {model} is rejected with a 400 and the "
                f"remedy is in llm_router._JSON_SYSTEM_SUFFIX, not in the "
                f"caller's prompt"
            )

    return tuple(hazards)


# ── First-live-use enforcement ───────────────────────────────────────────────

#: Paths already checked in this process. A set rather than a counter: the
#: question is "has this path ever been seen", and nothing downstream is
#: interested in how many times.
_checked: set[str] = set()


def already_checked(path: str) -> bool:
    """True when this path has had its first live response checked."""
    return path in _checked


def reset_first_use() -> None:
    """Forget which paths have been checked. Tests and `verify_live.py` only."""
    _checked.clear()


def _check_shape(
    contract: ResponseContract, path: str, payload: object
) -> None:
    """Raise `VendorContractViolation` if `payload` is not the declared shape."""
    if not isinstance(payload, dict):
        raise VendorContractViolation(
            vendor=contract.vendor,
            path=path,
            fixture=contract.fixture,
            detail=(
                f"the response body is a {type(payload).__name__}, and the "
                f"contract requires a JSON object"
            ),
        )

    for key, expected in contract.required_top_level.items():
        if key not in payload:
            raise VendorContractViolation(
                vendor=contract.vendor,
                path=path,
                fixture=contract.fixture,
                detail=f"the response has no '{key}' key",
            )
        if not isinstance(payload[key], expected):
            names = (
                expected.__name__
                if isinstance(expected, type)
                else "/".join(t.__name__ for t in expected)
            )
            raise VendorContractViolation(
                vendor=contract.vendor,
                path=path,
                fixture=contract.fixture,
                detail=(
                    f"'{key}' is a {type(payload[key]).__name__} and the "
                    f"contract requires {names}"
                ),
            )

    if contract.element_list_key is None:
        return

    elements = payload[contract.element_list_key]
    if not isinstance(elements, list):  # pragma: no cover -- typed above
        return
    if not elements:
        raise VendorContractViolation(
            vendor=contract.vendor,
            path=path,
            fixture=contract.fixture,
            detail=f"'{contract.element_list_key}' is empty",
        )
    for position, element in enumerate(elements):
        if not isinstance(element, dict):
            raise VendorContractViolation(
                vendor=contract.vendor,
                path=path,
                fixture=contract.fixture,
                detail=(
                    f"'{contract.element_list_key}[{position}]' is a "
                    f"{type(element).__name__} and the contract requires an "
                    f"object"
                ),
            )
        for key, expected in contract.required_per_element.items():
            if key not in element:
                raise VendorContractViolation(
                    vendor=contract.vendor,
                    path=path,
                    fixture=contract.fixture,
                    detail=(
                        f"'{contract.element_list_key}[{position}]' has no "
                        f"'{key}' key"
                    ),
                )
            if not isinstance(element[key], expected):
                names = (
                    expected.__name__
                    if isinstance(expected, type)
                    else "/".join(t.__name__ for t in expected)
                )
                raise VendorContractViolation(
                    vendor=contract.vendor,
                    path=path,
                    fixture=contract.fixture,
                    detail=(
                        f"'{contract.element_list_key}[{position}].{key}' is a "
                        f"{type(element[key]).__name__} and the contract "
                        f"requires {names}"
                    ),
                )


def openai_path(model: str) -> str:
    """The first-use path key for one OpenAI model.

    Per MODEL rather than per endpoint. Both models are served by the same URL
    and the same parser, but they are billed separately, keyed separately and
    -- since neither id has ever been resolved -- may not both exist. One
    checked path must not vouch for the other.
    """
    return f"openai.chat_completions:{model}"


def voyage_path() -> str:
    """The first-use path key for the embeddings endpoint."""
    return f"voyage.embeddings:{EMBEDDING_MODEL}"


def check_openai_response(
    payload: object, *, model: str, json_mode: bool, force: bool = False
) -> None:
    """Check a Chat Completions response against the contract, once per model.

    `json_mode` is checked as well as the envelope, because JSON mode's whole
    claim is that the response is ONE TOP-LEVEL OBJECT. That claim used to rest
    on an assistant-turn prefill that made the opening brace structurally
    unavoidable; it now rests on `response_format`, which is a stronger
    guarantee and, unlike the prefill, a guarantee made entirely on the
    vendor's side of the wire. This is where that guarantee is checked rather
    than assumed: text that does not open with a brace reaches the caller's
    `json.loads` as a parse error with no explanation attached, and the caller
    then degrades exactly as it would for an outage.

    `force` bypasses the once-per-path memo. `verify_live.py` uses it; nothing
    on a request path should.
    """
    path = openai_path(model)
    if not force and path in _checked:
        return
    _check_shape(OPENAI_CHAT_COMPLETIONS, path, payload)

    assert isinstance(payload, dict)  # narrowed by _check_shape
    if payload["object"] != CHAT_COMPLETION_OBJECT:
        raise VendorContractViolation(
            vendor="OpenAI",
            path=path,
            fixture=OPENAI_CHAT_COMPLETIONS.fixture,
            detail=(
                f"the response declares object {payload['object']!r} and this "
                f"codebase parses {CHAT_COMPLETION_OBJECT!r}; a streaming chunk "
                f"carries a 'delta' where the parser reads a 'message' and "
                f"would yield an empty string rather than raising"
            ),
        )

    message = payload["choices"][0]["message"]
    content = message.get("content")
    if not isinstance(content, str):
        raise VendorContractViolation(
            vendor="OpenAI",
            path=path,
            fixture=OPENAI_CHAT_COMPLETIONS.fixture,
            detail=(
                f"choices[0].message.content is "
                f"{type(content).__name__ if content is not None else 'null'} "
                f"and every caller in this codebase reads text; a null content "
                f"is the tool-call shape, which this platform never requests"
            ),
        )

    usage = payload["usage"]
    if not isinstance(usage, dict) or "prompt_tokens" not in usage:
        raise VendorContractViolation(
            vendor="OpenAI",
            path=path,
            fixture=OPENAI_CHAT_COMPLETIONS.fixture,
            detail=(
                "'usage' carries no 'prompt_tokens', so cost accounting would "
                "report zero for a call that was billed"
            ),
        )

    if json_mode:
        head = content.lstrip()
        if not head.startswith("{"):
            opening = head[:12] if head else "an empty string"
            raise VendorContractViolation(
                vendor="OpenAI",
                path=path,
                fixture="openai/chat_completion_luna_json.json",
                detail=(
                    f"the response was requested in JSON object mode and its "
                    f"text opens with {opening!r} rather than an opening "
                    f"brace, so the format did not constrain it and every "
                    f"JSON-mode caller in this codebase would fail its "
                    f"json.loads with no explanation attached"
                ),
            )

    _checked.add(path)


def check_voyage_response(
    payload: object,
    *,
    expected_rows: int,
    expected_dimension: int | None,
    force: bool = False,
) -> None:
    """Check an embeddings response against the contract, once per process.

    `expected_dimension` may be None, which skips the width check. The recorded
    fixture carries vectors truncated to a readable handful of components
    (stated in its own provenance block), so the shape and the width are
    separate assertions rather than one that cannot be made against a fixture a
    person can read.
    """
    path = voyage_path()
    if not force and path in _checked:
        return
    _check_shape(VOYAGE_EMBEDDINGS, path, payload)

    assert isinstance(payload, dict)  # narrowed by _check_shape
    if payload["model"] != EMBEDDING_MODEL:
        raise VendorContractViolation(
            vendor="Voyage",
            path=path,
            fixture=VOYAGE_EMBEDDINGS.fixture,
            detail=(
                f"the response was produced by '{payload['model']}' and the "
                f"request named '{EMBEDDING_MODEL}'; two models' vectors share "
                f"a column width and nothing else"
            ),
        )

    rows = payload["data"]
    if len(rows) != expected_rows:
        raise VendorContractViolation(
            vendor="Voyage",
            path=path,
            fixture=VOYAGE_EMBEDDINGS.fixture,
            detail=(
                f"the batch asked for {expected_rows} vectors and the response "
                f"carries {len(rows)}"
            ),
        )

    indices = sorted(int(row["index"]) for row in rows)
    if indices != list(range(expected_rows)):
        raise VendorContractViolation(
            vendor="Voyage",
            path=path,
            fixture=VOYAGE_EMBEDDINGS.fixture,
            detail=(
                "the 'index' values are not 0..n-1, so sorting on them cannot "
                "restore the request order and every vector could be attached "
                "to the wrong row"
            ),
        )

    if expected_dimension is not None:
        widths = {len(row["embedding"]) for row in rows}
        if widths != {expected_dimension}:
            raise VendorContractViolation(
                vendor="Voyage",
                path=path,
                fixture=VOYAGE_EMBEDDINGS.fixture,
                detail=(
                    f"the vectors are {sorted(widths)} components wide and the "
                    f"schema's columns are vector({expected_dimension})"
                ),
            )

    _checked.add(path)


__all__ = [
    "CHAT_COMPLETION_OBJECT",
    "CONTRACTS",
    "FIXTURE_ROOT",
    "OPENAI_CHAT_COMPLETIONS",
    "VOYAGE_EMBEDDINGS",
    "ResponseContract",
    "VendorContractViolation",
    "already_checked",
    "check_openai_response",
    "check_voyage_response",
    "describe_request_hazards",
    "openai_path",
    "reset_first_use",
    "voyage_path",
]
