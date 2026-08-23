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

WHAT IT CHECKS, AND WHY BOTH MODES
----------------------------------
Two calls per provider: plain text and JSON mode. They are genuinely different
questions. Groq's gpt-oss family answers plain text happily and, without
`reasoning_effort`, INTERMITTENTLY fails `response_format=json_object` with
`json_validate_failed` -- so a probe that only asked for prose would report a
healthy tier that drops a fraction of every structured call.

It also reports LATENCY, because a tier that works but takes eleven seconds
cannot serve an interactive deadline of twenty-six, and that is indistinguishable
from an outage to the person waiting.

USAGE
-----
    python -m app.scripts.probe_llm_models            # every provider
    python -m app.scripts.probe_llm_models groq       # one provider

Exit code is 1 if any configured provider fails either mode, so this can gate a
deploy or run on a schedule.
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


async def _probe_one(
    key: llm_router._RouterKey, json_mode: bool
) -> tuple[bool, float, str]:
    caller = llm_router._PROVIDER_CALLERS[key.provider]
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
        slowest = max(text_ms, json_ms)
        report[provider] = {
            "model": model,
            "extra_params": extra_params_for(provider),
            "status": "ok" if (text_ok and json_ok) else "failed",
            "text": {"ok": text_ok, "latency_ms": round(text_ms), "detail": text_detail},
            "json": {"ok": json_ok, "latency_ms": round(json_ms), "detail": json_detail},
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
        for mode in ("text", "json"):
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
        print(
            "\nAt least one provider rejected the model id we send it. "
            "Re-check config/llm_providers.PROVIDER_MODELS against the "
            "provider's live model list before suspecting the keys."
        )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
