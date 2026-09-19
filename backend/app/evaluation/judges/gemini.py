"""The judge transport. Gemini, and deliberately NOT the product's two models.

WHY THE JUDGE MODELS LIVE HERE AND NOT IN A SCRIPT
----------------------------------------------------
The first draft of the W7.2 probe put these ids in `app/scripts/`, and
`tests/test_llm_task_routing.py::test_no_other_model_id_appears_in_executable_code`
failed it immediately. That test is right and the placement was wrong.

`MODEL_FOR_TASK` is a closed mapping onto `gpt-5.6-terra` and `gpt-5.6-luna`,
and the value of that closure is that a candidate's GRADE cannot be produced by
an unreviewed model. `app/evaluation/` is the ONE exempt prefix, and the
exemption is not a convenience: `tests/test_judge_isolation.py` proves by AST
that nothing under `app/services/` imports this package and that no route or
worker can reach it, and
`test_the_model_grep_exemption_is_backed_by_the_isolation_test` fails if that
proof is ever deleted. A judge model id is safe here for a reason that is
checked, and would have been unchecked in a script.

It also removes a second copy. The determinism probe measures a panel and
`configured_jurors()` calls one, and those two lists have to be the same list or
the measured self-disagreement describes models that are not sitting on the
jury.

WHY GEMINI AT ALL
------------------
Self-preference. A model scoring its own family's output is worth roughly +10%
to +25% in win rate, so a judge drawn from the product's own vendor would
measure loyalty as quality. A third vendor is the cheapest structural answer.

WHICH IDS, AND HOW THEY WERE CHOSEN
-------------------------------------
Every id below answered an actual `generateContent` call on 2026-09-09. That is
not the same check as reading the model list, and the difference is not
academic: `models.list` on this account advertises `gemini-2.5-pro`,
`gemini-2.5-flash` and `gemini-2.5-flash-lite`, and all three answer
`generateContent` with 404 "no longer available to new users". A LISTING IS NOT
A CAPABILITY -- the `voyage-context-4` lesson in a new shape.

The `-pro` tier is absent because these keys answer it 429, not because it was
judged unsuitable: a juror that cannot be called is not a juror. Every
`-preview` id is absent because a threshold measured from a preview model stops
measuring anything the day the preview is withdrawn.

THE CREDENTIAL ROSTER IS THE THROUGHPUT
-----------------------------------------
The free tier meters per key per minute. The first probe run leaned on one key
and returned eleven `429`s out of twenty calls on a single case, which would
have been recorded as the model disagreeing with itself. Rotating over every
populated slot is what makes the measurement about the model.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from collections.abc import Sequence

logger = logging.getLogger(__name__)

#: The panel. Every id resolved by a live `generateContent` call, never by
#: reading the model list. See the module docstring for why that distinction
#: has its own paragraph.
JUDGE_MODELS: tuple[str, ...] = (
    "gemini-3.5-flash",
    "gemini-3.6-flash",
    "gemini-3.7-flash",
)

#: The credential slots. Enumerated, every one optional, the same roster
#: convention `.env.example` describes for the rest of the platform. Rotated
#: over rather than taken first: the meter is per key.
CREDENTIAL_SLOTS: tuple[str, ...] = (
    "GEMINI_API_KEY_1",
    "GEMINI_API_KEY_2",
    "GEMINI_API_KEY_3",
)

ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

REQUEST_TIMEOUT_SECONDS = 60.0

#: Faults that mean "the question never got asked", as opposed to "the model
#: answered". They are RETRIED rather than recorded. The distinction is the
#: reason this set is named rather than inlined: a quota rejection is the
#: harness hitting a meter, not the judge wavering, and counting it as
#: dispersion reports the free tier's rate limit as a property of the model.
RETRYABLE_FAULTS: frozenset[str] = frozenset({"quota_exhausted", "vendor_overloaded"})

#: Attempts per call before a transport fault is recorded as the answer.
#: Bounded, because a caller that retried forever would hang rather than report.
MAX_ATTEMPTS = 4

#: Seconds to wait after a retryable fault, per attempt. Explicit rather than
#: computed so the worst case per call is readable: 15 + 30 + 60 seconds.
#:
#: Sized against a PER-MINUTE meter, because that is what was measured: on
#: 2026-09-09 key 1 answered 200 while keys 2 and 3 answered 429 in the same
#: second, so the slots are separate projects with separate meters rather than
#: one pool. A backoff shorter than the meter's window retries inside the same
#: window and just spends attempts.
BACKOFF_SECONDS: tuple[float, ...] = (15.0, 30.0, 60.0)

#: Vendor message fragments mapped to the fault they mean. One status code
#: covers several unrelated conditions, so the code alone cannot tell an
#: operator which happened. Matched on a fragment because the prose around it
#: changes; an unmatched message reports `unclassified` rather than being forced
#: into the nearest bin.
_FAULT_FRAGMENTS: tuple[tuple[str, str], ...] = (
    ("no longer available", "model_retired"),
    ("is not supported", "model_unsupported"),
    ("exceeded your current quota", "quota_exhausted"),
    ("high demand", "vendor_overloaded"),
    ("api key not valid", "credential_rejected"),
    ("permission", "credential_forbidden"),
)

#: The prefix every non-answer carries, so a caller can separate verdicts from
#: failures with one test rather than by knowing every fault name.
ERROR_PREFIX = "error:"


def credentials() -> list[str]:
    """EVERY populated slot, not the first.

    Returns VALUES and none is ever printed by this module. The refusal names
    the VARIABLES, which is what an operator can act on.
    """
    found = [v for slot in CREDENTIAL_SLOTS if (v := os.environ.get(slot, "").strip())]
    if not found:
        raise RuntimeError(
            "no Gemini credential: set one of " + ", ".join(CREDENTIAL_SLOTS)
        )
    return found


def fault_class(body: bytes) -> str:
    """Name WHICH fault a status code carried, without echoing the body.

    The body is not returned and not logged. The probe's payloads are synthetic,
    but this is the function a future caller reaches for when they are not, and
    a fault name is everything an operator needs from it.
    """
    try:
        message = json.loads(body).get("error", {}).get("message", "")
    except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
        # An unreadable error body is itself the answer, and the narrow tuple
        # is deliberate: a bare `except` here would swallow a programming error
        # in the line above and report it as a vendor fault.
        return "unreadable_body"
    for fragment, fault in _FAULT_FRAGMENTS:
        if fragment in message.lower():
            return fault
    return "unclassified"


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

    Never raises. A transport failure returns `error:<code>:<fault>` and an
    answer outside `scale` returns `off_scale:<word>`; both are recorded by the
    caller rather than dropped, because a call that failed is a real way for a
    panel not to agree and hiding it would report a cleaner result than a
    caller would experience.
    """
    config: dict[str, object] = {
        "temperature": 0.0,
        # Enough for one word plus the reasoning budget the 3.x models spend
        # before it. Too small a ceiling truncates before the answer and is
        # indistinguishable from the model refusing to commit.
        "maxOutputTokens": max_output_tokens,
    }
    if seed is not None:
        config["seed"] = seed

    body = json.dumps(
        {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": config,
        }
    ).encode()
    request = urllib.request.Request(
        ENDPOINT.format(model=model),
        data=body,
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(
            request, timeout=REQUEST_TIMEOUT_SECONDS
        ) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        # The CODE alone is not enough, and this was learned expensively: the
        # probe's first run reported a self-disagreement of 0.0000 with every
        # case unanimous, entirely out of 404s. "The model is retired" was
        # indistinguishable from "the id has a typo" and from a real verdict of
        # perfect agreement.
        return f"{ERROR_PREFIX}http_{exc.code}:{fault_class(exc.read())}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return f"{ERROR_PREFIX}{type(exc).__name__}"
    except json.JSONDecodeError:
        return f"{ERROR_PREFIX}unparseable_success_body"

    try:
        parts = payload["candidates"][0]["content"]["parts"]
        text = "".join(part.get("text", "") for part in parts)
    except (KeyError, IndexError, TypeError):
        try:
            finish = str(payload["candidates"][0].get("finishReason", ""))
        except (KeyError, IndexError, TypeError):
            finish = ""
        return f"{ERROR_PREFIX}malformed_{finish or 'no_text'}"

    word = text.strip().lower().strip(".").replace(" ", "_").replace("-", "_")
    # An answer outside the scale is recorded as itself, never coerced to the
    # nearest label. Coercing would hide exactly the failure mode a judge has:
    # answering something the protocol has no bin for.
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

    `offset` advances per call so consecutive calls land on different keys.
    Within one call a retryable fault moves to the NEXT key before waiting,
    because a per-key minute meter is cleared by using a different key rather
    than by waiting on the exhausted one.

    A non-retryable fault -- a retired model, a rejected credential, malformed
    output -- returns immediately. Retrying those turns a permanent condition
    into a slow permanent condition, which is why `llm_router` trips its breaker
    on a credential failure at the FIRST occurrence rather than the third.
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
