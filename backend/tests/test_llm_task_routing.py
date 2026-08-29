"""Every task type resolves to exactly one of the two permitted models.

This file is spec-doc5 §B's acceptance criterion executed rather than asserted
in prose:

    "Every model call in the system resolves to exactly one of Sonnet 5,
     Haiku 4.5, or Voyage-context-4 -- grep the codebase for any other model
     string and confirm zero results outside historical comments/docs."
    "The per-component assignment table in §B.3 matches what the code actually
     calls, verified, not assumed from reading the code once."

The second sentence is the reason the grep below reads SOURCE rather than
importing the config: a table that agrees with itself proves nothing. What can
go wrong is a model id typed into a service, and only a scan of the files can
see that.

It replaces the multi-provider version, which asserted that every task named a
provider chain and that every chain was a subset of the three known providers.
That test earned its place: `conversation_turn` was missing from `TASK_ROUTES`
for two days, every conversational call raised, every caller correctly degraded
to the scripted question, and the product looked exactly as unadaptive as it had
before the adaptive work shipped. The same failure is possible here -- a new
task type absent from `MODEL_FOR_TASK` raises on every call -- so the coverage
check survives the vendor change with the chain check removed.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from app.config import llm_providers

APP_ROOT = pathlib.Path(__file__).resolve().parents[1] / "app"


# ── The closed roster ────────────────────────────────────────────────────────


def test_only_two_models_are_permitted() -> None:
    assert llm_providers.ALLOWED_MODELS == {
        llm_providers.MODEL_SONNET,
        llm_providers.MODEL_HAIKU,
    }


@pytest.mark.parametrize("task_type", sorted(llm_providers.MODEL_FOR_TASK))
def test_every_task_resolves_to_a_permitted_model(task_type: str) -> None:
    assert llm_providers.model_for(task_type) in llm_providers.ALLOWED_MODELS


def test_an_unknown_task_type_raises_rather_than_defaulting() -> None:
    """A typo must fail loudly.

    Silently defaulting would send a scoring call to whichever model the default
    happened to be, and nothing downstream would announce it: every caller in
    the conversation and scoring paths catches broadly and degrades, which is
    the right answer to an outage and a perfect concealment of a config typo.
    """
    with pytest.raises(ValueError):
        llm_providers.model_for("no_such_task")


# ── §B.3, transcribed ────────────────────────────────────────────────────────
#
# Written out literally rather than derived from the config, so this file is a
# second, independent statement of the assignment. A change to `MODEL_FOR_TASK`
# that was not intended has to be made twice to pass.

SPEC_B3_ASSIGNMENT = {
    # Bodha -- SWOT / Company DNA conversational intake -> Sonnet 5
    "swot_intake": llm_providers.MODEL_SONNET,
    "company_dna_intake": llm_providers.MODEL_SONNET,
    # Sutra -- competency naming, observable-evidence authoring, weight
    # derivation -> Sonnet 5
    "competency_transformation": llm_providers.MODEL_SONNET,
    # Yukti -- AI Score / category matching -> Haiku 4.5 (must be fast)
    "rerank": llm_providers.MODEL_HAIKU,
    # Vaada -- conversation / question generation -> Sonnet 5
    "conversation_turn": llm_providers.MODEL_SONNET,
    # Miti -- claim extraction -> Haiku 4.5 (narrow, mechanical, must not evaluate)
    "claim_extraction": llm_providers.MODEL_HAIKU,
    # Miti -- evidence tiering -> Haiku 4.5
    "evidence_tiering": llm_providers.MODEL_HAIKU,
    # Miti -- five dimension evaluators -> Sonnet 5
    "dimension_evaluation": llm_providers.MODEL_SONNET,
    # Miti -- triangulation agent -> Sonnet 5
    "triangulation": llm_providers.MODEL_SONNET,
    # Siddhi -- dossier / PRISM generation -> Sonnet 5
    "report_synthesis": llm_providers.MODEL_SONNET,
}


@pytest.mark.parametrize("task_type,model", sorted(SPEC_B3_ASSIGNMENT.items()))
def test_spec_b3_assignment_matches_the_code(task_type: str, model: str) -> None:
    assert llm_providers.model_for(task_type) == model


def test_the_aggregator_has_no_task_type() -> None:
    """spec-doc5 §B.3: "Miti -- aggregator | **No model.** Deterministic code only".

    A task type would be a door into a room that must not have one. The
    aggregator's freedom from model calls is asserted properly in
    `test_miti_pipeline.py` by reading its source; this is the cheap half --
    there is no route to reach a model with.
    """
    for name in ("aggregation", "aggregator", "miti_aggregate", "composite"):
        assert not llm_providers.is_known_task(name)


# ── The grep ─────────────────────────────────────────────────────────────────

#: Any model-id-shaped string that is NOT one of the two permitted ones. Built
#: as vendor-family prefixes because that is how a stray id actually appears --
#: somebody pastes `claude-3-5-sonnet-20241022` or `gpt-4o` out of a snippet.
_FORBIDDEN_MODEL_PATTERNS = [
    re.compile(r"\bclaude-(?!sonnet-5\b|haiku-4-5-20251001\b)[a-z0-9.\-]+", re.I),
    re.compile(r"\bgpt-[0-9o]", re.I),
    re.compile(r"\bllama-[0-9]", re.I),
    re.compile(r"\bgemini-[a-z0-9.\-]+", re.I),
    re.compile(r"\bmixtral|\bmistral-", re.I),
    re.compile(r"\bbge-m3\b", re.I),
    re.compile(r"\bvoyage-(?!context-4\b)[a-z0-9.\-]+", re.I),
]


def _code_lines(path: pathlib.Path) -> list[tuple[int, str]]:
    """Executable lines only: comments and docstring bodies are excluded.

    spec-doc5's acceptance criterion says "outside historical comments/docs",
    and this codebase's comments deliberately record which model ids were
    retired and why. A test that forbade naming them would delete the only
    record of two real incidents. The line-level filter is crude -- it drops any
    line whose first non-space character starts a comment or that sits inside a
    triple-quoted block -- and crude is right here: a false NEGATIVE lets a
    forbidden id hide in a docstring, where it cannot be called.
    """
    lines: list[tuple[int, str]] = []
    in_doc = False
    delim = ""
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = raw.strip()
        if in_doc:
            if delim in stripped:
                in_doc = False
            continue
        if stripped.startswith(('"""', "'''")):
            delim = stripped[:3]
            # A one-line docstring opens and closes on the same line.
            if not (len(stripped) > 5 and stripped.endswith(delim)):
                in_doc = True
            continue
        if stripped.startswith("#") or not stripped:
            continue
        code = raw.split("  #", 1)[0]
        lines.append((number, code))
    return lines


def test_no_other_model_id_appears_in_executable_code() -> None:
    offenders: list[str] = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for number, line in _code_lines(path):
            for pattern in _FORBIDDEN_MODEL_PATTERNS:
                match = pattern.search(line)
                if match:
                    offenders.append(
                        f"{path.relative_to(APP_ROOT.parent)}:{number} {match.group(0)!r}"
                    )
    assert not offenders, (
        "spec-doc5 §B.2 permits exactly Sonnet 5, Haiku 4.5 and voyage-context-4. "
        "These executable lines name another model:\n  " + "\n  ".join(offenders)
    )


def test_the_retired_providers_are_gone_from_the_source_tree() -> None:
    """The removal is real, not a disabled flag (spec-doc5 §B.1).

    Checks for the MODULES and the client entry points, not for the words: the
    words survive legitimately in comments recording why the tier was removed.
    """
    assert not (APP_ROOT / "services" / "llm_capacity.py").exists()
    assert not (APP_ROOT / "scripts" / "probe_llm_models.py").exists()

    from app.services import llm_router

    for gone in (
        "_call_groq",
        "_call_gemini",
        "_call_openrouter",
        "capacity_ordered",
        "probe_each_provider_first",
        "rotate_within_provider",
        "affordable_max_tokens",
        "is_org_wide_size_failure",
    ):
        assert not hasattr(llm_router, gone), gone

    for gone in (
        "TASK_ROUTES",
        "PROVIDER_MODELS",
        "ROUTE_SCORE_WEIGHTS",
        "WORKLOAD_PROFILES",
        "TASK_WORKLOAD",
        "WorkloadClass",
        "env_key_slots",
        "KEY_SLOTS_PER_PROVIDER",
        "DECLARED_CONTEXT_LIMITS",
        "MIN_CEILING_FRACTION",
    ):
        assert not hasattr(llm_providers, gone), gone


# ── Bounds that must survive the vendor change ───────────────────────────────


@pytest.mark.parametrize("task_type", sorted(llm_providers.MODEL_FOR_TASK))
def test_every_task_is_bounded_twice_over(task_type: str) -> None:
    """A per-attempt timeout alone does not bound what a user experiences.

    N attempts at the per-attempt cap is a multiple of it, which is why the
    total budget exists and why it must exceed one attempt (or the first attempt
    could never finish) while staying under the naive product of attempts and
    timeout (or it would not be bounding anything).
    """
    timeout = llm_providers.timeout_for(task_type)
    budget = llm_providers.total_budget_for(task_type)
    attempts = llm_providers.retry_budget_for(task_type)
    assert timeout > 0
    assert attempts >= 1
    assert budget > timeout, task_type
    assert budget <= timeout * attempts + 1, task_type


def test_every_judging_task_is_deterministic() -> None:
    """A grade must not depend on when it was computed.

    `dimension_evaluation` is the one that matters most now: it IS the grade,
    and a rescore that disagreed would read as a broken rubric rather than as
    sampling noise.
    """
    for task in (
        "behavioral_assessment",
        "report_synthesis",
        "rerank",
        "extraction",
        "claim_extraction",
        "evidence_tiering",
        "dimension_evaluation",
        "triangulation",
        "situation_classification",
    ):
        assert llm_providers.temperature_for(task) == 0.0, task


def test_an_unlisted_task_defaults_to_deterministic() -> None:
    """The safe direction: a new creative task reads flat, a new SCORING task
    silently sampling above zero would make grades non-reproducible and nothing
    would announce it."""
    assert llm_providers.temperature_for("something_new_and_unlisted") == 0.0


def test_backoff_is_bounded() -> None:
    assert llm_providers.backoff_seconds(1) == 0.0
    values = [llm_providers.backoff_seconds(n) for n in range(2, 12)]
    assert values == sorted(values)
    assert max(values) <= llm_providers.BACKOFF_MAX_SECONDS


# ── Failure classification (§B.4) ────────────────────────────────────────────


def test_failure_classification_covers_exactly_the_four_real_cases() -> None:
    assert llm_providers.classify_status(401) == "credential"
    assert llm_providers.classify_status(403) == "credential"
    assert llm_providers.classify_status(429) == "rate_limit"
    assert llm_providers.classify_status(500) == "provider_error"
    assert llm_providers.classify_status(503) == "provider_error"
    # 402 was OpenRouter's prepaid-balance refusal and 413 was Groq's
    # organisation-wide size ceiling. Neither describes a paid Anthropic
    # account, and both now fall through to the non-retryable bucket.
    assert llm_providers.classify_status(402) == "client_error"
    assert llm_providers.classify_status(413) == "client_error"


def test_only_transients_are_retried() -> None:
    assert llm_providers.is_retryable_status(429)
    assert llm_providers.is_retryable_status(502)
    # Our bug. It will fail identically on retry, so spending the budget on it
    # only delays the caller's deterministic fallback.
    assert not llm_providers.is_retryable_status(400)
    assert not llm_providers.is_retryable_status(401)
    assert not llm_providers.is_retryable_status(404)


# ── Pricing ──────────────────────────────────────────────────────────────────


def test_an_unpriced_model_is_distinguishable_from_a_free_one() -> None:
    assert llm_providers.is_priced(llm_providers.MODEL_SONNET)
    assert llm_providers.estimate_cost_usd("some-unpriced-model", 1000, 1000) == 0.0
    assert not llm_providers.is_priced("some-unpriced-model")


def test_sonnet_costs_more_than_haiku_for_identical_traffic() -> None:
    """Not a price check -- a check that the two are not accidentally equal,
    which is what a copy-paste in the table would look like."""
    sonnet = llm_providers.estimate_cost_usd(llm_providers.MODEL_SONNET, 10_000, 2_000)
    haiku = llm_providers.estimate_cost_usd(llm_providers.MODEL_HAIKU, 10_000, 2_000)
    assert sonnet > haiku > 0
