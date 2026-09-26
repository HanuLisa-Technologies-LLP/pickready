"""The coding answer key has ONE reader and writer, and no road to a client.

Asserted over the AST of every module under `app/`, the shape
`test_code_execution_architecture.py` and `test_judge_isolation.py` use.
Docstrings and comments never reach the checks, so prose may explain the
answer key while code may not reach it.

What is pinned, and why each is a separate assertion:

1. Only `coding_assessment/keys.py` (and the model that declares the table)
   may NAME `CodingQuestionKey` or the `coding_question_keys` table. A second
   reader is a second place a hidden test can be serialised from.
2. No route and no response schema imports the keys module at all.
3. No response schema declares a field called after a part of the key. A
   field is how a leak starts, long before a value is put in it.
4. Nothing on the generation or key path can start a process or evaluate a
   string: the model-written reference solution runs in the sandbox only.
5. Every dataclass field that holds part of the key is `repr=False`, so an
   exception message or a debugging print cannot quote a test.
"""
from __future__ import annotations

import ast
import dataclasses
import pathlib

from app.services.assessment_formats import coding_generation
from app.services.coding_assessment import keys as coding_keys

APP = pathlib.Path(__file__).resolve().parents[1] / "app"
KEYS_MODULE = APP / "services" / "coding_assessment" / "keys.py"

#: May name the table or its model in code.
MAY_NAME_THE_KEY = {
    KEYS_MODULE,
    APP / "models" / "coding.py",
    # The export line that puts the table on `Base.metadata`.
    APP / "models" / "__init__.py",
}

#: Field names that ARE the answer key. No response schema may declare one.
KEY_FIELD_NAMES = {"hidden_tests", "hidden_tests_json", "reference_solution", "reference_source"}

#: The modules on the generation and key path, beside `code_execution` (which
#: `test_code_execution_architecture.py` already sweeps).
EXECUTION_FREE = [
    *sorted((APP / "services" / "coding_assessment").glob("*.py")),
    APP / "services" / "assessment_formats" / "coding_generation.py",
    APP / "models" / "coding.py",
]
FORBIDDEN_IMPORTS = {"subprocess", "pty", "runpy", "ctypes", "multiprocessing", "pexpect", "shlex", "os"}
FORBIDDEN_BUILTINS = {"exec", "eval", "compile", "__import__"}


def _modules(root: pathlib.Path) -> list[pathlib.Path]:
    found = sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)
    assert found, f"no modules under {root}; this sweep would pass vacuously"
    return found


def _docstring_ids(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
                ids.add(id(body[0].value))
    return ids


def _names_the_key(tree: ast.AST) -> bool:
    skip = _docstring_ids(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "CodingQuestionKey":
            return True
        if isinstance(node, ast.Attribute) and node.attr == "CodingQuestionKey":
            return True
        if isinstance(node, ast.ImportFrom) and any(a.name == "CodingQuestionKey" for a in node.names):
            return True
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in skip
            and "coding_question_keys" in node.value
        ):
            return True
    return False


def _imports(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            found.add(module)
            found.update(f"{module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return found


def test_only_the_keys_module_names_the_answer_key() -> None:
    offenders = [
        str(path.relative_to(APP.parent))
        for path in _modules(APP)
        if path not in MAY_NAME_THE_KEY and _names_the_key(ast.parse(path.read_text(encoding="utf-8")))
    ]
    assert not offenders, "modules other than coding_assessment/keys name the answer key:\n  " + "\n  ".join(offenders)


def test_no_route_or_schema_imports_the_keys_module() -> None:
    private = {"app.services.coding_assessment.keys", "app.models.coding.CodingQuestionKey"}
    offenders: list[str] = []
    for root in (APP / "api", APP / "schemas"):
        for path in _modules(root):
            imported = _imports(ast.parse(path.read_text(encoding="utf-8")))
            if imported & private:
                offenders.append(str(path.relative_to(APP.parent)))
    assert not offenders, "a route or schema reaches the answer key:\n  " + "\n  ".join(offenders)


def test_no_response_schema_declares_a_field_named_after_the_key() -> None:
    offenders: list[str] = []
    for path in _modules(APP / "schemas"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.target.id in KEY_FIELD_NAMES:
                    offenders.append(f"{path.relative_to(APP.parent)}: {node.target.id}")
    assert not offenders, "\n  ".join(["response schemas declare answer-key fields:"] + offenders)


def test_nothing_on_the_generation_path_can_start_a_process_or_evaluate_code() -> None:
    offenders: list[str] = []
    for path in EXECUTION_FREE:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        relative = path.relative_to(APP.parent)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                offenders += [f"{relative}: import {a.name}" for a in node.names if a.name.split(".")[0] in FORBIDDEN_IMPORTS]
            elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in FORBIDDEN_IMPORTS:
                offenders.append(f"{relative}: from {node.module}")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_BUILTINS:
                offenders.append(f"{relative}: {node.func.id}(...)")
    assert not offenders, "\n  ".join(["an execution path exists on the coding generation path:"] + offenders)


def _hidden(cls: type, *names: str) -> list[str]:
    fields = {f.name: f for f in dataclasses.fields(cls)}
    return [name for name in names if fields[name].repr]


def test_every_field_that_holds_the_key_is_kept_out_of_a_repr() -> None:
    assert _hidden(coding_keys.HiddenTest, "stdin", "expected_stdout") == []
    assert _hidden(coding_keys.AnswerKey, "tests") == []
    assert _hidden(
        coding_generation.CodingQuestionDraft, "hidden_tests", "reference_source", "expected_approach"
    ) == []
    assert _hidden(coding_generation._Parsed, "hidden", "reference_source", "expected_approach") == []


def test_the_sweeps_would_catch_a_violation() -> None:
    """Mutation check, in process: each detector fires on the shape it targets."""
    assert _names_the_key(ast.parse("from app.models.coding import CodingQuestionKey\n"))
    assert _names_the_key(ast.parse("q = text('SELECT * FROM coding_question_keys')\n"))
    assert not _names_the_key(ast.parse('"""Reads coding_question_keys, in prose only."""\nx = 1\n'))
    assert "app.services.coding_assessment.keys" in _imports(
        ast.parse("from app.services.coding_assessment import keys\n")
    )
