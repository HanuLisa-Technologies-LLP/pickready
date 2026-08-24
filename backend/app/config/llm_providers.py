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

from enum import Enum
from typing import Iterator, Literal

from app.core.config import get_settings

#: Providers, in a stable order. Adding one here is the only place a new
#: provider's name needs to be registered.
PROVIDERS: tuple[str, ...] = ("groq", "gemini", "openrouter")

#: Key slots per provider. 3 x 7 = the 21-key roster.
KEY_SLOTS_PER_PROVIDER = 7

#: Default model per provider. These are read by the router's HTTP callers.
#:
#: A RETIRED MODEL ID IS THE SINGLE MOST COMMON WAY A WHOLE TIER GOES DARK, and
#: it has now happened twice. It does not look like a configuration error from
#: the inside: the router simply records a failure per key and falls through to
#: the caller's deterministic template, so the product reports "AI unavailable"
#: while every credential is perfectly valid. Re-probe these ids whenever AI
#: output degrades before suspecting the keys.
PROVIDER_MODELS: dict[str, str] = {
    # THIRD RETIREMENT, measured 2026-08-23. `llama-3.3-70b-versatile` answered
    # every Groq key with HTTP 404 -- not a rate limit, not a credential
    # problem, the id simply no longer exists on the account's model list. With
    # OpenRouter simultaneously out of prepaid credit (HTTP 402), TWO of the
    # three tiers were dark and every task fell through to Gemini, whose
    # free-tier flash latency was measured at 4.4s-11.4s for a ONE TOKEN reply.
    # That is the whole of "the AI runs for a long time and then does not
    # work": an interactive deadline of 26s cannot survive two dead tiers plus
    # an 11-second provider, so the loop degraded and the product reported an
    # AI failure while every credential was valid.
    #
    # `openai/gpt-oss-120b` is the replacement, verified on the live account:
    # 6/6 successes in JSON mode at 449-597ms, roughly twenty times faster than
    # the Gemini fallback it was standing behind.
    "groq": "openai/gpt-oss-120b",
    # A ROLLING ALIAS, deliberately, and the exception to pinning below.
    # `gemini-2.0-flash` was pinned here and, on 2026-08-01, every Gemini key
    # answered it with HTTP 429 and the tell-tale body
    #   "Quota exceeded for metric: ...generate_content_free_tier_requests,
    #    limit: 0, model: gemini-2.0-flash"
    # `limit: 0` is not a rate limit that clears on retry — Google had withdrawn
    # the model from the free tier outright, so the quota ceiling is zero
    # forever and no amount of backoff or key rotation reaches it. The very same
    # keys returned HTTP 200 against `gemini-flash-latest` in the same second.
    # The alias is Google's supported remedy for exactly this: it always
    # resolves to a current free-tier-eligible flash model (gemini-3.6-flash at
    # the time of writing), so a future retirement cannot silently remove the
    # entire Gemini tier again. Verified with JSON mode on all three keys.
    "gemini": "gemini-flash-latest",
    # NOT the ":free" slug — OpenRouter retired it and now hard-404s every
    # request, which silently killed the whole third fallback tier.
    "openrouter": "meta-llama/llama-3.3-70b-instruct",
}

#: Extra request parameters a provider needs that the OpenAI shape does not
#: carry. Data here rather than a literal in the caller, for the same reason
#: temperature is: a value hardcoded inside one `_call_*` cannot be read by the
#: liveness probe, so the probe would exercise a request the router never sends.
#:
#: `reasoning_effort` is REQUIRED for the gpt-oss family in JSON mode, and this
#: is not a tuning preference. Left unset, the model spends its budget in the
#: reasoning channel and Groq answers `json_validate_failed` on prompts as
#: trivial as "return {"ok": true}" -- measured, and it is intermittent, which
#: is the worst version of the bug: the tier looks healthy and drops a fraction
#: of structured calls. At "low" the same prompts returned 6/6 valid JSON, and
#: the request also got nine times faster (4983ms -> 544ms). Only "low",
#: "medium" and "high" are accepted; "none" is rejected with HTTP 400.
PROVIDER_EXTRA_PARAMS: dict[str, dict[str, object]] = {
    "groq": {"reasoning_effort": "low"},
}


def extra_params_for(provider: str) -> dict[str, object]:
    return dict(PROVIDER_EXTRA_PARAMS.get(provider, {}))


TaskType = Literal[
    "conversation_turn",
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
#:
#: THE ORDER IS MEASURED, NOT PREFERRED, and `app/scripts/probe_llm_models.py`
#: is how it is re-measured. Re-run it before changing anything here; a chain
#: whose first tier is dead or slow is invisible from the inside, because the
#: router correctly walks past it and the only symptom is latency.
#:
#: Measurement of 2026-08-23, which is why five of these entries moved:
#:   groq        573 ms text / 582 ms JSON      healthy
#:   openrouter  915 ms text / 3967 ms JSON     nearly out of prepaid credit,
#:                                              402s anything but a small
#:                                              max_tokens
#:   gemini      HTTP 503 "high demand", and    degraded
#:               12.9-22.7 s when it did answer
#:
#: Gemini led `technical_questions`, `behavioral_assessment` and `extraction`,
#: and OpenRouter led `jd_generation` and `report_synthesis`. Every one of those
#: tasks therefore opened with a tier that was either failing or ten to forty
#: times slower than the tier sitting behind it. Groq now leads everything a
#: person waits on; the other two remain as fallbacks, which is the job they can
#: still do.
TASK_ROUTES: dict[str, list[str]] = {
    "jd_generation": ["groq", "openrouter", "gemini"],
    "technical_questions": ["groq", "gemini", "openrouter"],
    "behavioral_assessment": ["groq", "gemini", "openrouter"],
    # Background, and the one place a slower-but-larger tier is still worth
    # having second: a report is synthesised in one long response.
    "report_synthesis": ["groq", "gemini", "openrouter"],
    "email_composition": ["groq", "gemini", "openrouter"],
    # The unified candidate conversation: the follow-up decision, the next
    # question, and the challenge to a non-answer.
    #
    # THIS ENTRY WAS MISSING FROM 2026-08-04 TO 2026-08-05, and its absence is
    # why the interview stayed a script. `conversation_turn` was added to
    # TASK_TEMPERATURE when the adaptive turn was built, and to nothing else.
    # `provider_order` raises ValueError on an unknown task type ON PURPOSE, so
    # a typo fails loudly rather than silently picking an arbitrary chain --
    # but every caller in the conversation path catches broadly and degrades to
    # the scripted question, because that is the right answer to a provider
    # outage with a candidate mid-assessment. So the guard that protects
    # candidates from an outage perfectly concealed a config typo: 100% of
    # conversational calls raised, 100% degraded, and the product looked
    # exactly as unadaptive as it had before any of the work.
    #
    # Groq first because it is the fastest of the three and a candidate is
    # watching a text box. Gemini LAST despite being the usual first choice:
    # measured on production 2026-08-05 its free-tier keys answer 429 quota
    # exceeded, while Groq and OpenRouter both answer 200.
    "conversation_turn": ["groq", "openrouter", "gemini"],
    # ── Legacy role hints (ESD §8.4) — behaviour deliberately unchanged ──
    "rerank": ["groq", "gemini", "openrouter"],
    "extraction": ["groq", "gemini", "openrouter"],
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
    # THE most interactive call in the product: a candidate is sitting in front
    # of a text box waiting for the next question, and there can be two of these
    # in one turn (classify, then write). Tighter than the others for that
    # reason: a slow provider here is felt twice per question.
    "conversation_turn": 12.0,
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
    # 24s: two attempts at the 12s cap. Deliberately short, because this budget
    # is spent while a candidate watches a text box, and the degraded path here
    # is the scripted question rather than a failure.
    "conversation_turn": 24.0,
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

#: Floor for the router's ADAPTIVE max_tokens retry.
#:
#: Some providers refuse a request not because the account is dead but because
#: the ceiling we asked for is larger than what the remaining balance or quota
#: covers, and they say so precisely:
#:   OpenRouter 402 "You requested up to 4096 tokens, but can only afford 2674."
#:   Groq 429     "Limit 100000, Used 99729, Requested 4133."
#: Both are satisfiable at a smaller ceiling, and both were being thrown away.
#: The router re-asks the SAME key with the provider's own stated ceiling rather
#: than condemning the provider (see llm_router.affordable_max_tokens).
#:
#: The floor is what stops that from turning into a pointless extra attempt: an
#: account with 270 tokens of daily quota left cannot produce a usable JD or
#: report, so below this we accept the failure and move to the next provider,
#: which is the genuinely useful thing to try.
MIN_USEFUL_MAX_TOKENS = 512

#: A reduced ceiling is only accepted if it is at least this FRACTION of what
#: the task asked for.
#:
#: Without it the adaptive retry quietly trades correctness for availability. A
#: nearly empty OpenRouter balance offered 2600 tokens against report_synthesis'
#: 8192, and taking that deal would put a TRUNCATED Functional Skills Report in
#: front of a recruiter — precisely what this repo's standing rule forbids
#: ("a truncated report is worse than a slow one"; remarks are regenerated, never
#: cut to length). Below the fraction we let the attempt fail and move to the
#: next provider, which very likely can serve the whole thing.
#:
#: The same 2600 IS accepted for jd_generation, whose ask is 4096: a JD at
#: two-thirds length is a complete draft the recruiter edits anyway.
MIN_CEILING_FRACTION = 0.5


def max_tokens_for(task_type: str) -> int:
    return TASK_MAX_TOKENS.get(task_type, DEFAULT_MAX_TOKENS)


#: Sampling temperature per task. Policy, and therefore DATA -- it belongs here
#: beside the routing table rather than inline in the router, for the same
#: reason provider order and retry budget do (claude.md rule: routing policy is
#: data in config/llm_providers.py, never inline in a service). It was
#: previously hardcoded at 0.1 in TWO places inside llm_router, once for the
#: OpenAI-style payload and once for Gemini's generationConfig, so the two could
#: drift apart silently and neither could be varied by task.
#:
#: The split is by whether the task JUDGES or WRITES.
#:
#: A scoring call must return the same grade for the same answer every time it
#: runs. Anything above zero means a candidate's grade depends partly on when
#: they were scored, which is indefensible in a hiring decision and, worse,
#: unfalsifiable: a rescore that disagrees looks like a bug in the rubric rather
#: than sampling noise. These sit at 0.0.
#:
#: A conversational turn is the opposite case. Asking a follow-up at 0.0 makes
#: the interviewer sound like a form, repeating near-identical phrasing to every
#: candidate, which is exactly the "static script" complaint. Phrasing may vary;
#: WHAT is asked is fixed by the framework, not by the sampler.
#:
#: Note what is NOT here: report_synthesis is a scoring-adjacent task that
#: states grades and must not vary, so it is deterministic even though its
#: output is prose.
TASK_TEMPERATURE: dict[str, float] = {
    # ── Deterministic: these judge. ──────────────────────────────────────────
    "behavioral_assessment": 0.0,   # per-answer and per-competency scoring
    "report_synthesis": 0.0,        # states the grades a client reads
    "rerank": 0.0,                  # orders candidates
    "extraction": 0.0,              # pulls structured fields out of a resume
    # ── Generative: these write. ─────────────────────────────────────────────
    # Question banks want variety across a job's items, but not so much that a
    # rubric drifts from its question.
    "technical_questions": 0.4,
    "jd_generation": 0.5,
    "email_composition": 0.5,
    # The unified candidate conversation. The highest in the product, and the
    # only place where sounding different to different people is the point.
    "conversation_turn": 0.7,
}

#: Anything unlisted is treated as a judging task. Defaulting to deterministic
#: is the safe direction: a new task that should have been creative reads a
#: little flat, whereas a new SCORING task silently sampling at 0.5 would make
#: grades non-reproducible and nothing would announce it.
DEFAULT_TEMPERATURE = 0.0


def temperature_for(task_type: str) -> float:
    return TASK_TEMPERATURE.get(task_type, DEFAULT_TEMPERATURE)


#: How many keys the router is willing to burn on one logical call before it
#: gives up and lets the caller's own fallback take over. Bounded so a task
#: cannot spend minutes walking 21 dead keys.
#:
#: Sized so that, with three providers in the chain, a call can lose a whole
#: provider AND still retry a rate-limited key on a sibling. The budgets below
#: were each one attempt short of that: a 402 from the first provider plus a
#: burst 429 from the second used the entire budget and the third provider was
#: never reached, which is how "AI features not working at all" looked in
#: production. The wall-clock ceiling in TASK_TOTAL_BUDGET is what actually
#: bounds a user-facing request; these numbers bound the attempts within it.
TASK_RETRY_BUDGET: dict[str, int] = {
    # Six, raised from three on 2026-08-05 after watching it fail live.
    #
    # Three was "one per provider tier, no sibling-key retries", reasoned from
    # the fallback being acceptable. Measured, that was wrong: a turn makes up
    # to THREE calls (classify, challenge or probe, then write the next
    # question), so a real interview issues them in bursts and Groq answers 429.
    # A 429 is retried with a reduced max_tokens, which consumes an attempt, so
    # a throttled first tier could exhaust the budget before the chain ever
    # reached a HEALTHY OpenRouter. Observed as every challenge in a live
    # transcript arriving canned while an isolated probe composed 5 out of 5.
    #
    # Six is still tightly bounded, and it is NOT the thing protecting the
    # candidate from waiting: TASK_TOTAL_BUDGET does that, in wall clock, at
    # 24s. Attempts are ~300ms when they succeed, so the extra budget costs
    # nothing on the happy path and buys the fallback tier a real chance on the
    # unhappy one.
    "conversation_turn": 6,
    "jd_generation": 5,
    "technical_questions": 6,
    "behavioral_assessment": 5,
    "report_synthesis": 6,
    "email_composition": 4,
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


# ── Token pricing, for cost attribution (2026-08-11) ─────────────────────────
#
# Prices are USD per MILLION tokens, as DATA here rather than inline in the
# router, for the same reason provider order and timeouts are: a commercial
# number changes on someone else's schedule and must be editable without
# touching the retry loop.
#
# THESE ARE ESTIMATES AND ARE LABELLED AS SUCH EVERYWHERE THEY SURFACE.
# The router reports `estimated_cost_usd`, never `cost`. Three reasons a figure
# here cannot be the invoice:
#
#   * two of the three providers are used on free or promotional tiers, where
#     the real marginal cost is zero and the useful number is the one that says
#     what it WOULD cost at list price;
#   * OpenRouter prices per underlying model and routes between them; and
#   * providers round and batch differently from what their usage block says.
#
# What the number IS good for is the comparison the operator actually needs:
# which task_type and which key are consuming the budget. That ordering is
# stable even when the absolute figure is not.
#
# A provider missing from this table costs 0.0 and is reported with
# `priced=False`, so "we have no price for this" never silently reads as
# "this was free".
TOKEN_PRICES_USD_PER_MILLION: dict[str, dict[str, float]] = {
    # Groq, Llama-class models on the free/developer tier.
    "groq": {"prompt": 0.05, "completion": 0.08},
    # Gemini Flash-class.
    "gemini": {"prompt": 0.075, "completion": 0.30},
    # OpenRouter varies by underlying model; this is a mid-range placeholder.
    "openrouter": {"prompt": 0.20, "completion": 0.60},
}


def estimate_cost_usd(provider: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Estimated list-price cost of one call, in USD.

    Returns 0.0 for an unpriced provider. Callers that need to distinguish
    "free" from "unknown" should check `is_priced`.
    """
    prices = TOKEN_PRICES_USD_PER_MILLION.get(provider)
    if not prices:
        return 0.0
    return (
        prompt_tokens * prices.get("prompt", 0.0)
        + completion_tokens * prices.get("completion", 0.0)
    ) / 1_000_000


def is_priced(provider: str) -> bool:
    """True when a price is on file, so a 0.0 can be read correctly."""
    return provider in TOKEN_PRICES_USD_PER_MILLION


# ── Capacity-aware routing policy (2026-08-24) ───────────────────────────────
#
# Everything below is DATA for `services/llm_capacity.py`, and it is here for
# the same reason provider order, timeouts and temperature are: a scheduler with
# its weights inline cannot be reviewed, cannot be diffed, and cannot be argued
# with. `claude.md` rule: routing policy is data in config/llm_providers.py,
# never inline in a service.


class WorkloadClass(str, Enum):
    """The shapes of work the product actually sends.

    These exist because a liveness probe that passes on input unlike the input
    the product sends is not a probe, it is a green tick. Groq answered "Say OK"
    in 580ms while returning HTTP 413 to every real resume extraction, and only
    a payload of realistic SIZE could tell the two apart.
    """

    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    LONG_CONTEXT = "long_context"
    STRUCTURED_JSON = "structured_json"
    RESUME_EXTRACTION = "resume_extraction"


#: What each workload class costs to send, and what shape it is.
#:
#: `approx_input_tokens` is the number that matters: it is compared against a
#: quota domain's PROVEN ceiling, and it is why RESUME_EXTRACTION sits at 12000.
#: A resume plus a job description was measured at roughly that against the live
#: API on 2026-08-23, and the Groq organisation's per-minute pool is 8000, so
#: this class is the one that separates a tier that works from a tier that only
#: looks like it works.
WORKLOAD_PROFILES: dict[WorkloadClass, dict[str, object]] = {
    WorkloadClass.SMALL: {"approx_input_tokens": 40, "json": False},
    WorkloadClass.MEDIUM: {"approx_input_tokens": 1200, "json": False},
    WorkloadClass.LARGE: {"approx_input_tokens": 6000, "json": False},
    WorkloadClass.LONG_CONTEXT: {"approx_input_tokens": 20000, "json": False},
    WorkloadClass.STRUCTURED_JSON: {"approx_input_tokens": 60, "json": True},
    WorkloadClass.RESUME_EXTRACTION: {"approx_input_tokens": 12000, "json": True},
}


#: Which workload class each task type behaves like. This is the "task
#: complexity and required context size" input to the router's score, expressed
#: once as data rather than re-derived per call site.
TASK_WORKLOAD: dict[str, WorkloadClass] = {
    "conversation_turn": WorkloadClass.MEDIUM,
    "jd_generation": WorkloadClass.MEDIUM,
    "technical_questions": WorkloadClass.LARGE,
    "behavioral_assessment": WorkloadClass.LARGE,
    "report_synthesis": WorkloadClass.LONG_CONTEXT,
    "email_composition": WorkloadClass.SMALL,
    "rerank": WorkloadClass.MEDIUM,
    # A resume plus a JD. The one that 413s.
    "extraction": WorkloadClass.RESUME_EXTRACTION,
}

DEFAULT_WORKLOAD = WorkloadClass.MEDIUM


def workload_for_task(task_type: str) -> WorkloadClass:
    return TASK_WORKLOAD.get(task_type, DEFAULT_WORKLOAD)


#: PUBLISHED context windows, in tokens. These are what the vendor SAYS, and the
#: registry treats them as documentation only -- they are reported beside the
#: measured numbers and never gate a routing decision.
#:
#: The distinction is load-bearing. Groq's published window for gpt-oss-120b is
#: 131072 and the account could not accept 12268, because the binding constraint
#: was the organisation's per-minute pool and not the model's context at all.
#: Routing on the published figure is precisely how the 413 storm happened.
DECLARED_CONTEXT_LIMITS: dict[str, int] = {
    "groq": 131072,
    "gemini": 1048576,
    "openrouter": 131072,
}


def declared_context_limit(provider: str) -> int | None:
    return DECLARED_CONTEXT_LIMITS.get(provider)


#: Weights for the router's route score. Positive terms are things we want,
#: negative terms are things we want less of; `llm_capacity.score_route` names
#: which is which. Every term is normalised to 0..1 first, so these numbers are
#: directly comparable to each other.
#:
#: The shape of the table is the policy, and it says three things:
#:
#:   * CAPABILITY AND PREFERENCE DOMINATE. A route proven to carry the workload,
#:     on the provider the measured route order puts first, wins by default.
#:   * COOLDOWN OUTWEIGHS PREFERENCE. A throttled domain must lose to a
#:     less-preferred one that can answer now, or the preference order becomes a
#:     way of insisting on a provider that is currently refusing us.
#:   * COST IS NEARLY IRRELEVANT. It is here because the brief asks for it and
#:     because it breaks ties, but a fraction of a cent must never outrank
#:     availability, and a weight of 5 against a cooldown weight of 70 is that
#:     statement in a form somebody can check.
#:
#: `load_share` is what makes traffic DISTRIBUTE rather than concentrate: a
#: domain that has taken most of the recent traffic scores lower until the
#: others catch up. It is deliberately smaller than `capability`, because
#: spreading load onto a route that cannot do the job is not load balancing.
ROUTE_SCORE_WEIGHTS: dict[str, float] = {
    # Wanted.
    "preference": 40.0,
    "capability": 45.0,
    "capacity_headroom": 30.0,
    "success_rate": 25.0,
    "latency": 20.0,
    # Unwanted.
    "cooldown": 70.0,
    "quota_pressure": 25.0,
    "load_share": 18.0,
    "unverified_domain": 3.0,
    "cost": 5.0,
}

#: Latency normaliser: a route at this mean latency scores 0.5 on the latency
#: term. One second, because that is roughly the line between a provider that
#: can lead an interactive route and one that cannot.
LATENCY_REFERENCE_MS = 1000.0

#: How many recent latency samples a route keeps. Long enough to smooth one
#: unlucky call, short enough that a recovered provider is not held down by an
#: outage it is already out of.
RECENT_LATENCY_SAMPLES = 20

#: How many recent ATTEMPTS the load-share window remembers. This is the whole
#: mechanism behind "distribute across viable routes": share is measured over
#: this window and penalised, so a domain cannot monopolise traffic while
#: siblings sit idle.
LOAD_SHARE_WINDOW = 50

#: How long a quota domain is scored down after a 429.
#:
#: Deliberately short, and deliberately NOT the key breaker's fifteen minutes. A
#: per-minute rate limit clears within a minute, and treating it like an
#: account-level write-off would discard the traffic the domain can still serve.
DOMAIN_COOLDOWN_SECONDS = 60.0

#: How soon after a throttle a sibling's success still counts as evidence of
#: INDEPENDENCE. Outside this window the two events are simply unrelated in
#: time, and reading them as proof would manufacture independence that was never
#: observed -- the exact error that makes a router think it has 3x the
#: throughput it has.
CAPACITY_INDEPENDENCE_WINDOW_S = 60.0
