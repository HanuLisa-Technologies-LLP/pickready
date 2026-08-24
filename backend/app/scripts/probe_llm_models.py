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

from app.config.llm_providers import (
    PROVIDER_MODELS,
    WORKLOAD_PROFILES,
    WorkloadClass,
    extra_params_for,
)
from app.services import llm_capacity, llm_router

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
_REALISTIC_TOKENS = int(
    WORKLOAD_PROFILES[WorkloadClass.RESUME_EXTRACTION]["approx_input_tokens"]
)
#: Sized FROM the workload profile rather than beside it. A probe whose payload
#: drifted away from the number the router scores against would certify a route
#: for a size the router never sends, which is the same green tick in a new
#: costume. Roughly four characters per token, matching
#: `llm_capacity.estimate_tokens`.
_REALISTIC_PROMPT = "Senior Python engineer with Kafka, Postgres and Airflow. " * (
    (_REALISTIC_TOKENS * 4) // 56
)

#: Which workload class each of the three modes stands for. This is the join
#: between what the probe MEASURES and what the router SCORES: `observe_probe`
#: files the results under these classes, and `score_route` reads the resulting
#: classification. Without the mapping the probe would produce a report a human
#: reads and the router would learn nothing.
_MODE_WORKLOADS = {
    "text": WorkloadClass.SMALL,
    "json": WorkloadClass.STRUCTURED_JSON,
    "size": WorkloadClass.RESUME_EXTRACTION,
}


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
    requested = llm_capacity.estimate_tokens(messages)
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            result = await caller(client, key, messages, json_mode, 128, 0.0)
    except Exception as exc:  # noqa: BLE001 -- every failure is a report line
        elapsed = (time.monotonic() - started) * 1000
        # THE PROBE IS WHERE DOMAIN MEMBERSHIP IS LEARNED, and it learns it from
        # the failure rather than from the report. A 413 body names the
        # organisation and the pool's real ceiling, and a 402 body names the
        # account; `observe_failure` parses both and files them against the
        # quota domain. So an hourly probe in the worker leaves that worker's
        # registry knowing which credentials share a pool and how big it is,
        # before any candidate's request has to find out the expensive way.
        llm_capacity.observe_failure(
            key.provider,
            key.fingerprint,
            exc,
            latency_ms=elapsed,
            task_type="probe",
            requested_tokens=requested,
            model=PROVIDER_MODELS.get(key.provider, ""),
        )
        if isinstance(exc, httpx.HTTPStatusError):
            body = ""
            try:
                body = exc.response.text[:160].replace("\n", " ")
            except Exception:  # noqa: BLE001 -- diagnostics only
                pass
            return False, elapsed, f"HTTP {exc.response.status_code} {body}"
        return False, elapsed, f"{type(exc).__name__}: {exc}"
    elapsed = (time.monotonic() - started) * 1000
    payload = llm_router.as_provider_result(result)
    llm_capacity.observe_success(
        key.provider,
        key.fingerprint,
        latency_ms=elapsed,
        task_type="probe",
        prompt_tokens=payload.prompt_tokens or requested,
        completion_tokens=payload.completion_tokens,
        model=PROVIDER_MODELS.get(key.provider, ""),
    )
    return True, elapsed, (payload.content or "").strip()[:60]


async def _openrouter_key_facts(key: llm_router._RouterKey) -> dict[str, Any]:
    """Ask OpenRouter what it thinks this credential is. Never raises.

    THIS CALL IS WHY THE ROUTER DOES NOT BELIEVE IT HAS THREE OPENROUTER POOLS.
    Measured 2026-08-24, all three credentials report three different `usage`
    figures -- which is exactly what three independent pools would look like --
    and the SAME `creator_user_id`, while the 402 that refuses a large prompt
    names `openrouter_credits` as the limit source. One wallet, three
    itemised statements. The account id goes to `observe_organisation`, which is
    the signal that decides membership; the usage figure goes to
    `observe_usage_reading`, which records it and deliberately does not route on
    it.
    """
    facts: dict[str, Any] = {}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                "https://openrouter.ai/api/v1/key",
                headers={"Authorization": f"Bearer {key.api_key}"},
            )
        response.raise_for_status()
        data = (response.json() or {}).get("data") or {}
    except Exception as exc:  # noqa: BLE001 -- a fact we cannot read is not fatal
        return {"error": type(exc).__name__}
    account = data.get("creator_user_id")
    if account:
        llm_capacity.observe_organisation(key.provider, key.fingerprint, str(account))
        facts["account"] = str(account)
    if data.get("usage") is not None:
        llm_capacity.observe_usage_reading(
            key.provider, key.fingerprint, str(data.get("usage"))
        )
        facts["usage"] = data.get("usage")
    facts["free_tier"] = bool(data.get("is_free_tier"))
    return facts


async def _probe_key(key: llm_router._RouterKey) -> dict[str, Any]:
    """All three modes against ONE credential, recorded in the registry."""
    modes: dict[str, Any] = {}
    for mode in ("text", "json", "size"):
        ok, ms, detail = await _probe_one(
            key, json_mode=(mode != "text"), realistic=(mode == "size")
        )
        modes[mode] = {"ok": ok, "latency_ms": round(ms), "detail": detail}
    classification = llm_capacity.observe_probe(
        key.provider,
        key.fingerprint,
        PROVIDER_MODELS.get(key.provider, ""),
        {_MODE_WORKLOADS[mode]: modes[mode]["ok"] for mode in modes},
    )
    row = {
        "fingerprint": key.fingerprint,
        "classification": classification.value,
        **modes,
    }
    if key.provider == "openrouter":
        row["account"] = await _openrouter_key_facts(key)
    return row


async def probe(providers: list[str] | None = None) -> dict[str, Any]:
    """Probe EVERY populated credential, not one per provider.

    One key per provider was enough to answer "is the model id still there" and
    is not enough to answer the question that actually decides routing: do these
    credentials share a quota pool. That is learned by watching what each one
    says when it refuses, and a credential that is never called says nothing.
    Measured 2026-08-24, all three Groq keys named the same organisation and the
    same 8000-token ceiling, so the router now knows they are ONE pool instead
    of assuming they are three.

    The per-provider keys of the returned report are unchanged, because
    `workers.tasks.probe_llm_models` reads them; the per-key detail is added
    alongside under `keys`.
    """
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
        rows = [await _probe_key(key) for key in candidates]
        # A tier is OK when at least one of its credentials can do the job.
        # ANY rather than ALL, because these are alternatives to each other: one
        # revoked key out of three is a key to replace, not a dead tier, and
        # reporting it as a dead tier sends an operator to re-verify a model id
        # that was never the problem.
        def _any(mode: str) -> bool:
            return any(row[mode]["ok"] for row in rows)

        def _best(mode: str) -> dict[str, Any]:
            passing = [row for row in rows if row[mode]["ok"]] or rows
            return min(passing, key=lambda row: row[mode]["latency_ms"])[mode]

        text, json_mode_row, size = _best("text"), _best("json"), _best("size")
        report[provider] = {
            "model": model,
            "extra_params": extra_params_for(provider),
            # A tier that cannot carry a real prompt is NOT ok, however fast it
            # answers a toy one. `size` is weighted equally with the other two
            # for exactly that reason.
            "status": "ok" if (_any("text") and _any("json") and _any("size")) else "failed",
            "text": text,
            "json": json_mode_row,
            "size": {**size, "approx_tokens": _REALISTIC_TOKENS},
            "slow": max(text["latency_ms"], json_mode_row["latency_ms"]) > SLOW_MS,
            "keys": rows,
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
        for key_row in row.get("keys", []):
            modes = " ".join(
                f"{mode}={'ok' if key_row[mode]['ok'] else 'FAIL'}"
                for mode in ("text", "json", "size")
            )
            account = key_row.get("account") or {}
            suffix = (
                f"  account={account.get('account')} usage={account.get('usage')}"
                if account
                else ""
            )
            print(
                f"      key {key_row['fingerprint']:<18} "
                f"{key_row['classification']:<8} {modes}{suffix}"
            )
    return ok


def _render_capacity() -> None:
    """The capacity table AS MEASURED, keyed by quota domain.

    This is the table the router actually routes on, printed from the same
    registry the router reads, so an operator is never comparing a report
    against a different source of truth than the scheduler. Identifiers, counts
    and timings; no key material and no prompt text.
    """
    snapshot = llm_capacity.snapshot()
    print("\nQuota domains, as MEASURED (a domain is a pool, not a credential):")
    for domain_id, domain in snapshot["domains"].items():
        ceiling = domain["effective_request_ceiling"]
        print(
            f"  {domain_id:<44} {domain['state']:<21} "
            f"members={domain['member_count']} "
            f"ceiling={'unmeasured' if ceiling is None else ceiling} "
            f"throttles={domain['throttle_events']}"
        )
    print("\nRoutes:")
    header = (
        f"  {'fingerprint':<18} {'provider':<11} {'domain':<44} {'status':<9} "
        f"{'class':<8} {'realistic':<10} {'latency':>9} {'fail rate':>10}"
    )
    print(header)
    for fingerprint, entry in snapshot["routes"].items():
        rate = entry["success_rate"]
        failure_rate = "n/a" if rate is None else f"{1.0 - rate:.0%}"
        latency = entry["mean_latency_ms"]
        realistic = entry["probe_results"].get(
            WorkloadClass.RESUME_EXTRACTION.value
        )
        # "fail", not "refused": this column says whether the route CARRIED a
        # realistic payload, and it does not claim to know why it did not. A
        # size ceiling and an exhausted per-minute quota both land here and want
        # completely different remedies, so the reason is read off the domain
        # ceiling and the throttle count rather than guessed at in one word.
        verdict = "n/a" if realistic is None else ("pass" if realistic else "fail")
        print(
            f"  {fingerprint:<18} {entry['provider']:<11} {entry['domain']:<44} "
            f"{entry['status']:<9} {entry['classification']:<8} "
            f"{verdict:<10} "
            f"{('n/a' if latency is None else f'{latency:.0f} ms'):>9} "
            f"{failure_rate:>10}"
        )
        if entry["disabled_reason"]:
            print(f"      excluded: {entry['disabled_reason']}")



async def main() -> int:
    providers = sys.argv[1:] or None
    print("Probing configured LLM model ids...")
    report = await probe(providers)
    ok = _render(report)
    _render_capacity()
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
