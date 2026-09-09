"""The second judge vendor. Groq, and the same isolation rules as Gemini.

WHY GROQ IS BACK, WHEN spec-doc5 DELETED IT
---------------------------------------------
It deleted Groq as a PRODUCT model vendor, and that stands: `MODEL_FOR_TASK` is
still a closed mapping onto `gpt-5.6-terra` and `gpt-5.6-luna`, there is still
no fallback chain, and nothing under `app/services/` may reach this module.
What comes back is Groq as a JUDGE, which is a different role with the opposite
requirement: the product wants one reviewed vendor, and a jury wants vendors the
product does not use.

`app/evaluation/` is the one prefix the model-id grep exempts, and the exemption
is backed by `tests/test_judge_isolation.py` proving by AST that no route or
worker can reach this package. A Groq model id is safe here for a reason that is
checked.

WHY A SECOND VENDOR AT ALL, WHEN GEMINI ALREADY EXISTS
--------------------------------------------------------
Because Gemini's free tier could not finish the W7.2 probe: the daily allowance
ran out at roughly a third of 600 calls and every key answered 429. A
measurement that cannot be completed is not a measurement, and a gate threshold
resting on eight usable calls would be a number describing nothing.

This is NOT a fallback chain. Both vendors are declared, both are probed, and
the panel is whichever ones ANSWER. A jury's value comes from heterogeneity;
having two vendors available is the point, not a redundancy.

WHICH MODELS, AND THE SELF-PREFERENCE CAVEAT STATED HONESTLY
--------------------------------------------------------------
Every id below answered an actual chat-completions call on 2026-09-09, resolved
by CALL and never by reading the model list -- the same discipline the Gemini
module records, for the same reason: that account's listing advertises three
2.5-family ids which all answer 404 "no longer available to new users".

`qwen/qwen3.8-27b` and `qwen/qwen3.6-27b` are the independent legs: a different
lab from the product's models entirely. `openai/gpt-oss-120b` is open-weight and
architecturally distinct from `gpt-5.6-*`, but it shares an OpenAI lineage with
them, so it is the leg most exposed to self-preference (published at roughly
+10% to +25% win rate for a model's own family). It is included for panel
heterogeneity and it is a MINORITY of the panel deliberately: on a majority
vote it cannot carry a verdict alone.

`groq/compound-mini` is deliberately absent. It is an agentic system that may
call tools, so its answer is not a function of the prompt alone, and a juror
whose verdict depends on what a search returned that minute cannot be measured
for self-agreement.

THE CLOUDFLARE HEADER IS LOAD BEARING
---------------------------------------
Groq sits behind Cloudflare, which answers `403` with error code `1010` to
urllib's default `User-Agent`. That is not a credential failure and not a rate
limit, and without a `User-Agent` header EVERY call fails in a way that reads
like a rejected key. Stated here because the fix is one header and the symptom
points somewhere else entirely.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Sequence

logger = logging.getLogger(__name__)

#: The panel. Every id resolved by a live chat-completions CALL, never by
#: reading the model list. See the module docstring for the self-preference
#: caveat on the gpt-oss leg.
JUDGE_MODELS: tuple[str, ...] = (
    "qwen/qwen3.8-27b",
    "qwen/qwen3.6-27b",
    "openai/gpt-oss-120b",
)

#: The credential slots, enumerated and every one optional, the same roster
#: convention `.env.example` describes. Rotated over rather than taken first:
#: the free tier meters per key.
CREDENTIAL_SLOTS: tuple[str, ...] = (
    "GROQ_API_KEY_1",
    "GROQ_API_KEY_2",
    "GROQ_API_KEY_3",
)

ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"

#: Without this the edge answers 403 code 1010 to every call. Not optional.
USER_AGENT = "readypick-eval/1.0"

REQUEST_TIMEOUT_SECONDS = 60.0

RETRYABLE_FAULTS: frozenset[str] = frozenset(
    {"quota_exhausted", "vendor_overloaded", "rate_limited"}
)

MAX_ATTEMPTS = 4
BACKOFF_SECONDS: tuple[float, ...] = (5.0, 15.0, 30.0)

_FAULT_FRAGMENTS: tuple[tuple[str, str], ...] = (
    ("decommissioned", "model_retired"),
    ("does not exist", "model_unsupported"),
    ("rate limit", "rate_limited"),
    ("quota", "quota_exhausted"),
    ("over capacity", "vendor_overloaded"),
    ("invalid api key", "credential_rejected"),
    ("unauthorized", "credential_rejected"),
)

ERROR_PREFIX = "error:"

#: Some reasoning models emit their scratchpad inside the content. The verdict
#: is what follows it. Stripping rather than refusing, because a `<think>` block
#: is the model working, not the model failing: `qwen/qwen3.6-27b` returns one
#: on every call and would otherwise be permanently unmeasurable.
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_UNCLOSED_THINK = re.compile(r"<think>.*\Z", re.DOTALL | re.IGNORECASE)


def credentials() -> list[str]:
    """EVERY populated slot, not the first. The meter is per key."""
    found = [v for slot in CREDENTIAL_SLOTS if (v := os.environ.get(slot, "").strip())]
    if not found:
        raise RuntimeError(
            "no Groq credential: set one of " + ", ".join(CREDENTIAL_SLOTS)
        )
    return found


#: Status codes whose MEANING is unambiguous without reading the prose. The
#: message is still consulted first, because it distinguishes conditions a code
#: cannot -- a 404 is a retired model or a typo -- but a code-only fallback is
#: what stops a vendor rewording its 429 from silently turning a retryable
#: fault into an unretried one. That happened on the first Groq run: the body
#: did not match any fragment, the fault read `unclassified`, and the call was
#: never retried because `unclassified` is not in RETRYABLE_FAULTS.
_FAULT_BY_STATUS: dict[int, str] = {
    429: "rate_limited",
    500: "vendor_overloaded",
    502: "vendor_overloaded",
    503: "vendor_overloaded",
    504: "vendor_overloaded",
    401: "credential_rejected",
    403: "credential_rejected",
}


def fault_class(body: bytes, status: int | None = None) -> str:
    """Name WHICH fault a status carried, without echoing the body.

    Message first, status second. The message can separate conditions the code
    cannot; the status catches the conditions a reworded message would lose.
    """
    message = ""
    try:
        payload = json.loads(body)
        message = str(payload.get("error", {}).get("message", "") or payload)
    except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
        message = ""
    lowered = message.lower()
    for fragment, fault in _FAULT_FRAGMENTS:
        if fragment in lowered:
            return fault
    if status is not None and status in _FAULT_BY_STATUS:
        return _FAULT_BY_STATUS[status]
    return "unreadable_body" if not message else "unclassified"


def strip_reasoning(text: str) -> str:
    """Remove a `<think>` scratchpad, closed or truncated, and return the rest.

    The unclosed case is not hypothetical: a model that hits its token ceiling
    mid-thought emits an opening tag and no closing one, and a regex that only
    matched closed blocks would hand the caller a whole scratchpad as if it were
    the verdict.
    """
    without = _THINK_BLOCK.sub(" ", text)
    return _UNCLOSED_THINK.sub(" ", without).strip()


def call_once(
    model: str,
    prompt: str,
    api_key: str,
    *,
    seed: int | None,
    scale: Sequence[str],
    max_output_tokens: int = 2048,
) -> str:
    """One call. Returns a word from `scale`, or a marker naming the fault.

    Never raises. `max_completion_tokens`, not `max_tokens`: the platform's own
    vendor work already recorded that the newer OpenAI-shaped APIs refuse the
    older name with a 400 `unsupported_parameter`, and Groq speaks that shape.
    """
    body: dict[str, object] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_completion_tokens": max_output_tokens,
    }
    if seed is not None:
        body["seed"] = seed

    request = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # See the module docstring. Without this every call is a 403.
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(
            request, timeout=REQUEST_TIMEOUT_SECONDS
        ) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return f"{ERROR_PREFIX}http_{exc.code}:{fault_class(exc.read(), exc.code)}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return f"{ERROR_PREFIX}{type(exc).__name__}"
    except json.JSONDecodeError:
        return f"{ERROR_PREFIX}unparseable_success_body"

    try:
        choice = payload["choices"][0]
        text = choice["message"].get("content") or ""
    except (KeyError, IndexError, TypeError):
        return f"{ERROR_PREFIX}malformed_no_text"

    word = strip_reasoning(text).lower().strip().strip(".")
    word = word.replace(" ", "_").replace("-", "_")
    return word if word in tuple(scale) else f"off_scale:{word[:40]}"


def call(
    model: str,
    prompt: str,
    keys: Sequence[str],
    *,
    seed: int | None,
    scale: Sequence[str],
    offset: int = 0,
) -> str:
    """One call, rotating keys and retrying ONLY metering faults.

    Identical contract to `gemini.call`, deliberately: `determinism.run_probe`
    takes a vendor MODULE and must not care which one it was handed.
    """
    if not keys:
        raise ValueError("call() needs at least one credential")
    last = ""
    for attempt in range(MAX_ATTEMPTS):
        key = keys[(offset + attempt) % len(keys)]
        last = call_once(model, prompt, key, seed=seed, scale=scale)
        if not last.startswith(ERROR_PREFIX):
            return last
        if last.rsplit(":", 1)[-1] not in RETRYABLE_FAULTS:
            return last
        if attempt < len(BACKOFF_SECONDS):
            time.sleep(BACKOFF_SECONDS[attempt])
    return last
