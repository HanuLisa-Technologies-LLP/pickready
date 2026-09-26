"""The hash fallback is GONE, and a model failure is "not assessed" (WP5-B).

`functional_assessment._stable_score` hashed a seed into 45..94 whenever the
rubric scorer could not answer, and the report carried a plausible grade
written by a hash: measured over 20,000 seeds, 70% of hashed inputs graded
Moderately Matching or better. Miti's item stage replaced it, and a failed
evaluation is now `not_assessed` with no score at all.

This sweep keeps it gone. It reads the AST, not the prose: docstrings and
comments that RECORD the deletion are history and must survive, while any
executable reference (a definition, a call, an import of the name) fails.
The string `deterministic_fallback` is refused as a value written by the live
scoring path, where it was the report's scoring mode for a hashed run.
"""
from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"

#: The live scoring path: nothing here may write the retired mode.
_SCORING_PATH = (
    APP / "services" / "functional_assessment.py",
    APP / "services" / "miti",
    APP / "services" / "assessment_pipeline",
    APP / "workers",
    APP / "api",
)


def _python_files(root: Path):
    if root.is_file():
        yield root
        return
    yield from (path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _executable_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            names.add(node.name.split(".")[-1])
            if node.asname:
                names.add(node.asname)
    return names


def _string_constants(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    }


def test_no_module_in_the_app_defines_or_calls_the_hash_scorer() -> None:
    offenders = sorted(
        str(path.relative_to(APP))
        for path in _python_files(APP)
        if "_stable_score" in _executable_names(path)
    )
    assert not offenders, f"the hash fallback is back: {offenders}"


def test_the_scoring_path_never_writes_the_retired_fallback_mode() -> None:
    offenders = sorted(
        str(path.relative_to(APP))
        for root in _SCORING_PATH
        for path in _python_files(root)
        if any("deterministic_fallback" in value for value in _string_constants(path))
    )
    assert not offenders, f"a live scorer writes deterministic_fallback: {offenders}"


def test_miti_and_the_orchestrator_import_no_hash() -> None:
    """A grade is judged or it is not assessed. Nothing in the grading path has
    a use for a hash of the candidate's answer."""
    for root in (APP / "services" / "miti", APP / "services" / "functional_assessment.py"):
        for path in _python_files(root):
            assert "hashlib" not in _executable_names(path), path.name


def test_the_sweep_is_not_vacuous() -> None:
    """A sweep over zero files passes for ever."""
    assert len(list(_python_files(APP / "services" / "miti"))) >= 8
    assert (APP / "services" / "functional_assessment.py").is_file()
