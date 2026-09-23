"""The harness is outside the product, and nothing inside the product may reach it.

WHAT IS BEING PROTECTED (RPN-HARNESS-001 section 1)
-----------------------------------------------------
HARNESS.md makes isolation the FIRST requirement rather than the last, and
states the reason in one sentence: "A harness that production code can reach is
a harness that can change production behaviour."

That is not an abstract worry, because of what the harness is allowed to do.
It injects faults at real seams, it replaces the clock, it forces
`TASK_DISPATCH_BACKEND` to `record`, and it serves hand-authored vendor
contracts in place of a vendor. Every one of those is correct inside a scenario
and catastrophic inside a request handler. A single `from harness import ...`
in `app/` would put all of it one import away from a production code path, and
the failure would not announce itself: a dispatch that records instead of
dispatching looks exactly like a dispatch that worked.

WHY A DIRECT AST SWEEP AND NOT `reachable_modules`
----------------------------------------------------
`test_judge_isolation.py` uses `orchestration_checks.reachable_modules` because
the thing it protects lives INSIDE `app/`, so a direct-import check would miss a
route reaching the jury through a third module. That argument does not transfer
here, and using the same tool would be cargo cult rather than reuse: `harness`
is not an `app.*` module, so the transitive walk over `app.*` can never leave
`app.` and reach it. The only way `app/` gets to `harness` is by naming it
directly, which is exactly what this sweep looks for. Walking transitively would
add cost and prove nothing extra.

STATIC, DELIBERATELY, for the reason `test_judge_isolation.py` gives: importing
the packages to find out would answer a different question, namely what pytest's
import order happens to have loaded.
"""
from __future__ import annotations

import ast
import pathlib

APP_ROOT = pathlib.Path(__file__).resolve().parents[1] / "app"
BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
HARNESS_ROOT = BACKEND_ROOT / "harness"

FORBIDDEN_IN_APP = "harness"


def _imported_top_levels(source: str) -> set[str]:
    """Every top-level package a module names, from its source, with `ast`.

    The top level alone is enough here, unlike in the judge sweep: anything
    under `harness` starts with `harness.`, and a relative import cannot reach
    a sibling package of `app` at all. Collecting the first dotted component
    keeps the comparison exact, so a legitimately named local variable or a
    module called `harness_helpers` cannot trip it.
    """
    targets: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            # `level > 0` is a relative import, which resolves inside the
            # importing package and therefore cannot name `harness`.
            if node.level == 0 and node.module:
                targets.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            targets.update(alias.name.split(".")[0] for alias in node.names)
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
    that the isolation stopped being enforced. This is the same guard
    `test_judge_isolation.py` carries, for the same reason.
    """
    assert "harness" in _imported_top_levels("from harness.faults import model_429")
    assert "harness" in _imported_top_levels("import harness")
    assert "harness" in _imported_top_levels("import harness.doubles.clock as clock")
    assert "harness" in _imported_top_levels("from harness import scenario")
    # A relative import resolves inside the importing package, so it is not a
    # way into `harness` and must not be reported as one.
    assert _imported_top_levels("from .services import rbac") == set()
    assert _imported_top_levels("import json\nfrom typing import Any") == {
        "json",
        "typing",
    }


# ── The sweep ────────────────────────────────────────────────────────────────


def test_no_module_under_app_imports_the_harness() -> None:
    offenders: list[str] = []
    scanned = 0
    for path in _python_files(APP_ROOT):
        scanned += 1
        if FORBIDDEN_IN_APP in _imported_top_levels(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(BACKEND_ROOT)))
    # A sweep that scanned nothing passes vacuously. `app/` is the product; a
    # count in double digits would mean the walk is broken, not that the tree
    # shrank.
    assert scanned > 200, scanned
    assert not offenders, (
        "The harness must not be importable from product code. It injects "
        "faults at real seams, replaces the clock and forces the dispatch "
        "backend to `record`, and every one of those is catastrophic inside a "
        "request handler and silent when it happens:\n  " + "\n  ".join(offenders)
    )


def test_the_harness_may_import_the_product_and_that_asymmetry_is_intended() -> None:
    """The rule is one-directional, and stating it stops somebody "fixing" it.

    A harness that could not import the product could not exercise it, which is
    the whole job. The asymmetry is safe for the reason the judge isolation test
    gives about its own: reaching FROM the harness INTO the product cannot make
    the product reach back.
    """
    text = (HARNESS_ROOT / "report.py").read_text(encoding="utf-8")
    assert "app.evaluation" in text


def test_the_harness_package_exists_where_the_contract_says_it_does() -> None:
    """HARNESS.md section 1 names `backend/harness/`, so the sweep above is
    about real files rather than about a package nobody wrote. Without this, a
    renamed or deleted package would make the isolation assertion pass by
    having nothing left to violate it."""
    assert HARNESS_ROOT.is_dir()
    modules = {path.stem for path in _python_files(HARNESS_ROOT)}
    assert {"__init__", "scenario", "run", "artifacts", "report", "baseline", "cli"} <= modules
    assert not (APP_ROOT / "harness").exists()
