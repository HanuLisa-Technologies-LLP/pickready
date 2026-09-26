"""The grading pipeline runs ONE WAY, and the import graph says so (PLAN-p5 WP5-D).

    evidence (1)  ->  grading / Miti (2)  ->  composition / Siddhi (3)  ->  persistence (4)

`functional_assessment` used to be one 2,700 line module in which scoring,
prose, the gate and an UPDATE of the delivered report shared state, so a
change to one could reach the others by any route. It is now an orchestrator
over four stage modules, and this test holds the shape:

  * a stage imports only `types`, the stages BEFORE it and shared leaves,
    never a later stage and never `functional_assessment`;
  * Miti (the grading authority) imports no Siddhi and no later stage, so a
    report concern can never feed back into a grade;
  * Siddhi (the writer) imports no persistence, so composing a report can
    never write one;
  * `functional_assessment` makes no model call and no database write of its
    own: the stages do.

FUNCTION-LEVEL IMPORTS COUNT. This repository breaks import cycles by moving
an import inside a function, which is exactly how a back edge would hide, so
the walk is over every `Import` node in the tree, not the module prologue.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

APP = pathlib.Path(__file__).resolve().parents[1] / "app"
PIPELINE = APP / "services" / "assessment_pipeline"

#: Stage order. `validation` is stage 1's helper (the application's own words).
STAGES: dict[str, int] = {
    "evidence": 1,
    "validation": 1,
    "grading": 2,
    "composition": 3,
    "persistence": 4,
}
PACKAGE = "app.services.assessment_pipeline"
ORCHESTRATOR = "app.services.functional_assessment"


def _imports(path: pathlib.Path) -> set[str]:
    """Every module this file imports, at ANY depth, as dotted names; a
    `from package import name` also yields `package.name` so a submodule
    imported by name is seen."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return found


def _stage_of(module: str) -> int | None:
    if not module.startswith(PACKAGE + "."):
        return None
    name = module[len(PACKAGE) + 1 :].split(".")[0]
    return STAGES.get(name)


def test_every_stage_module_is_classified() -> None:
    """A new module in the package must be given a place in the order, or it
    is a stage nothing constrains."""
    present = {
        path.stem for path in PIPELINE.glob("*.py") if path.stem not in ("__init__", "types")
    }
    assert present == set(STAGES), sorted(present ^ set(STAGES))


@pytest.mark.parametrize("stage", sorted(STAGES))
def test_a_stage_imports_no_later_stage_and_not_the_orchestrator(stage: str) -> None:
    own = STAGES[stage]
    offenders = []
    for module in _imports(PIPELINE / f"{stage}.py"):
        if module == ORCHESTRATOR or module.startswith(ORCHESTRATOR + "."):
            offenders.append(module)
        target = _stage_of(module)
        if target is not None and target > own:
            offenders.append(module)
    assert not offenders, f"{stage} reaches forward: {sorted(offenders)}"


def _package_imports(package: str) -> dict[str, set[str]]:
    return {
        str(path.relative_to(APP)): _imports(path)
        for path in (APP / "services" / package).rglob("*.py")
        if "__pycache__" not in path.parts
    }


def test_miti_imports_no_siddhi_and_no_later_stage() -> None:
    offenders = []
    for path, modules in _package_imports("miti").items():
        for module in modules:
            if module.startswith("app.services.siddhi") or module == ORCHESTRATOR:
                offenders.append(f"{path}: {module}")
            stage = _stage_of(module)
            if stage is not None and stage > STAGES["evidence"]:
                offenders.append(f"{path}: {module}")
    assert not offenders, offenders


def test_siddhi_imports_no_persistence_and_not_the_orchestrator() -> None:
    offenders = []
    for path, modules in _package_imports("siddhi").items():
        for module in modules:
            if module == ORCHESTRATOR or module.startswith(PACKAGE + ".persistence"):
                offenders.append(f"{path}: {module}")
    assert not offenders, offenders


def test_the_orchestrator_calls_no_model_and_writes_nothing_itself() -> None:
    source = (APP / "services" / "functional_assessment.py").read_text(encoding="utf-8")
    for forbidden in (
        "llm_router",
        "session.add(",
        "insert(",
        "update(",
        "delete(",
        "StateGraph",
    ):
        assert forbidden not in source, forbidden


def test_the_direction_check_is_not_vacuous() -> None:
    """The guard on the guard: the walk sees a FUNCTION-LEVEL import, and a
    planted forward edge is classified as one."""
    nested = ast.parse(
        "def f():\n    from app.services.assessment_pipeline import persistence\n"
    )
    seen = {
        f"{node.module}.{alias.name}"
        for node in ast.walk(nested)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert any((_stage_of(module) or 0) > STAGES["grading"] for module in seen)
    # And the real stages do reach backwards, so the check is reading them.
    assert any(_stage_of(module) == 1 for module in _imports(PIPELINE / "grading.py")) or any(
        module.endswith(".types") for module in _imports(PIPELINE / "grading.py")
    )
