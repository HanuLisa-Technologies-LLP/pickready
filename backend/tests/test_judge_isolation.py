"""The judge jury is outside the product, structurally, in both directions.

WHAT IS BEING PROTECTED (RPN-AI-UP-001 W7.4)
---------------------------------------------
`MODEL_FOR_TASK` is a closed mapping onto exactly two model ids, and
`test_llm_task_routing.py` greps executable source for any other model string.
The whole value of that closure is this: A CANDIDATE'S GRADE CANNOT BE PRODUCED
BY AN UNREVIEWED MODEL. A judge on a third vendor is not an exception to the
rule, it is outside its scope, and the only thing that makes "outside its scope"
mean anything is that no product entry point can reach the judge at all.

So two assertions, in opposite directions, and both are needed:

  1. NOTHING UNDER `app/services/` IMPORTS `app/evaluation/`. Without this, a
     scorer could call a judge helper and a grade would flow through code the
     model-closure grep has been told to skip.

  2. NOTHING UNDER `app/evaluation/judges/` IS REACHABLE FROM `app/api/` OR
     `app/workers/`. Without this, a route could reach the jury through some
     third module and the same hole opens from the other end.

WHY REACHABILITY AND NOT A DIRECT-IMPORT CHECK
------------------------------------------------
A direct-import check answers the wrong question, and this codebase has the
scar: for a whole phase every Part A agent name pointed at code no route
imported, every module was green in isolation, and the framework ran nowhere.
`import_graph.reachable_modules` walks the TRANSITIVE import graph from
`app/api/**`, `app/workers/**` and `app/main.py` with `ast`, which is the check
that would have caught it. Reused here rather than reimplemented: two answers to
"what can a route reach" would eventually disagree, and the disagreement would
be silent.

STATIC, DELIBERATELY. Importing the packages to find out would answer a
different question, namely what pytest's import order happens to have loaded.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from app.import_graph import reachable_modules

APP_ROOT = pathlib.Path(__file__).resolve().parents[1] / "app"
SERVICES_ROOT = APP_ROOT / "services"
EVALUATION_ROOT = APP_ROOT / "evaluation"
JUDGES_ROOT = EVALUATION_ROOT / "judges"

FORBIDDEN_FROM_SERVICES = "app.evaluation"
JUDGES_PACKAGE = "app.evaluation.judges"


def _imported_app_modules(source: str) -> set[str]:
    """Every `app.*` target a module names, from its source, with `ast`.

    Both spellings are collected. `from app.evaluation import judges` names the
    submodule in the ALIAS rather than in `node.module`, and a scanner that
    missed that reads a package import as reaching only the package's
    `__init__` -- which is how a check quietly answers "no" for everything.
    """
    targets: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith("app"):
                targets.add(module)
                targets.update(f"{module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            targets.update(
                alias.name for alias in node.names if alias.name.startswith("app")
            )
    return targets


def _python_files(root: pathlib.Path) -> list[pathlib.Path]:
    return [
        path
        for path in sorted(root.rglob("*.py"))
        if "__pycache__" not in path.parts
    ]


# ── The scanner itself, checked in both directions ───────────────────────────


def test_the_import_scanner_actually_detects_an_import() -> None:
    """The negative direction, and it is not a formality.

    A scanner with a typo in its attribute names returns an empty set for every
    file, the sweep below passes over anything at all, and nothing announces
    that the isolation stopped being enforced.
    """
    assert "app.evaluation" in _imported_app_modules("from app.evaluation import metrics")
    assert "app.evaluation.metrics" in _imported_app_modules(
        "from app.evaluation import metrics"
    )
    assert "app.evaluation.judges.jury" in _imported_app_modules(
        "import app.evaluation.judges.jury"
    )
    # A deeper `from` names the SUBMODULE, not the package. The sweep below
    # matches on the `app.evaluation.` prefix for exactly this reason, and this
    # line records that the scanner does not helpfully invent the parent.
    assert _imported_app_modules("from app.evaluation.golden import X") == {
        "app.evaluation.golden",
        "app.evaluation.golden.X",
    }
    assert _imported_app_modules("import json\nfrom typing import Any") == set()


# ── Direction one: services must not reach the eval layer ────────────────────


def test_no_service_module_imports_the_evaluation_package() -> None:
    offenders: list[str] = []
    scanned = 0
    for path in _python_files(SERVICES_ROOT):
        scanned += 1
        for target in _imported_app_modules(path.read_text(encoding="utf-8")):
            if target == FORBIDDEN_FROM_SERVICES or target.startswith(
                FORBIDDEN_FROM_SERVICES + "."
            ):
                offenders.append(f"{path.relative_to(APP_ROOT.parent)} imports {target}")
    # A sweep that scanned nothing passes vacuously. `app/services/` is the
    # largest package in this tree; a count in single digits means the walk is
    # broken, not that the tree shrank.
    assert scanned > 50, scanned
    assert not offenders, (
        "The evaluation layer must not be importable from a service. A scorer "
        "that could call a judge helper would route a candidate's grade "
        "through code the model-closure grep is told to skip:\n  "
        + "\n  ".join(offenders)
    )


def test_the_evaluation_layer_may_import_services_and_that_asymmetry_is_intended() -> None:
    """The rule is one-directional and stating it stops somebody "fixing" it.

    `regression.py` imports `app.services.verification` and `app.services.rag`
    inside its case functions on purpose: a regression case has to exercise the
    real module. That direction is harmless, because reaching FROM the eval
    layer INTO the product cannot make the product call a judge.
    """
    text = (EVALUATION_ROOT / "regression.py").read_text(encoding="utf-8")
    assert "app.services" in text


# ── Direction two: no product entry point may reach the jury ─────────────────


def test_the_judges_package_is_not_reachable_from_a_route_or_a_worker() -> None:
    reachable = reachable_modules()
    # Sanity: the reachability walk found a real graph. An empty or tiny answer
    # would make every assertion below pass while proving nothing.
    assert len(reachable) > 100, len(reachable)
    assert "app.main" in reachable

    offenders = sorted(
        module
        for module in reachable
        if module == JUDGES_PACKAGE or module.startswith(JUDGES_PACKAGE + ".")
    )
    assert not offenders, (
        "A judge reachable from a request handler or a background task would "
        "destroy the property the closed model mapping exists to guarantee, "
        "which is that a grade cannot be produced by an unreviewed model. "
        "Reachable: " + ", ".join(offenders)
    )


def test_the_whole_evaluation_package_is_unreachable_from_a_route_or_a_worker() -> None:
    """Stronger than W7.4 requires, and true today, so it is pinned.

    The eval layer is reached only by `app/scripts/*`, which are commands a
    person or CI runs, never entry points a user's request arrives at. Losing
    that property would not by itself be a judge leak, but it is the step
    before one, and it is far cheaper to notice here than in the reachability
    audit six months from now.
    """
    reachable = reachable_modules()
    offenders = sorted(
        module
        for module in reachable
        if module == "app.evaluation" or module.startswith("app.evaluation.")
    )
    assert not offenders, ", ".join(offenders)


def test_every_judge_module_is_actually_under_the_judges_directory() -> None:
    """W7.4 says the jury lives in `app/evaluation/judges/`, not in
    `app/services/`. This is that sentence, executed: the modules exist where
    they were required to exist, so the reachability assertion above is about
    real files rather than about a package nobody wrote."""
    assert JUDGES_ROOT.is_dir()
    modules = {path.stem for path in _python_files(JUDGES_ROOT)}
    assert {"__init__", "jury", "protocol"} <= modules
    assert not (SERVICES_ROOT / "judges").exists()


def test_an_uncredentialed_deployment_gets_an_empty_panel_and_a_reason(
    monkeypatch,
) -> None:
    """No credential, no panel, and the reason names what to set.

    SUPERSEDED PREMISE, KEPT DELIBERATELY. This used to assert that the reason
    mentioned "W7.2", because at the time there was no judge credential of any
    kind and the determinism probe had never run. Both changed on 2026-09-09:
    Groq keys exist, the probe ran, and `configured_jurors()` returns a real
    panel when it can. What survives is the rule that outlived the situation --
    an empty panel, never a stub that answers, and a reason a reader can act on.

    Every slot is cleared explicitly rather than trusting the test environment
    to be bare. A test that only passes because the machine happens to lack a
    key would silently stop testing anything on a developer's laptop.
    """
    from app.evaluation.judges import configured_jurors, gemini, groq
    from app.evaluation.judges import unavailable_reason

    for slot in groq.CREDENTIAL_SLOTS + gemini.CREDENTIAL_SLOTS:
        monkeypatch.delenv(slot, raising=False)

    assert configured_jurors() == ()
    reason = unavailable_reason()
    # It must name the variables to set, because that is the actionable half.
    assert "GROQ_API_KEY_1" in reason
    assert "GEMINI_API_KEY_1" in reason
    # And it must say why the product's own credentials cannot be borrowed.
    assert "OPENAI_GPT_TERRA" in reason


def test_a_credentialed_deployment_gets_a_real_panel(monkeypatch) -> None:
    """The direction the old test could not check, and the one that matters.

    `configured_jurors()` was HARDCODED to return `()` for its whole existence.
    An assertion that it returns empty passes just as happily against a
    function that can never return anything else, so it could never have caught
    the wiring being absent. This constructs the panel from a fake credential
    and makes no network call: what is pinned is that a panel FORMS, that its
    members are distinct, and that every juror is a judge-vendor model rather
    than one of the product's two.
    """
    from app.evaluation.judges import configured_jurors, gemini, groq

    for slot in gemini.CREDENTIAL_SLOTS:
        monkeypatch.delenv(slot, raising=False)
    monkeypatch.setenv(groq.CREDENTIAL_SLOTS[0], "not-a-real-key-and-never-called")

    jurors = configured_jurors()
    assert len(jurors) == len(groq.JUDGE_MODELS)

    ids = [juror.judge_id for juror in jurors]
    # A jury's value comes from heterogeneity, so two jurors sharing an id are
    # one judge counted twice. `judge_set` refuses that; this catches it here.
    assert len(set(ids)) == len(ids)

    # NONE of them may be a product model. That is the whole point of the
    # judge sitting outside the closed mapping: a model scoring its own
    # family's output measures loyalty as quality.
    for juror in jurors:
        assert "gpt-5.6" not in juror.model


def test_a_juror_abstains_rather_than_guessing_when_the_vendor_fails() -> None:
    """A transport failure did not disagree with the human. It did not answer.

    Returning a label here would put a vendor outage into a kappa. `ABSTAIN`
    sends it to `build_result`, which WIDENS the accuracy interval instead of
    scoring the case wrong.
    """
    import types

    from app.evaluation.judges import ABSTAIN, DEFAULT_SCALE, ModelJuror

    def failing_call(model, prompt, keys, *, seed, scale, offset=0):
        return "error:http_503:vendor_overloaded"

    juror = ModelJuror(
        judge_id="fake:down",
        vendor=types.SimpleNamespace(call=failing_call),
        model="fake-model",
        keys=("k",),
        scale=DEFAULT_SCALE,
    )
    case = types.SimpleNamespace(
        case_id="c", requirement="r", evidence="e", payload_ref="p", notes=""
    )
    assert juror.verdict(case) == ABSTAIN

    # And an answer outside the scale abstains too: an unparseable verdict is
    # the judge being broken, not the judge being wrong.
    def off_scale(model, prompt, keys, *, seed, scale, offset=0):
        return "off_scale:maybe_probably"

    juror = ModelJuror(
        judge_id="fake:chatty",
        vendor=types.SimpleNamespace(call=off_scale),
        model="fake-model",
        keys=("k",),
        scale=DEFAULT_SCALE,
    )
    assert juror.verdict(case) == ABSTAIN


def test_calling_the_jury_with_no_judges_raises_rather_than_returning_zeroes() -> None:
    from app.evaluation.judges import JudgeProtocol, JuryUnavailable, judge_set

    protocol = JudgeProtocol(
        scale=("met", "not_met"),
        population="Vivekium reasoning golden set 2026.Q3.1",
        abstention_handling="interval",
        aggregation="majority",
        position_handling="not_applicable",
        repeats=1,
        judge_ids=("a", "b", "c"),
    )
    with pytest.raises(JuryUnavailable):
        judge_set([], (), protocol)
