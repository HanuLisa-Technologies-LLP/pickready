"""Model roster + per-task model policy for the LLM router (spec-doc5 Part B).

This module is DATA ONLY -- no I/O, no state, no side effects -- so the policy
can be unit-tested and reviewed without standing up the router. That property is
older than this rewrite and is the reason the module keeps its name: `claude.md`
rule 9 says routing policy is data in `config/llm_providers.py`, never inline in
a service, and a rename would have moved the rule's address without changing
anything it protects.

WHAT CHANGED, AND WHAT THE OLD VERSION COST
-------------------------------------------
Until 2026-08-28 this file described a 21-key, three-provider roster (Groq /
Gemini / OpenRouter) with a measured provider-preference order per task, a
capacity registry keyed by quota domain, a `route_score` weighting table, and a
GREEN/YELLOW/RED workload classification. All of it is gone, superseded in full
by spec-doc5 Part B.

It is worth writing down what that machinery was actually FOR, because the
reason it can be deleted is not that it was wrong -- it worked, and the comments
it carried record real measured incidents. It existed because three free-tier
accounts share nothing except unreliability: a retired model id took a whole
tier dark twice, a prepaid balance ran out and answered 402, an organisation's
8000-token-per-minute pool answered 413 to every realistic resume extraction,
and a free-tier model was withdrawn outright and answered `limit: 0`. Every one
of those is a failure mode of *not paying a vendor*. The dynamic scheduler was
the cost of routing around them.

One vendor on a real account removes the class of problem, so it removes the
machinery. What survives is the discipline that was always provider-agnostic:
per-task timeouts, a total wall-clock budget separate from the per-attempt
timeout, an explicit output ceiling, per-task temperature, a bounded retry
budget, and a circuit breaker. Those are in this file and in `llm_router`
unchanged in intent.

THE VENDOR CHANGED ON 2026-08-31, AND THE DISCIPLINE DID NOT
-------------------------------------------------------------
This file described Anthropic until 2026-08-31. It now describes OpenAI. That
is a deliberate reversal of a documented rule, made by the product owner, and
it is recorded here rather than left to be discovered from a diff.

What was reversed is the VENDOR. What was NOT reversed is the single-vendor
discipline that made the previous phase worth doing: one vendor, two model ids,
a closed mapping, a grep that fails on a third id. Groq, Gemini, OpenRouter and
a 1371-line capacity registry were deleted to reach that state, and none of it
comes back. There is no fallback chain, no `if provider ==` branch, and no
second transport.

THE THREE ENDPOINTS, AND NOTHING ELSE
--------------------------------------
    reasoning / writing / judgment  -> gpt-5.6-terra
    extraction / classification     -> gpt-5.6-luna
    every embedding                 -> voyage-4

The two-tier split is the point of the mapping and it survived the vendor
change intact: every task that ran on the reasoning tier still runs on the
reasoning tier, and every task that ran on the extraction tier still runs on
the extraction tier. No task moved. `claim_extraction` in particular MUST NOT
EVALUATE, and moving it up a tier would be a boundary violation rather than an
upgrade -- see `MODEL_FOR_TASK` below.

No third model, no second embedding model. Adding one is a later decision and
not one to take on implementation judgment, so `MODEL_FOR_TASK` is a closed
mapping onto exactly two ids and `tests/test_llm_task_routing.py` asserts the
closure and greps the executable source for any other model string.

THE TWO MODEL IDS HAVE NEVER BEEN SENT TO A LIVE ENDPOINT
----------------------------------------------------------
There is no OpenAI key in this phase, so neither `gpt-5.6-terra` nor
`gpt-5.6-luna` has been resolved against the models endpoint. They are the
owner's strings, used verbatim. They are named constants here precisely so a
wrong id is a one-line fix rather than a search, and the first live response on
each path is checked against a hand-authored fixture by
`app/services/reliability/vendor_contract.py`, which raises rather than parsing
a differently shaped body into an empty string. `VERIFICATION_PENDING.md`
carries the row and the command.
"""
from __future__ import annotations

from typing import Literal

# ── The roster ───────────────────────────────────────────────────────────────
#
# Pinned ids, not aliases. The old file kept `gemini-flash-latest` as a rolling
# alias precisely because a free-tier model had been withdrawn underneath a
# pinned id; on a paid account that pressure does not exist, and a pinned id is
# what makes a scoring call reproducible across a deploy. A model that changes
# underneath a grade is the same defect as a temperature above zero: the
# candidate's grade depends on WHEN they were scored.

#: Reasoning, writing and judgment. Every task that requires genuine
#: evaluation, dialogue generation, or evidence-grounded writing.
MODEL_TERRA = "gpt-5.6-terra"

#: Extraction, classification and routing. High-volume, low-ambiguity,
#: mechanical sub-tasks.
MODEL_LUNA = "gpt-5.6-luna"

#: The sole embedding model for every RAG surface in the platform.
#:
#: Pinned to 1024 output dimensions in `services/embeddings.py`. That is not a
#: preference: `profiles.embedding`, `jobs.embedding` and `context_chunks`
#: are `vector(1024)` columns holding vectors written by the BGE-M3 endpoint
#: this replaces, and Voyage's default output width for this family is 1024, so
#: the swap needs no migration. Changing the width later is a re-embed of every
#: row, not a config change, and `EMBEDDING_DIM` is asserted in tests for that
#: reason.
EMBEDDING_MODEL = "voyage-4"

#: Every model id this platform may call. The acceptance criterion is unchanged
#: by the vendor swap: "grep the codebase for any other model string and confirm
#: zero results". `tests/test_llm_task_routing.py` is that grep, executed.
ALLOWED_MODELS: frozenset[str] = frozenset({MODEL_TERRA, MODEL_LUNA})

#: Kept as a single-element tuple rather than deleted. Two callers read it --
#: the admin health endpoint's key roster and `matching`'s reasoning trace --
#: and both are answering "which vendor served this", which is still a real
#: question with a now-boring answer. A one-element tuple keeps those call
#: sites honest instead of having them hardcode the string.
PROVIDERS: tuple[str, ...] = ("openai",)
PROVIDER = "openai"

#: WHICH CREDENTIAL EACH MODEL IS CALLED WITH.
#:
#: Two keys for one vendor is unusual and it is what the owner has, so it is
#: DATA here rather than a branch in the router. The value is the `Settings`
#: attribute, which is populated from the environment variable of the same name
#: uppercased: `OPENAI_GPT_TERRA` and `OPENAI_GPT_LUNA`.
#:
#: `llm_router.key_for_model` is the only reader. An absent key for the model
#: being called raises the same loud `LLMUnavailableError` an absent single key
#: used to raise, naming the variable that is missing -- never a silent switch
#: to the other key, which would send a judging call to the extraction tier and
#: change what a grade was produced by without changing anything visible.
SETTINGS_ATTR_FOR_MODEL: dict[str, str] = {
    MODEL_TERRA: "openai_gpt_terra",
    MODEL_LUNA: "openai_gpt_luna",
}

#: The environment variable behind each of those settings, for error messages
#: and for `configured_key_count`. Derived rather than written twice.
ENV_VAR_FOR_MODEL: dict[str, str] = {
    model: attribute.upper() for model, attribute in SETTINGS_ATTR_FOR_MODEL.items()
}

OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"
VOYAGE_EMBEDDINGS_URL = "https://api.voyageai.com/v1/embeddings"

#: The `response_format` this platform asks for in JSON mode, and the token the
#: published API requires to be present somewhere in the messages before it will
#: accept that format. Both are DATA rather than literals in the router because
#: `vendor_contract.describe_request_hazards` has to check the same two facts
#: the router relies on, and two copies of a published constraint drift.
JSON_OBJECT_RESPONSE_FORMAT: dict[str, str] = {"type": "json_object"}
JSON_MODE_REQUIRED_TOKEN = "json"


# ── Task types ───────────────────────────────────────────────────────────────

TaskType = Literal[
    # ── The candidate-facing conversation ──
    "conversation_turn",
    # ── Job setup ──
    "jd_generation",
    "technical_questions",
    "swot_intake",
    "company_dna_intake",
    "situation_classification",
    "competency_transformation",
    # ── Scoring ──
    "behavioral_assessment",
    "claim_extraction",
    "evidence_tiering",
    "dimension_evaluation",
    "triangulation",
    # ── Output ──
    "report_synthesis",
    "email_composition",
    # ── Legacy role hints (ESD §8.4), retained verbatim so every pre-existing
    #    caller keeps its established behaviour ──
    "rerank",
    "extraction",
]


#: WHICH MODEL EACH TASK RUNS ON (spec-doc5 §B.3).
#:
#: The split is one question: does this task JUDGE or WRITE (Terra), or does it
#: EXTRACT, CLASSIFY or ROUTE (Luna)? Two entries below are worth their own
#: sentence because the obvious answer is the wrong one:
#:
#:   * `claim_extraction` is Luna and MUST NOT EVALUATE. Runbook §57.1 makes
#:     extraction a narrow mechanical step precisely so that a model's opinion
#:     of a claim cannot leak into the pipeline before the dimension evaluators,
#:     which are the only components allowed to hold one. Putting Terra here
#:     would not be an upgrade, it would be a boundary violation.
#:   * `rerank` is Luna because reranking exists to be fast and orders a list
#:     it does not grade. `dimension_evaluation` is Terra because it grades.
#:
#: What is NOT here: the aggregator. spec-doc5 §B.3 assigns it "No model.
#: Deterministic code only", so it has no task type at all, and
#: `tests/test_miti_pipeline.py` asserts the aggregation module imports no
#: router. A task type would be a door into a room that must not have one.
MODEL_FOR_TASK: dict[str, str] = {
    # ── Terra: evaluation, dialogue, evidence-grounded writing ─────────────
    # Vaada. "Human-quality dialogue is a stated product bar" (§B.3).
    "conversation_turn": MODEL_TERRA,
    "jd_generation": MODEL_TERRA,
    "technical_questions": MODEL_TERRA,
    # Bodha, both mandates: structured interview judgment and probe selection.
    "swot_intake": MODEL_TERRA,
    "company_dna_intake": MODEL_TERRA,
    # Sutra: competency naming, observable-evidence authoring, weight
    # derivation. Judgment-heavy.
    "competency_transformation": MODEL_TERRA,
    "behavioral_assessment": MODEL_TERRA,
    # Miti: five isolated rubric-anchored evaluators.
    "dimension_evaluation": MODEL_TERRA,
    # Miti: contradiction reasoning and benign-explanation generation.
    "triangulation": MODEL_TERRA,
    # Siddhi: writing quality and evidence-citation enforcement.
    "report_synthesis": MODEL_TERRA,
    # ASSUMPTION: §B.3's table does not list email composition. Assigned Terra
    # rather than Luna because a lifecycle email is prose a candidate reads
    # over the client's name, which is the "writing" side of §B.2's split, and
    # because every send is human-editable before it goes out -- a draft a
    # person will not want to rewrite is worth the better model. Surfaced here
    # rather than left as a silent judgment call.
    "email_composition": MODEL_TERRA,
    # ── Luna: extraction, classification, routing ───────────────────────────
    # Bodha's situation-type call is a six-way classification over a completed
    # SWOT, and the Hiring Manager confirms it explicitly before the session
    # closes, so a wrong label is caught by a human rather than by a rescore.
    "situation_classification": MODEL_LUNA,
    # Miti stage 2. Narrow, mechanical, must-not-evaluate.
    "claim_extraction": MODEL_LUNA,
    # Miti stage 3. Mostly rule-based; only the specificity modifier needs
    # model judgment at all.
    "evidence_tiering": MODEL_LUNA,
    # Yukti's AI Score. "Must be fast; this is an 'instant' product
    # requirement" (§B.3).
    "rerank": MODEL_LUNA,
    # Resume parsing and field extraction.
    "extraction": MODEL_LUNA,
}

#: An unlisted task is a programming error, not a default. Kept as an explicit
#: raise for the reason the old `provider_order` did: `conversation_turn` was
#: missing from the routing table for two days, every conversational call raised
#: ValueError, every caller correctly degraded to the scripted question, and the
#: product looked exactly as unadaptive as before the adaptive work shipped. A
#: silent default would have hidden that for longer, not less long.
def model_for(task_type: str) -> str:
    """The model id `task_type` runs on. Raises for an unknown type."""
    try:
        return MODEL_FOR_TASK[task_type]
    except KeyError as exc:
        raise ValueError(
            f"Unknown LLM task_type {task_type!r}; expected one of "
            f"{sorted(MODEL_FOR_TASK)}"
        ) from exc


def is_known_task(task_type: str) -> bool:
    return task_type in MODEL_FOR_TASK


def provider_order(task_type: str) -> list[str]:
    """Retained for callers that report which vendor served a call.

    One vendor, so the list has one element -- but it still validates
    `task_type`, which is the half of the old contract that was load-bearing.
    """
    model_for(task_type)
    return [PROVIDER]


# ── Timeouts ─────────────────────────────────────────────────────────────────
#
# Unchanged in intent from the multi-provider era, and the reasoning is
# reproduced because it is the part a single vendor does not make obsolete.
#
# The latency brief asks for a flat 10-15s cap on every LLM call. That is right
# for the calls a person is waiting on and wrong for the ones they are not: a
# PRISM Report synthesises seven sections in one response and cannot finish in
# 15 seconds, so a flat cap there would not make the product faster -- it would
# make every report fail and then be retried, which is slower AND produces
# nothing.
#
#   INTERACTIVE -- a request handler is blocked. Capped tight, so a slow call
#   costs one visible pause and the caller's deterministic fallback takes over.
#
#   BACKGROUND -- a Celery task. Nobody is watching, and a truncated report is
#   worse than a slow one.
#
# The reasoning tier is slower per token than the free-tier flash models it
# replaced, and the interactive numbers were raised accordingly rather than
# left where a faster model had put them. Leaving them would have converted a model upgrade
# into a timeout regression: the caller degrades, the product looks unchanged,
# and nothing announces which of the two happened.
TASK_TIMEOUTS: dict[str, float] = {
    # ── IMMEDIATE interactive: a request handler is blocked and the OUTPUT IS
    #    SHORT. The 15s/30s contract from the latency brief is unchanged for
    #    these, because a slower model does not make a 60-token reply slow.
    #
    #    THE MOST interactive call in the product: a candidate is sitting in
    #    front of a text box, and there can be two of these in one turn
    #    (classify, then write). Tighter than the others for that reason -- a
    #    slow call here is felt twice per question -- and left exactly where the
    #    flash-model era put it, so the candidate-facing latency contract is
    #    unmoved by the vendor change.
    "conversation_turn": 12.0,
    "situation_classification": 12.0,
    "email_composition": 15.0,
    "rerank": 15.0,
    "swot_intake": 15.0,
    "company_dna_intake": 15.0,
    # ── GENERATIVE interactive: a request handler is blocked and the output is
    #    a DOCUMENT. This is the one number the model consolidation genuinely
    #    moved, and it is worth stating why rather than letting a reader assume
    #    the cap was relaxed out of convenience.
    #
    #    The brief's flat 15s cap was measured against a flash-class model
    #    emitting a 4096-token ceiling in a few seconds. A reasoning-tier model
    #    is slower per token and better per token, and holding 15s would not make
    #    the Generate JD button faster -- it would make every generation time
    #    out and fall back to the deterministic template, permanently. That is
    #    the exact failure the brief's own reasoning already names for
    #    report_synthesis ("a flat cap there would not make the product faster,
    #    it would make every report fail"); this is the same argument one tier
    #    down. `tests/test_platform_audit.py` encodes both tiers so the
    #    exception is a reviewed rule rather than a drifted number.
    "jd_generation": 25.0,
    # Background.
    "technical_questions": 90.0,
    "competency_transformation": 90.0,
    "behavioral_assessment": 60.0,
    "claim_extraction": 60.0,
    "evidence_tiering": 45.0,
    "dimension_evaluation": 60.0,
    "triangulation": 60.0,
    "report_synthesis": 120.0,
    "extraction": 60.0,
}

#: Total wall-clock budget for one logical call, across every retry.
#:
#: Without this, "20s per attempt" times a retry budget of four is an 80-second
#: request with a 20-second timeout on it. The per-attempt cap alone does not
#: bound what the user experiences, and this is the bound that does.
TASK_TOTAL_BUDGET: dict[str, float] = {
    # Two attempts at the 12s cap. Deliberately short: this budget is spent
    # while a candidate watches a text box, and the degraded path here is the
    # scripted question rather than a failure. `agent_loop.INTERACTIVE_DEADLINE`
    # is 26s and must stay above this number, or the loop's own deadline would
    # be tighter than one router call and the second attempt could never run.
    "conversation_turn": 24.0,
    "situation_classification": 24.0,
    "email_composition": 30.0,
    "rerank": 30.0,
    "swot_intake": 30.0,
    "company_dna_intake": 30.0,
    # The generative-interactive exception. See TASK_TIMEOUTS above.
    "jd_generation": 50.0,
    "technical_questions": 200.0,
    "competency_transformation": 200.0,
    "behavioral_assessment": 140.0,
    "claim_extraction": 140.0,
    "evidence_tiering": 100.0,
    "dimension_evaluation": 140.0,
    "triangulation": 140.0,
    "report_synthesis": 280.0,
    "extraction": 140.0,
}

DEFAULT_TIMEOUT = 45.0
DEFAULT_TOTAL_BUDGET = 120.0


def timeout_for(task_type: str) -> float:
    return TASK_TIMEOUTS.get(task_type, DEFAULT_TIMEOUT)


def total_budget_for(task_type: str) -> float:
    """Wall-clock ceiling for the entire retry chain of one logical call."""
    return TASK_TOTAL_BUDGET.get(task_type, DEFAULT_TOTAL_BUDGET)


# ── Output ceiling ───────────────────────────────────────────────────────────
#
# `max_tokens` is optional on Chat Completions, and this table is sent anyway,
# for the reason it existed before the parameter was mandatory: an unbounded
# generation is an unbounded bill and an unbounded wait, and `report_synthesis`
# asking for seven sections needs a ceiling large enough to finish rather than
# no ceiling at all. It is cost control and latency control, not a transport
# requirement.
TASK_MAX_TOKENS: dict[str, int] = {
    "conversation_turn": 2048,
    "jd_generation": 4096,
    "email_composition": 1024,
    "swot_intake": 1024,
    "company_dna_intake": 1024,
    "situation_classification": 512,
    "rerank": 2048,
    "technical_questions": 8192,
    # Seven stages over a whole matrix.
    "competency_transformation": 8192,
    "behavioral_assessment": 4096,
    "claim_extraction": 8192,
    "evidence_tiering": 4096,
    "dimension_evaluation": 4096,
    "triangulation": 4096,
    # Seven report sections in one response -- the largest thing we ask for.
    "report_synthesis": 8192,
    "extraction": 8192,
}

DEFAULT_MAX_TOKENS = 4096


def max_tokens_for(task_type: str) -> int:
    return TASK_MAX_TOKENS.get(task_type, DEFAULT_MAX_TOKENS)


# ── Temperature ──────────────────────────────────────────────────────────────
#
# The split is by whether the task JUDGES or WRITES, and it is unchanged.
#
# A scoring call must return the same grade for the same answer every time it
# runs. Anything above zero means a candidate's grade depends partly on when
# they were scored, which is indefensible in a hiring decision and, worse,
# unfalsifiable: a rescore that disagrees looks like a bug in the rubric rather
# than sampling noise.
#
# A conversational turn is the opposite case. Asking a follow-up at 0.0 makes
# the interviewer sound like a form, repeating near-identical phrasing to every
# candidate, which is exactly the "static script" complaint. Phrasing may vary;
# WHAT is asked is fixed by the matrix, not by the sampler.
TASK_TEMPERATURE: dict[str, float] = {
    # ── Deterministic: these judge. ─────────────────────────────────────────
    "behavioral_assessment": 0.0,
    "report_synthesis": 0.0,        # states the grades a client reads
    "rerank": 0.0,                  # orders candidates
    "extraction": 0.0,
    "claim_extraction": 0.0,
    "evidence_tiering": 0.0,
    "dimension_evaluation": 0.0,    # THE grade. Never above zero.
    "triangulation": 0.0,
    "situation_classification": 0.0,
    # ── Generative: these write. ────────────────────────────────────────────
    "competency_transformation": 0.2,
    "technical_questions": 0.4,
    "jd_generation": 0.5,
    "email_composition": 0.5,
    "swot_intake": 0.5,
    "company_dna_intake": 0.5,
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


# ── Retry budget ─────────────────────────────────────────────────────────────
#
# How many attempts the router will spend on one logical call before it gives up
# and lets the caller's own deterministic fallback take over.
#
# Smaller than the multi-provider era's budgets, and deliberately so. Those
# numbers were sized to walk THREE provider tiers plus sibling-key retries; with
# one vendor there is nothing to walk to, so an attempt is only ever worth
# making against a transient (429 or 5xx or timeout). Beyond a few of those the
# honest answer is that the vendor is unavailable, and spending more attempts
# just makes the caller wait longer to hear it.
TASK_RETRY_BUDGET: dict[str, int] = {
    "conversation_turn": 2,
    "situation_classification": 2,
    "jd_generation": 3,
    "email_composition": 3,
    "swot_intake": 3,
    "company_dna_intake": 3,
    "rerank": 3,
    "technical_questions": 3,
    "competency_transformation": 3,
    "behavioral_assessment": 3,
    "claim_extraction": 3,
    "evidence_tiering": 3,
    "dimension_evaluation": 3,
    "triangulation": 3,
    "report_synthesis": 3,
    "extraction": 3,
}

DEFAULT_RETRY_BUDGET = 3


def retry_budget_for(task_type: str) -> int:
    return TASK_RETRY_BUDGET.get(task_type, DEFAULT_RETRY_BUDGET)


# ── Backoff ──────────────────────────────────────────────────────────────────
#
# Exponential with a cap, and the cap matters more than the base: an interactive
# task's whole wall-clock budget is 40 seconds, so a backoff that grew past a
# few seconds would spend the budget sleeping rather than trying. The router
# also honours a `retry-after` header when the vendor sends one, which is
# strictly better information than any local curve.
BACKOFF_BASE_SECONDS = 0.5
BACKOFF_MAX_SECONDS = 8.0


def backoff_seconds(attempt: int) -> float:
    """Delay before attempt number `attempt` (1-based). Bounded."""
    if attempt <= 1:
        return 0.0
    return float(min(BACKOFF_BASE_SECONDS * (2 ** (attempt - 2)), BACKOFF_MAX_SECONDS))


# ── Failure classification (spec-doc5 §B.4) ──────────────────────────────────
#
# Simplified to what actually applies to the OpenAI and Voyage APIs. Every
# branch that existed only for another vendor's quirk is gone, and it is worth
# naming them so a reader does not go looking: the OpenRouter 402
# ("can only afford N tokens") and its adaptive max_tokens re-ask, the Groq 413
# organisation-wide size failure, the Groq 429 quota regex, and the free-tier
# `limit: 0` withdrawal case. None of them describe a paid single-vendor
# account, and keeping dead branches around a retry loop is how a retry loop
# becomes unreviewable.

#: The account cannot be used at all: a bad or revoked key, or a permission
#: problem. Retrying is pointless and the breaker should trip immediately --
#: unlike a 429, no amount of waiting fixes it.
CREDENTIAL_STATUSES: frozenset[int] = frozenset({401, 403})

#: Rate limited. Transient by definition, honours `retry-after`.
RATE_LIMIT_STATUS = 429

#: The vendor failed. Transient, retried with backoff.
def is_provider_error(status: int) -> bool:
    return 500 <= status < 600


def classify_status(status: int) -> str:
    """One of: credential | rate_limit | provider_error | client_error."""
    if status in CREDENTIAL_STATUSES:
        return "credential"
    if status == RATE_LIMIT_STATUS:
        return "rate_limit"
    if is_provider_error(status):
        return "provider_error"
    return "client_error"


def is_retryable_status(status: int) -> bool:
    """A 4xx that is not 429 is our bug and will fail identically on retry."""
    return status == RATE_LIMIT_STATUS or is_provider_error(status)


# ── Cost attribution ─────────────────────────────────────────────────────────
#
# USD per MILLION tokens, as DATA here rather than inline in the router, for the
# same reason timeouts are: a commercial number changes on someone else's
# schedule and must be editable without touching the retry loop.
#
# THESE TWO ROWS ARE UNVERIFIED FOR THESE TWO MODEL IDS, and saying so is the
# point of this paragraph. No published price sheet has been read for
# `gpt-5.6-terra` or `gpt-5.6-luna`; the rates below carry forward the previous
# roster's reasoning-tier and extraction-tier figures unchanged, so what they
# encode honestly is the RATIO between the tiers and not the absolute cost of
# either. `VERIFICATION_PENDING.md` carries the row.
#
# That is survivable because of how the number is used and how it is labelled.
# The router reports `estimated_cost_usd`, never `cost`: prompt caching, batch
# discounts and the vendor's own rounding all move the invoice, and the number
# here could never see any of them even with the right rates. What it IS good
# for is the comparison an operator actually needs -- which task_type is
# consuming the budget -- and that ordering is stable under a uniform error in
# either row.
#
# Keyed by MODEL, not by provider, because with one vendor the model is the only
# axis on which price varies.
TOKEN_PRICES_USD_PER_MILLION: dict[str, dict[str, float]] = {
    MODEL_TERRA: {"prompt": 3.00, "completion": 15.00},
    MODEL_LUNA: {"prompt": 1.00, "completion": 5.00},
}


def estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Estimated list-price cost of one call, in USD.

    Returns 0.0 for an unpriced model. Callers that need to distinguish "free"
    from "unknown" should check `is_priced` -- a missing price must never read
    as a free call.
    """
    prices = TOKEN_PRICES_USD_PER_MILLION.get(model)
    if not prices:
        return 0.0
    return (
        prompt_tokens * prices.get("prompt", 0.0)
        + completion_tokens * prices.get("completion", 0.0)
    ) / 1_000_000


def is_priced(model: str) -> bool:
    """True when a price is on file, so a 0.0 can be read correctly."""
    return model in TOKEN_PRICES_USD_PER_MILLION


# ── Credentials ──────────────────────────────────────────────────────────────

def configured_key_count() -> dict[str, int]:
    """{provider: how many of the two model credentials are present}.

    Retained for the admin health endpoint and `scripts/validate_stack.py`,
    which report the roster without ever leaking key material. It counts rather
    than reporting a boolean because the two keys can be present independently,
    and one of two is a state an operator needs to see: every task on the
    unconfigured tier raises and degrades to its caller's deterministic
    fallback while every task on the other tier looks perfectly healthy.
    """
    from app.core.config import get_settings  # noqa: PLC0415 -- avoids an import cycle

    settings = get_settings()
    present = sum(
        1
        for attribute in SETTINGS_ATTR_FOR_MODEL.values()
        if (getattr(settings, attribute, "") or "").strip()
    )
    return {PROVIDER: present}


def configured_models() -> dict[str, bool]:
    """{model id: whether its credential is set}. Never the key material.

    The per-model view `configured_key_count` deliberately flattens. Two of
    two and zero of two are unambiguous; one of two is not, and this is what
    says which one.
    """
    from app.core.config import get_settings  # noqa: PLC0415 -- avoids an import cycle

    settings = get_settings()
    return {
        model: bool((getattr(settings, attribute, "") or "").strip())
        for model, attribute in sorted(SETTINGS_ATTR_FOR_MODEL.items())
    }
