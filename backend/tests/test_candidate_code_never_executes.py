"""Candidate code never executes in any Vivekium process. A static check.

Candidate code, starter code and the model-written reference solution run in
the sandbox and nowhere else (PLAN-p4 principle 2). The sandbox is a separate
host reached over HTTP through `code_execution.judge0`; this module asks the
other half of the question: can ANY process this repository ships start a
program or evaluate a string?

The processes are the backend image (API, task Lambda, on-demand Fargate
tasks all run `backend/app`), the one zip Lambda (`lambda/`) and the
proctoring analysis service (`analysis-service/`). Every module under those
roots is parsed, and every EXECUTION CAPABILITY is found: an import of a
module that starts processes or runs code (`subprocess`, `pty`, `runpy`,
`multiprocessing`, `ctypes`, `pexpect`, `code`, `codeop`), a process call on
`os`, an asyncio subprocess, or the `exec`/`eval`/`compile`/`__import__`
builtins.

WHAT IS PINNED
--------------
1. The set of modules holding any capability is EXACTLY the allowlist below,
   each with its reason. A new one fails here, naming the file, so adding an
   execution path is a reviewed line with a justification attached rather
   than something that arrives with a dependency bump.
2. The one module on it today, the media pipeline's ffmpeg runner, cannot be
   handed candidate code: it runs ONE `subprocess.run`, on an argv list, with
   no shell, and every argv it is given is a list literal whose program is
   `ffmpeg` or `ffprobe`. It names nothing that holds code (no coding model,
   no coding service, no answer body).
3. The sweeps themselves are checked against planted violations, so a
   detector that quietly stopped matching fails rather than passing.

Comments never reach the AST and docstrings are skipped, so prose may say
"subprocess" while code may not reach it. `test_code_execution_architecture`
and `test_coding_key_confinement` pin the narrower rules for the coding
packages themselves; this is the whole-product statement.
"""
from __future__ import annotations

import ast
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]
ROOTS = (REPO / "backend" / "app", REPO / "lambda", REPO / "analysis-service")

MEDIA_RUNNER = "backend/app/services/video/processing.py"

#: The only modules in any shipped process that may start a program.
EXECUTION_ALLOWLIST: dict[str, str] = {
    MEDIA_RUNNER: (
        "ffmpeg and ffprobe over an assessment recording the pipeline itself "
        "downloaded: a fixed binary and a built argv, never a shell and never "
        "code a candidate wrote (asserted below)."
    ),
}

PROCESS_MODULES = frozenset({"subprocess", "pty", "runpy", "multiprocessing", "ctypes", "pexpect", "code", "codeop"})
OS_PROCESS_CALLS = frozenset({
    "system", "popen", "fork", "forkpty", "posix_spawn", "posix_spawnp",
    "execv", "execve", "execl", "execle", "execlp", "execlpe", "execvp", "execvpe",
    "spawnv", "spawnve", "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnvp", "spawnvpe",
})
EVALUATING_BUILTINS = frozenset({"exec", "eval", "compile", "__import__"})

#: Names a module holding candidate code would use. The media runner may use none.
CODE_HOLDING_NAMES = frozenset({
    "coding_assessment", "code_execution", "coding", "CodingRun", "CodingSubmission",
    "CodingQuestionKey", "answer_json", "reference_source", "starter_code",
})
MEDIA_BINARIES = frozenset({"ffmpeg", "ffprobe"})


def _modules() -> list[pathlib.Path]:
    found: list[pathlib.Path] = []
    for root in ROOTS:
        assert root.is_dir(), f"{root} is missing; a shipped process would go unswept"
        found.extend(
            p for p in root.rglob("*.py")
            if not {"__pycache__", ".venv", "node_modules", "tests"} & set(p.parts)
        )
    assert len(found) > 100, "almost nothing was found; this sweep would pass vacuously"
    return sorted(found)


def capabilities(tree: ast.AST) -> set[str]:
    """Every execution capability in a parsed module, as short labels."""
    hits: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            hits |= {f"import {a.name}" for a in node.names if a.name.split(".")[0] in PROCESS_MODULES}
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.split(".")[0] in PROCESS_MODULES:
                hits.add(f"from {module}")
            names = {a.name for a in node.names}
            if module == "os" and names & OS_PROCESS_CALLS:
                hits.add("from os import a process call")
            if module == "asyncio" and any(n.startswith("create_subprocess") for n in names):
                hits.add("from asyncio import create_subprocess")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in EVALUATING_BUILTINS:
                hits.add(f"{func.id}(...)")
            elif isinstance(func, ast.Attribute):
                if func.attr.startswith("create_subprocess"):
                    hits.add(f"{func.attr}(...)")
                if (
                    isinstance(func.value, ast.Name)
                    and func.value.id == "os"
                    and (func.attr in OS_PROCESS_CALLS or func.attr.startswith(("exec", "spawn")))
                ):
                    hits.add(f"os.{func.attr}(...)")
    return hits


def _relative(path: pathlib.Path) -> str:
    return path.relative_to(REPO).as_posix()


def test_only_the_allowlisted_modules_can_start_a_program_or_evaluate_code() -> None:
    capable: dict[str, set[str]] = {}
    for path in _modules():
        hits = capabilities(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        if hits:
            capable[_relative(path)] = hits
    unexpected = {path: sorted(hits) for path, hits in capable.items() if path not in EXECUTION_ALLOWLIST}
    assert not unexpected, (
        "a shipped process gained an execution path. Candidate code runs in the "
        f"sandbox only; justify it in EXECUTION_ALLOWLIST or remove it:\n  {unexpected}"
    )
    missing = sorted(set(EXECUTION_ALLOWLIST) - set(capable))
    assert not missing, f"allowlisted modules no longer execute anything; drop them: {missing}"


def _code_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            names.update((node.module or "").split("."))
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.update(alias.name.split("."))
    return names


def media_runner_violations(tree: ast.AST) -> list[str]:
    """Why the media runner could be handed code, or [] when it cannot."""
    problems: list[str] = []
    leaked = sorted(_code_names(tree) & CODE_HOLDING_NAMES)
    if leaked:
        problems.append(f"names something that holds candidate code: {leaked}")
    runner_calls = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "subprocess":
            if func.attr != "run":
                problems.append(f"subprocess.{func.attr} is not the one sanctioned call")
            if not node.args or not isinstance(node.args[0], ast.Name):
                problems.append("subprocess.run is not given its argv parameter")
            for keyword in node.keywords:
                if keyword.arg == "shell" and not (
                    isinstance(keyword.value, ast.Constant) and keyword.value.value is False
                ):
                    problems.append("subprocess.run may use a shell")
        if isinstance(func, ast.Name) and func.id == "_run_tool":
            runner_calls += 1
            argv = node.args[0] if node.args else None
            program = argv.elts[0] if isinstance(argv, ast.List) and argv.elts else None
            if not (isinstance(program, ast.Constant) and program.value in MEDIA_BINARIES):
                problems.append(f"line {node.lineno}: _run_tool is not given a literal ffmpeg or ffprobe argv")
    if not runner_calls:
        problems.append("no _run_tool call was found; the argv check would pass vacuously")
    return problems


def test_the_media_runner_runs_only_media_binaries_and_never_sees_code() -> None:
    path = REPO / MEDIA_RUNNER
    assert media_runner_violations(ast.parse(path.read_text(encoding="utf-8"))) == []


def test_the_sweeps_catch_what_they_are_for() -> None:
    """Mutation check, in process: each detector fires on the shape it targets."""
    planted = ast.parse(
        "import subprocess\nfrom os import system\nimport asyncio\n"
        "asyncio.create_subprocess_exec('python', path)\nexec(source)\nos.execvp('sh', [])\n"
    )
    assert capabilities(planted) >= {
        "import subprocess", "from os import a process call", "create_subprocess_exec(...)",
        "exec(...)", "os.execvp(...)",
    }
    documented = ast.parse('"""subprocess and exec are mentioned in prose only."""\nx = 1\n')
    assert capabilities(documented) == set()

    handed_code = ast.parse(
        "from app.models.coding import CodingRun\n"
        "def _run_tool(argv): return subprocess.run(argv, shell=True)\n"
        "def run(run): _run_tool(['python', run.source])\n"
    )
    problems = media_runner_violations(handed_code)
    assert any("holds candidate code" in p for p in problems)
    assert any("shell" in p for p in problems)
    assert any("ffmpeg or ffprobe" in p for p in problems)
