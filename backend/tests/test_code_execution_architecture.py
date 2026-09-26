"""The domain never names Judge0, and nothing in the package can run code.

Asserted over the AST of every module under `app/`, the shape
`test_judge_isolation.py` and `test_harness_isolation.py` already use. Comments
never reach the AST and docstrings are skipped, so prose may explain the
sandbox while code may not reach it.
"""
from __future__ import annotations

import ast
import pathlib

APP = pathlib.Path(__file__).resolve().parents[1] / "app"
PACKAGE = APP / "services" / "code_execution"

#: The only modules allowed to NAME the sandbox product at all: the settings
#: that configure it, the port's one backend switch, and the adapter.
MAY_NAME_THE_SANDBOX = {
    APP / "core" / "config.py",
    PACKAGE / "provider.py",
    PACKAGE / "judge0.py",
}

#: Wire details of one sandbox, as STRING LITERALS (an identifier such as a
#: settings field is not a wire detail). Only its adapter may contain them.
WIRE_FIELDS = ("X-Auth-Token", "X-Auth-User", "language_id", "base64_encoded", "max_processes_and_or_threads")
WIRE_PATH_PREFIX = "/submissions"

#: Anything that could start a process or evaluate a string as code.
FORBIDDEN_IMPORTS = {"subprocess", "pty", "runpy", "ctypes", "multiprocessing", "pexpect", "shlex"}
FORBIDDEN_OS_CALLS = {"system", "popen", "execv", "execve", "execl", "execlp", "execvp", "spawnv", "spawnl", "fork", "forkpty"}
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


def _code_strings(tree: ast.AST) -> list[str]:
    """Every string literal, identifier and imported name that is CODE."""
    skip = _docstring_ids(tree)
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            out.append(node.value)
        elif isinstance(node, ast.Name):
            out.append(node.id)
        elif isinstance(node, ast.Attribute):
            out.append(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append(node.name)
        elif isinstance(node, ast.ImportFrom):
            out.append(node.module or "")
            out.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            out.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.arg):
            out.append(node.arg)
        elif isinstance(node, ast.keyword) and node.arg:
            out.append(node.arg)
    return out


def test_only_the_adapter_the_port_switch_and_the_settings_name_the_sandbox() -> None:
    offenders: list[str] = []
    for path in _modules(APP):
        if path in MAY_NAME_THE_SANDBOX:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any("judge0" in text.lower() for text in _code_strings(tree)):
            offenders.append(str(path.relative_to(APP.parent)))
    assert not offenders, "code outside the adapter names the sandbox:\n  " + "\n  ".join(offenders)


def _string_literals(tree: ast.AST) -> list[str]:
    skip = _docstring_ids(tree)
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip
    ]


def test_only_the_adapter_knows_the_wire_format() -> None:
    offenders: list[str] = []
    for path in _modules(APP):
        if path == PACKAGE / "judge0.py":
            continue
        for literal in _string_literals(ast.parse(path.read_text(encoding="utf-8"))):
            if literal in WIRE_FIELDS or literal.startswith(WIRE_PATH_PREFIX):
                offenders.append(f"{path.relative_to(APP.parent)}: {literal!r}")
    assert not offenders, "\n  ".join(["sandbox wire details outside the adapter:"] + offenders)


def test_no_module_outside_the_package_imports_the_adapter_or_the_double() -> None:
    """Domain code imports the port; the adapter is chosen by `get_provider`."""
    private = {"app.services.code_execution.judge0", "app.services.code_execution.fake"}
    offenders: list[str] = []
    for path in _modules(APP):
        if PACKAGE in path.parents:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                names = {f"{module}.{a.name}" for a in node.names} | {module}
                if names & private:
                    offenders.append(str(path.relative_to(APP.parent)))
            elif isinstance(node, ast.Import):
                if {a.name for a in node.names} & private:
                    offenders.append(str(path.relative_to(APP.parent)))
    assert not offenders, "modules import the adapter or the double directly:\n  " + "\n  ".join(offenders)


def test_nothing_in_the_package_can_start_a_process_or_evaluate_code() -> None:
    """Candidate code runs in the sandbox and nowhere else. The double included."""
    offenders: list[str] = []
    for path in _modules(PACKAGE):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        relative = path.relative_to(APP.parent)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in FORBIDDEN_IMPORTS:
                        offenders.append(f"{relative}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").split(".")[0] in FORBIDDEN_IMPORTS:
                    offenders.append(f"{relative}: from {node.module}")
                if node.module == "os" and {a.name for a in node.names} & FORBIDDEN_OS_CALLS:
                    offenders.append(f"{relative}: from os import a process call")
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id in FORBIDDEN_BUILTINS:
                    offenders.append(f"{relative}: {func.id}(...)")
                if (
                    isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "os"
                    and (func.attr in FORBIDDEN_OS_CALLS or func.attr.startswith(("exec", "spawn")))
                ):
                    offenders.append(f"{relative}: os.{func.attr}(...)")
    assert not offenders, "\n  ".join(["an execution path exists inside the application:"] + offenders)


def test_the_sweep_would_catch_a_violation() -> None:
    """Mutation check, in process: the detectors fire on the shapes they target."""
    planted = ast.parse("import subprocess\nsubprocess.run(['x'])\nheaders = {'X-Auth-Token': t}\n")
    assert "subprocess" in _code_strings(planted)
    assert "X-Auth-Token" in _string_literals(planted)
    documented = ast.parse('"""Judge0 is mentioned in prose only."""\nx = 1\n')
    assert not any("judge0" in s.lower() for s in _code_strings(documented))
