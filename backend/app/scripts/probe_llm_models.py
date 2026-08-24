"""Ask every configured provider whether the model id we send it still exists.

WHY THIS SCRIPT EXISTS
----------------------
A retired model id is the single most common way a whole provider tier goes
dark, and it has now happened three times:

  * `openrouter/...:free` began hard-404ing, killing the third fallback tier;
  * `gemini-2.0-flash` was withdrawn from the free tier and answered 429 with
    `limit: 0`, which is not a rate limit and never clears;
  * `llama-3.3-70b-versatile` was removed from Groq's model list and answered
    404 to every key (measured 2026-08-23).

None of these look like a configuration error from the inside. The router does
exactly what it is supposed to do -- records a failure per key, walks to the
next tier -- so the only symptom is that AI output gets slower and then quietly
degrades to a deterministic template. The product reports "AI unavailable"
while every credential is perfectly valid.

WHAT IT CHECKS, AND WHY THREE MODES
-----------------------------------
Three calls per provider: plain text, JSON mode, and a REALISTIC payload. All
three are genuinely different questions, and each caught a real outage that the
other two missed.

  text   Does the model id still resolve at all. Three tiers have gone dark
         this way.
  json   Groq's gpt-oss family answers plain text happily and, without
         `reasoning_effort`, INTERMITTENTLY fails `response_format=json_object`
         with `json_validate_failed`. A probe that only asked for prose would
         report a healthy tier that drops a fraction of every structured call.
  size   The one this script was missing, and the omission mattered. Its first
         version asked every provider to "Say OK" and reported Groq healthy at
         580ms. It WAS healthy, for two-token requests. In production the same
         tier answered HTTP 413 to every real call, because a resume extraction
         is ~12k tokens and the account's ceiling is 8000 per minute.

A liveness probe that passes on input unlike the input the product sends is not
a liveness probe. It is a green tick.

It also reports LATENCY, because a tier that works but takes eleven seconds
cannot serve an interactive deadline of twenty-six, and that is indistinguishable
from an outage to the person waiting.

USAGE
-----
    python -m app.scripts.probe_llm_models            # every provider
    python -m app.scripts.probe_llm_models groq       # one provider

Exit code is 1 if any configured provider fails any of the three, so this can
gate a deploy or run on a schedule.
"""
from __future__ import annotations

import asyncio
import sys
import time
from typing import Any

import httpx

from app.config.llm_providers import PROVIDER_MODELS, extra_params_for
from app.services import llm_router

#: Deliberately trivial, and deliberately NOT a real prompt. The question is
#: "does this model id resolve and answer", not "is the output any good".
_TEXT_MESSAGES = [
    {"role": "system", "content": "Reply with exactly one word."},
    {"role": "user", "content": "Say OK."},
]
_JSON_MESSAGES = [
    {
        "role": "system",
        "content": "You are a JSON API. Respond with a single JSON object and nothing else.",
    },
    {"role": "user", "content": 'Return {"ok": true} exactly.'},
]

#: A tier slower than this cannot serve an interactive turn. Not a failure --
#: reported separately, because the remedy is different: a dead id needs a new
#: id, a slow tier needs reordering.
SLOW_MS = 3000.0

#: Roughly the size of a real extraction prompt: one resume plus a job
#: description. Measured at ~12k tokens against the live API.
#:
#: THIS EXISTS BECAUSE THE TOY PROBE CERTIFIED A TIER THAT COULD NOT WORK.
#: The first version of this script asked every provider to "Say OK" and
#: reported Groq healthy at 580ms. It was healthy, for two-token requests. In
#: production the same tier answered HTTP 413 to every real call: "Request too
#: large ... on tokens per minute (TPM): Limit 8000, Requested 12268". Every
#: model on that account carries the same ceiling, so no model choice fixes it
#: and only a payload of realistic SIZE can reveal it.
#:
#: A liveness probe that passes on input unlike the input the product sends is
#: not a liveness probe. It is a green tick.
_REALISTIC_TOKENS = 12000
_REALISTIC_PROMPT = (
    "Senior Python engineer with Kafka, Postgres and Airflow experience. "
    * 900
)


async def _probe_one(
    key: llm_router._RouterKey, json_mode: bool, realistic: bool = False
) -> tuple[bool, float, str]:
    caller = llm_router._PROVIDER_CALLERS[key.provider]
    if realistic:
        messages = [
            {"role": "system", "content": "You are a JSON API. Extract skills."},
            {"role": "user", "content": _REALISTIC_PROMPT},
        ]
    else:
        messages = _JSON_MESSAGES if json_mode else _TEXT_MESSAGES
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            result = await caller(client, key, messages, json_mode, 128, 0.0)
    except httpx.HTTPStatusError as exc:
        elapsed = (time.monotonic() - started) * 1000
        body = ""
        try:
            body = exc.response.text[:160].replace("\n", " ")
        except Exception:  # noqa: BLE001 -- diagnostics only
            pass
        return False, elapsed, f"HTTP {exc.response.status_code} {body}"
    except Exception as exc:  # noqa: BLE001 -- every failure is a report line
        elapsed = (time.monotonic() - started) * 1000
        return False, elapsed, f"{type(exc).__name__}: {exc}"
    elapsed = (time.monotonic() - started) * 1000
    text = llm_router.as_provider_result(result).content
    return True, elapsed, (text or "").strip()[:60]


async def probe(providers: list[str] | None = None) -> dict[str, Any]:
    keys = await llm_router._load_keys(None)
    wanted = set(providers or PROVIDER_MODELS)
    report: dict[str, Any] = {}

    for provider in sorted(wanted):
        model = PROVIDER_MODELS.get(provider, "<unconfigured>")
        candidates = [k for k in keys if k.provider == provider]
        if not candidates:
            report[provider] = {
                "model": model,
                "status": "no_key",
                "detail": "no populated key slot, tier is not in use",
            }
            continue
        key = candidates[0]
        text_ok, text_ms, text_detail = await _probe_one(key, json_mode=False)
        json_ok, json_ms, json_detail = await _probe_one(key, json_mode=True)
        size_ok, size_ms, size_detail = await _probe_one(key, json_mode=True, realistic=True)
        slowest = max(text_ms, json_ms)
        report[provider] = {
            "model": model,
            "extra_params": extra_params_for(provider),
            # A tier that cannot carry a real prompt is NOT ok, however fast it
            # answers a toy one. `size` is weighted equally with the other two
            # for exactly that reason.
            "status": "ok" if (text_ok and json_ok and size_ok) else "failed",
            "text": {"ok": text_ok, "latency_ms": round(text_ms), "detail": text_detail},
            "json": {"ok": json_ok, "latency_ms": round(json_ms), "detail": json_detail},
            "size": {
                "ok": size_ok,
                "latency_ms": round(size_ms),
                "detail": size_detail,
                "approx_tokens": _REALISTIC_TOKENS,
            },
            "slow": slowest > SLOW_MS,
        }
    return report


def _render(report: dict[str, Any]) -> bool:
    ok = True
    for provider, row in report.items():
        if row["status"] == "no_key":
            print(f"  {provider:<12} {row['model']:<34} SKIPPED  {row['detail']}")
            continue
        if row["status"] != "ok":
            ok = False
        flag = "OK     " if row["status"] == "ok" else "FAILED "
        print(f"  {provider:<12} {row['model']:<34} {flag}")
        for mode in ("text", "json", "size"):
            entry = row[mode]
            mark = "ok" if entry["ok"] else "FAIL"
            print(
                f"      {mode:<5} {mark:<5} {entry['latency_ms']:>6} ms  "
                f"{entry['detail']}"
            )
        if row["slow"]:
            print(
                f"      SLOW: over {SLOW_MS:.0f} ms. Usable for background work, "
                "too slow to lead an interactive route."
            )
    return ok



async def main() -> int:
    providers = sys.argv[1:] or None
    print("Probing configured LLM model ids...")
    report = await probe(providers)
    ok = _render(report)
    if not ok:
        # The remedy differs completely by WHICH probe failed, so the summary
        # says which rather than offering one guess. Sending an operator to
        # re-verify a model id when the real problem is an exhausted quota costs
        # them the hour it takes to rule the id out, and the two failures look
        # identical from the product's side: AI output degrades and nothing
        # raises.
        size_only = [
            name
            for name, row in report.items()
            if row.get("status") == "failed"
            and row.get("text", {}).get("ok")
            and not row.get("size", {}).get("ok")
        ]
        dead_id = [
            name
            for name, row in report.items()
            if row.get("status") == "failed" and not row.get("text", {}).get("ok")
        ]
        if size_only:
            print(
                "\nTIER TOO SMALL, and this is not a broken model id: "
                + ", ".join(size_only)
                + ". These answer a small prompt and reject a realistic one, so "
                "every toy health check passes while real work fails. The "
                "ceiling is on the ACCOUNT, so no model id fixes it: the "
                "account needs credit or a higher tier."
            )
        if dead_id:
            print(
                "\nFAILING AT EVERY PROMPT SIZE: "
                + ", ".join(dead_id)
                + ". Re-check config/llm_providers.PROVIDER_MODELS against the "
                "provider's live model list before suspecting the keys."
            )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
