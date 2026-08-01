"""Provider roster + per-task routing table for the LangGraph LLM router.

This module is DATA ONLY — no I/O, no state, no side effects — so the routing
policy can be unit-tested and reviewed without standing up the router.

THE 21-KEY ROSTER
-----------------
Seven key slots per provider (Groq / Gemini / OpenRouter) = 21 slots. Slots 1-3
are the original ESD §8.4 trio; 4-7 are the 2026-07-27 expansion. Every slot is
OPTIONAL: `env_key_slots()` yields only the ones actually populated, so a dev
box with three keys behaves exactly as it did before. The `llm_provider_keys`
table still takes precedence over env when it has rows (see llm_router).

WHY TASK-TYPE ROUTING
---------------------
Different work wants different models, and spreading tasks across providers is
what stops one provider's rate limit from stalling every concurrent assessment:

  jd_generation         -> OpenRouter first: long-form reasoning and structure.
  technical_questions   -> Gemini first: better at grounded, specific probes.
  behavioral_assessment -> Gemini first: nuance in open-ended answers.
  report_synthesis      -> OpenRouter first: comprehensiveness across sections.
  email_composition     -> Groq first: latency matters, prose is short.
  rerank                -> Groq first (legacy hint, unchanged — latency-bound).
  extraction            -> Gemini first (legacy hint, unchanged — long context).

`rerank` and `extraction` are retained verbatim so every existing caller keeps
its established behaviour; the five task types above are additive.
"""
from __future__ import annotations

from typing import Iterator, Literal

from app.core.config import get_settings

#: Providers, in a stable order. Adding one here is the only place a new
#: provider's name needs to be registered.
PROVIDERS: tuple[str, ...] = ("groq", "gemini", "openrouter")

#: Key slots per provider. 3 x 7 = the 21-key roster.
KEY_SLOTS_PER_PROVIDER = 7

#: Default model per provider. These are read by the router's HTTP callers.
PROVIDER_MODELS: dict[str, str] = {
    "groq": "llama-3.3-70b-versatile",
    "gemini": "gemini-2.0-flash",
    # NOT the ":free" slug — OpenRouter retired it and now hard-404s every
    # request, which silently killed the whole third fallback tier.
    "openrouter": "meta-llama/llama-3.3-70b-instruct",
}

TaskType = Literal[
    "jd_generation",
    "technical_questions",
    "behavioral_assessment",
    "report_synthesis",
    "email_composition",
    "rerank",
    "extraction",
]

#: Provider preference order per task type. The router walks these in order,
#: round-robining across the healthy keys *within* each provider tier.
TASK_ROUTES: dict[str, list[str]] = {
    "jd_generation": ["openrouter", "gemini", "groq"],
    "technical_questions": ["gemini", "openrouter", "groq"],
    "behavioral_assessment": ["gemini", "groq", "openrouter"],
    "report_synthesis": ["openrouter", "gemini", "groq"],
    "email_composition": ["groq", "gemini", "openrouter"],
    # ── Legacy role hints (ESD §8.4) — behaviour deliberately unchanged ──
    "rerank": ["groq", "gemini", "openrouter"],
    "extraction": ["gemini", "openrouter", "groq"],
}

#: Per-ATTEMPT request timeout (seconds), and below it the TOTAL wall-clock
#: budget for the whole fallback chain.
#:
#: The latency brief asks for a flat 10-15s cap on every LLM call. That is
#: right for the calls a person is waiting on and wrong for the ones they are
#: not: a Functional Skills Report synthesises six sections in one response and
#: simply cannot finish in 15 seconds, so a flat cap there would not make the
#: product faster — it would make every report fail and then be retried, which
#: is slower AND produces nothing. The split below keeps the brief's intent
#: (never let a human wait on a slow provider) while staying buildable:
#:
#:   INTERACTIVE — a request handler is blocked on this. Capped at 15s per
#:   attempt, 30s total, so a dead provider costs one visible pause and the
#:   fallback takes over rather than the user watching a spinner.
#:
#:   BACKGROUND — a Celery task. Nobody is watching, and a truncated report is
#:   worse than a slow one. Generous per attempt, still bounded in total so a
#:   task cannot occupy a worker indefinitely.
TASK_TIMEOUTS: dict[str, float] = {
    # Interactive: the recruiter is watching the Generate JD button.
    "jd_generation": 15.0,
    # Interactive: the compose modal is open and blocked.
    "email_composition": 15.0,
    # Interactive by design — reranking exists to be fast.
    "rerank": 15.0,
    # Background (Celery).
    "technical_questions": 60.0,
    "behavioral_assessment": 45.0,
    "report_synthesis": 90.0,
    "extraction": 45.0,
}

#: Total wall-clock budget for one logical call, across every fallback attempt.
#: Without this, "15s per attempt" times a 4-key retry budget is a 60s request —
#: the per-attempt cap alone does not bound what the user experiences.
TASK_TOTAL_BUDGET: dict[str, float] = {
    "jd_generation": 30.0,
    "email_composition": 30.0,
    "rerank": 30.0,
    "technical_questions": 150.0,
    "behavioral_assessment": 120.0,
    "report_synthesis": 210.0,
    "extraction": 120.0,
}

#: Per-call output ceiling, in tokens.
#:
#: This is NOT a nicety. OpenRouter bills against a prepaid balance and, when a
#: request omits `max_tokens`, it prices the request at the MODEL's maximum
#: (65536 for llama-3.3-70b) and rejects it outright:
#:   402 "This request requires more credits, or fewer max_tokens. You
#:        requested up to 65536 tokens, but can only afford 3944."
#: Every OpenRouter call in production was failing this way, which took out the
#: first-choice tier for jd_generation and report_synthesis. Sending an explicit,
#: task-sized ceiling is also simply correct: it bounds cost and latency on
#: every provider, not just the one that complained.
TASK_MAX_TOKENS: dict[str, int] = {
    # One JD document, seven sections.
    "jd_generation": 4096,
    # A short email body.
    "email_composition": 1024,
    # An ordering plus brief justifications.
    "rerank": 2048,
    # A full question bank (up to 25 items with rubrics).
    "technical_questions": 8192,
    "behavioral_assessment": 4096,
    # Six report sections in one response — the largest thing we ask for.
    "report_synthesis": 8192,
    "extraction": 8192,
}

DEFAULT_MAX_TOKENS = 4096


def max_tokens_for(task_type: str) -> int:
    return TASK_MAX_TOKENS.get(task_type, DEFAULT_MAX_TOKENS)


#: How many keys the router is willing to burn on one logical call before it
#: gives up and lets the caller's own fallback take over. Bounded so a task
#: cannot spend minutes walking 21 dead keys.
TASK_RETRY_BUDGET: dict[str, int] = {
    "jd_generation": 4,
    "technical_questions": 4,
    "behavioral_assessment": 4,
    "report_synthesis": 5,
    "email_composition": 3,
    "rerank": 6,
    "extraction": 6,
}

DEFAULT_TIMEOUT = 45.0
DEFAULT_TOTAL_BUDGET = 120.0
DEFAULT_RETRY_BUDGET = 4


def is_known_task(task_type: str) -> bool:
    return task_type in TASK_ROUTES


def provider_order(task_type: str) -> list[str]:
    """Preferred provider order for `task_type`. Raises for an unknown type —
    a typo must fail loudly rather than silently pick an arbitrary chain."""
    try:
        return list(TASK_ROUTES[task_type])
    except KeyError as exc:
        raise ValueError(
            f"Unknown LLM task_type {task_type!r}; expected one of "
            f"{sorted(TASK_ROUTES)}"
        ) from exc


def timeout_for(task_type: str) -> float:
    return TASK_TIMEOUTS.get(task_type, DEFAULT_TIMEOUT)


def total_budget_for(task_type: str) -> float:
    """Wall-clock ceiling for the entire fallback chain of one logical call."""
    return TASK_TOTAL_BUDGET.get(task_type, DEFAULT_TOTAL_BUDGET)


def retry_budget_for(task_type: str) -> int:
    return TASK_RETRY_BUDGET.get(task_type, DEFAULT_RETRY_BUDGET)


def env_key_slots() -> Iterator[tuple[str, int, str]]:
    """Yield (provider, slot_number, api_key) for every POPULATED env slot.

    Empty slots are skipped entirely, which is what makes the roster elastic:
    the same code runs with 3 keys or 21.
    """
    settings = get_settings()
    for provider in PROVIDERS:
        for slot in range(1, KEY_SLOTS_PER_PROVIDER + 1):
            value = getattr(settings, f"{provider}_api_key_{slot}", "")
            if value:
                yield provider, slot, value


def configured_key_count() -> dict[str, int]:
    """{provider: populated slot count} — used by the health endpoint and by
    `scripts/validate_stack.py` to report the roster without leaking keys."""
    counts = {provider: 0 for provider in PROVIDERS}
    for provider, _slot, _value in env_key_slots():
        counts[provider] += 1
    return counts
