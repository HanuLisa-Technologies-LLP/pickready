"""The retired matcher is gone, and this is what keeps it gone (PLAN-p2 4.5).

WHAT WAS REMOVED
----------------
Phase 2 of the Vivekium release replaced the resume-stage matcher with Yukti
(`services/yukti`): one reading per person, grounded quotes, a derived rank key
and words on every screen. The retired half is DELETED rather than left
unreachable, because a module that still compiles is one handler away from
coming back without a decision behind it:

* the deterministic pre-screen grade (`services/hiring/prescreen`), whose
  name-blind helpers moved byte for byte to `yukti/anonymise.py`;
* the longevity nudge (`services/longevity`), which moved a `match_score`
  nothing orders on any more;
* the third copy of the rating scale (`services/tiers`);
* the ranking critic (`verification/ranking`), which judged the four
  parameters' 25-30 word comments Yukti no longer writes;
* the Matching categories generator and scorer prompts;
* the legacy read side of `services/matching` (the comments-only payload, the
  score-stripped breakdown, the word helpers and the padding clauses);
* the Candidate Dashboard's number and letter grades, the `match_percent`
  exception to rule 1, the unscoped task-status and per-job results routes,
  and the run alias on the jobs router;
* six task types no live caller used (`tests/test_llm_task_routing.
  DELETED_TASK_TYPES`).

WHAT IS SWEPT, AND WHAT IS NOT
------------------------------
Live code and configuration: the backend's `app/` and the frontend's `app/`,
`components/` and `lib/`, plus `infra/`, the repository `scripts/` and
`.github/`. NOT `backend/tests/` or `backend/harness/`: the guards there name
these symbols precisely to forbid them (`assert "match_percent" not in
payload`), and a sweep that failed on its own guards would be deleted by the
first person it annoyed. Whitespace-normalised through `removal_sweep`, so a
mention wrapped across a line is still found.

THE HAND-OFF IS COMPLETE
------------------------
`services/matching_categories` survived with exactly one reader,
`functional_assessment._matching_dimensions` (the PRISM AI Score section).
The grading split (PLAN-p5 WP5-D) replaced it with Yukti's frozen snapshot
(`yukti.projection.pre_assessment_snapshot`) and deleted the module in the
same change, so the pending map below is empty and any new reader fails.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

from tests.removal_sweep import BACKEND, REPO, sweep

APP = BACKEND / "app"
THIS_FILE = pathlib.Path(__file__).resolve()

#: Modules the retired matcher owned. Named individually rather than globbed:
#: a glob would pass the day somebody re-added one under a different name.
GONE_MODULES = (
    "app.services.longevity",
    "app.services.tiers",
    "app.services.hiring.prescreen",
    "app.services.verification.ranking",
)

#: Prompts that fed the retired matcher. The categories prompt is assembled
#: rather than spelled: `test_tatva_matrix_editor_removed` sweeps the tests
#: for its name, and one sweep must not trip on another's list.
GONE_PROMPTS = ("matching_scoring_system", "matching_categories" + "_system")

#: Live code roots. See the module docstring for why tests and the harness are
#: not among them.
LIVE_ROOTS = (
    APP,
    REPO / "frontend" / "app",
    REPO / "frontend" / "components",
    REPO / "frontend" / "lib",
    REPO / "infra",
    REPO / "scripts",
    REPO / ".github",
)

#: Every name the retired matcher's surface carried. A hit in live code is a
#: reader, a writer or a route the removal missed.
LEGACY_NAMES = (
    "match_percent",
    "Match %",
    "_PAD_CLAUSES",
    "publish_ai_scores",
    "customer_success_patterns",
    "skills_match_comment",
    "role_alignment_comment",
    "MatchingCategories" + "Card",
    "/matching/jobs/{job_id}/results",
    "run-matching",
    "/matching/tasks/",
    "ranking_payload",
    "client_breakdown",
    "adjusted_match_score",
    "ready_pick_score",
    "pre_screen_grade",
    "prescreen_breakdown",
    "matching_label",
    "enforce_word_range",
    "assign_tier",
    "yukti_gate",
    "PreScreenResult",
    "competency_transformation",
    "situation_classification",
    "evidence_tiering",
)

#: Stale wording in the backend's live modules (the scripts are Phase 7's
#: GCP and Cloudinary cleanup, so `app/scripts` is exempt for these alone).
STALE_WORDING = ("Cloudinary", "4-parameter", "four-parameter")

#: A reference to the surviving categories module, as code rather than as the
#: table name (`job_matching_categories`) or the history column
#: (`matching_categories_finalized_at`), both of which S4 keeps.
MATCHING_CATEGORIES_MODULE = re.compile(
    r"services[./]matching_categories\b|import matching_categories\b|\bmatching_categories\."
)

#: Files that still reference the categories module, each with the owner who
#: removes the reference. Not permanent: see the hand-off test.
PENDING_CATEGORY_READERS: dict = {
    # EMPTY since the grading split (PLAN-p5 WP5-D): the report's AI Score is
    # Yukti's frozen snapshot (`yukti.projection.pre_assessment_snapshot`), and
    # `services/matching_categories` was deleted with its last reader.
}

#: The router and routing-table entry points a task type is passed to. A
#: string such as "technical_questions" is also a TABLE name, so the check is
#: on these calls rather than on every call that happens to take one first.
TASK_TYPE_CALLS = frozenset(
    {
        "invoke_llm",
        "chat_completion",
        "model_for",
        "provider_order",
        "timeout_for",
        "total_budget_for",
        "max_tokens_for",
        "temperature_for",
        "retry_budget_for",
        "is_known_task",
    }
)

#: The six deleted task types, as a call site would pass them.
DELETED_TASK_TYPES = frozenset(
    {
        "rerank",
        "competency_transformation",
        "situation_classification",
        "claim_extraction",
        "evidence_tiering",
        "technical_questions",
    }
)


def _pattern(names) -> re.Pattern[str]:
    return re.compile("|".join(re.escape(name) for name in names))


# ── The modules and prompts ──────────────────────────────────────────────────


@pytest.mark.parametrize("module", GONE_MODULES)
def test_the_module_cannot_be_imported(module: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        __import__(module)


@pytest.mark.parametrize("module", GONE_MODULES)
def test_the_file_is_not_on_disk_either(module: str) -> None:
    """A module left on disk but unreachable is a file the next reader finds,
    reads, and reasonably assumes is live."""
    relative = pathlib.Path(*module.split("."))
    assert not (BACKEND / relative.with_suffix(".py")).exists(), module
    assert not (BACKEND / relative / "__init__.py").exists(), module


@pytest.mark.parametrize("prompt", GONE_PROMPTS)
def test_the_prompt_is_gone(prompt: str) -> None:
    assert not (APP / "prompts" / f"{prompt}.txt").exists(), prompt


# ── The sweeps ───────────────────────────────────────────────────────────────


def test_no_live_code_names_the_retired_matcher() -> None:
    hits = sweep(_pattern(LEGACY_NAMES), roots=LIVE_ROOTS)
    assert not hits, "the retired matcher is still named in live code:\n" + "\n".join(hits)


def test_no_live_backend_module_carries_the_stale_wording() -> None:
    hits = sweep(_pattern(STALE_WORDING), roots=(APP,), exempt=(APP / "scripts",))
    assert not hits, "stale storage or four-parameter wording:\n" + "\n".join(hits)


def test_only_the_named_hand_off_reads_the_categories_module() -> None:
    exempt = (APP / "services" / "matching_categories.py", *PENDING_CATEGORY_READERS)
    hits = sweep(MATCHING_CATEGORIES_MODULE, roots=LIVE_ROOTS, exempt=exempt)
    assert not hits, "a new reader of the retired categories module:\n" + "\n".join(hits)


def test_the_pending_hand_off_is_still_real() -> None:
    """An exemption whose file no longer reads the module is an exemption that
    outlived its reason: delete the entry AND `services/matching_categories`."""
    for path, owner in PENDING_CATEGORY_READERS.items():
        text = path.read_text(encoding="utf-8")
        assert MATCHING_CATEGORIES_MODULE.search(text), (
            f"{path.relative_to(REPO)} no longer reads matching_categories "
            f"({owner}); delete the module and this entry together"
        )


def test_no_call_site_passes_a_deleted_task_type() -> None:
    """A call such as `invoke_llm("rerank", ...)` would raise at runtime on an
    unknown task type, and only when that path finally ran. Found by AST, on
    the router and routing-table calls and on any `task_type=` keyword, so a
    dict key, an enum member or a table named the same is not mistaken for a
    call."""
    offenders: list[str] = []
    for path in APP.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.id if isinstance(func, ast.Name)
                else func.attr if isinstance(func, ast.Attribute)
                else None
            )
            first = node.args[0] if node.args else None
            if (
                name in TASK_TYPE_CALLS
                and isinstance(first, ast.Constant)
                and first.value in DELETED_TASK_TYPES
            ):
                offenders.append(f"{path.relative_to(REPO)}:{node.lineno}: {first.value}")
            for keyword in node.keywords:
                if (
                    keyword.arg == "task_type"
                    and isinstance(keyword.value, ast.Constant)
                    and keyword.value.value in DELETED_TASK_TYPES
                ):
                    offenders.append(
                        f"{path.relative_to(REPO)}:{node.lineno}: {keyword.value.value}"
                    )
    assert not offenders, offenders


def test_the_sweep_is_not_vacuous() -> None:
    """The guard on the guard: every sweep here must find a planted hit, or a
    broken helper would report a clean tree for ever."""
    planted = sweep(_pattern(LEGACY_NAMES), roots=(THIS_FILE,))
    assert len(planted) >= len(LEGACY_NAMES)
    assert sweep(MATCHING_CATEGORIES_MODULE, roots=(THIS_FILE,))
