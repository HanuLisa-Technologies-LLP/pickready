"""The response shapes this codebase assumes its two vendors return, and the
first-live-use check that makes a wrong assumption announce itself.

spec-doc6 §12.5. There is no Anthropic key and no Voyage key in this phase, so
every statement this module makes about the vendors is derived from their
PUBLISHED API SCHEMAS and has never been observed against a live endpoint. That
is not a caveat to skim: a shape derived from documentation is a guess with a
citation, and the failure mode of a wrong guess here is silent. `parse_response`
in `llm_router` reads `content[].text` and returns "" when the block list is
shaped differently; `embeddings._embed_batch` sorts on `data[].index` and would
raise only if the key were absent entirely. Neither notices a response that is
merely DIFFERENT from what was expected, and a report written from an empty
string reads exactly like a report written from a model that had little to say.

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

THE KNOWN REQUEST HAZARDS ARE NOT SPECULATION, AND THEY ARE NOT FIXED HERE
---------------------------------------------------------------------------
`describe_request_hazards` records two published constraints that the request
this codebase currently builds does not satisfy on the Sonnet 5 path. They are
recorded rather than worked around because changing the request shape changes
what every Sonnet call sends, which is an owner decision and not a typing
cleanup. Their entries in `VERIFICATION_PENDING.md` carry the command that
settles them. What this module guarantees is that if they are real, the first
live call says so in one specific sentence instead of degrading quietly into
every caller's deterministic fallback.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.config.llm_providers import EMBEDDING_MODEL, MODEL_SONNET

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
    be, not merely to be present: `content` arriving as a string rather than a
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


#: The Messages API response. One contract for both models: the endpoint,
#: the block list and the usage object are identical, and the split this
#: codebase cares about (Sonnet judges and writes, Haiku extracts and
#: classifies) is a routing decision rather than a transport one.
ANTHROPIC_MESSAGES = ResponseContract(
    name="anthropic.messages",
    vendor="Anthropic",
    fixture="anthropic/messages_response_sonnet_reasoning.json",
    authored_from=(
        "Anthropic Messages API published response schema: a message object "
        "carrying id, type, role, model, a content block list, stop_reason and "
        "usage with input_tokens and output_tokens."
    ),
    required_top_level={
        "type": str,
        "role": str,
        "model": str,
        "content": list,
        "usage": dict,
    },
    element_list_key="content",
    required_per_element={"type": str},
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

CONTRACTS: tuple[ResponseContract, ...] = (ANTHROPIC_MESSAGES, VOYAGE_EMBEDDINGS)


# ── Known request hazards, derived from published documentation ──────────────

#: Models whose published API rejects a last-assistant-turn prefill with a 400.
#:
#: `llm_router.build_payload` appends `{"role": "assistant", "content": "{"}` in
#: JSON mode. That is the mechanism CLAUDE.md records as making JSON mode
#: structural rather than advisory, and it is correct for a model that accepts
#: a prefill. The published schema says the 5-series does not.
PREFILL_REJECTING_MODELS: frozenset[str] = frozenset({MODEL_SONNET})

#: Models whose published API rejects a non-default `temperature` with a 400.
#:
#: `build_payload` always sends `temperature`, from
#: `config.llm_providers.temperature_for(task_type)`. Every value that function
#: returns in this codebase is 0.0 or 0.7, and both are non-default.
SAMPLING_REJECTING_MODELS: frozenset[str] = frozenset({MODEL_SONNET})

#: The Messages API `temperature` default. A request that omits the parameter,
#: or sends exactly this, is accepted by every model in the roster.
DEFAULT_TEMPERATURE = 1.0


def describe_request_hazards(model: str, payload: dict[str, Any]) -> tuple[str, ...]:
    """Published constraints this request body does not satisfy, if any.

    Returns sentences, not codes, because the one place this is read is a log
    line and an error message a human is looking at after a 400 they did not
    expect. Never quotes the payload: a Messages request carries a real
    candidate's answers and a real job description.
    """
    hazards: list[str] = []

    turns = payload.get("messages")
    if (
        model in PREFILL_REJECTING_MODELS
        and isinstance(turns, list)
        and turns
        and isinstance(turns[-1], dict)
        and turns[-1].get("role") == "assistant"
    ):
        hazards.append(
            f"the request ends on an assistant turn (JSON mode's prefill) and "
            f"the published schema for {model} rejects a last-assistant-turn "
            f"prefill with a 400; the documented replacement is a structured "
            f"output format or a system-prompt instruction"
        )

    temperature = payload.get("temperature")
    if (
        model in SAMPLING_REJECTING_MODELS
        and isinstance(temperature, (int, float))
        and float(temperature) != DEFAULT_TEMPERATURE
    ):
        hazards.append(
            f"the request sets a non-default temperature and the published "
            f"schema for {model} rejects temperature, top_p and top_k with a "
            f"400; the documented replacement is to omit the parameter"
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


def anthropic_path(model: str) -> str:
    """The first-use path key for one Anthropic model."""
    return f"anthropic.messages:{model}"


def voyage_path() -> str:
    """The first-use path key for the embeddings endpoint."""
    return f"voyage.embeddings:{EMBEDDING_MODEL}"


def check_anthropic_response(
    payload: object, *, model: str, json_mode: bool, force: bool = False
) -> None:
    """Check a Messages API response against the contract, once per model.

    `json_mode` is checked as well as the envelope, because JSON mode's whole
    claim is that the response is a top-level object: the prefill seeds the
    assistant turn with an opening brace and `parse_response` prepends it back.
    A first text block that opens with a fence or an apology means the prefill
    did not take, and the caller's `json.loads` would be the thing that noticed
    -- as a parse error with no explanation attached.

    `force` bypasses the once-per-path memo. `verify_live.py` uses it; nothing
    on a request path should.
    """
    path = anthropic_path(model)
    if not force and path in _checked:
        return
    _check_shape(ANTHROPIC_MESSAGES, path, payload)

    assert isinstance(payload, dict)  # narrowed by _check_shape
    blocks = payload["content"]
    text_blocks = [
        b for b in blocks if isinstance(b, dict) and b.get("type") == "text"
    ]
    if not text_blocks:
        types = ", ".join(
            str(b.get("type")) for b in blocks if isinstance(b, dict)
        )
        raise VendorContractViolation(
            vendor="Anthropic",
            path=path,
            fixture=ANTHROPIC_MESSAGES.fixture,
            detail=(
                f"no content block has type 'text' (saw: {types or 'none'}), "
                f"and every caller in this codebase reads text"
            ),
        )

    usage = payload["usage"]
    if not isinstance(usage, dict) or "input_tokens" not in usage:
        raise VendorContractViolation(
            vendor="Anthropic",
            path=path,
            fixture=ANTHROPIC_MESSAGES.fixture,
            detail=(
                "'usage' carries no 'input_tokens', so cost accounting would "
                "report zero for a call that was billed"
            ),
        )

    if json_mode:
        head = str(text_blocks[0].get("text") or "").lstrip()
        if head.startswith("```") or head.startswith('"'):
            raise VendorContractViolation(
                vendor="Anthropic",
                path=path,
                fixture="anthropic/messages_response_haiku_json_prefill.json",
                detail=(
                    "the first text block opens with a fence or a quote, so "
                    "the assistant-turn prefill did not constrain the "
                    "response and prepending the opening brace back would "
                    "produce text that is not JSON"
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
    "ANTHROPIC_MESSAGES",
    "CONTRACTS",
    "DEFAULT_TEMPERATURE",
    "FIXTURE_ROOT",
    "PREFILL_REJECTING_MODELS",
    "SAMPLING_REJECTING_MODELS",
    "VOYAGE_EMBEDDINGS",
    "ResponseContract",
    "VendorContractViolation",
    "already_checked",
    "anthropic_path",
    "check_anthropic_response",
    "check_voyage_response",
    "describe_request_hazards",
    "reset_first_use",
    "voyage_path",
]
