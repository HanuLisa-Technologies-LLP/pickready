#!/usr/bin/env python
"""Refuse an unreachable module-level symbol in `backend/app/`.

spec-doc6 §10.1 and §11.3: "No dead code, delete rather than deprecate", with a
dead-code check in the blocking gate list.

WHY THIS IS DELIBERATELY NARROW
--------------------------------
A general dead-code detector on this codebase would be wrong far more often
than right, and a check that cries wolf gets an allowlist, and an allowlist gets
long, and then nothing is checked. Almost everything here is referenced by a
framework rather than by a caller: FastAPI resolves route handlers by
decorator, Celery by task name, SQLAlchemy by class, Alembic by the `upgrade`
and `downgrade` names, pytest by fixture. None of those references exist in any
source file this script could read.

So it looks for exactly one thing, and that thing is unambiguous:

    a module-level function in `app/` that is NOT decorated, is NOT re-exported
    through `__all__`, and whose name occurs nowhere in the repository except
    its own `def` line.

An undecorated function nothing names is not held by a framework, is not part
of a package's published surface, and is not called. It is dead. There is no
third reading.

Constants are checked the same way, and only when they are private
(`_LEADING_UNDERSCORE`): a public constant is part of a module's surface and may
legitimately exist for a reader, whereas a private one nothing reads is a
leftover.

WHAT IS DELIBERATELY NOT CHECKED
---------------------------------
Classes (SQLAlchemy models, Pydantic schemas, exception types held only by an
`except` clause in another package). Methods (an override, a protocol
implementation, a `__enter__`). Unused imports, which are `ruff`'s F401 and are
better reported by a linter that understands `__init__` re-exports. Entire
modules nothing imports, which is a real question this codebase has already
asked and answered by hand -- `app/scripts/worked_example.py` is the only
non-test importer of the whole Part A stack, and a script that said so as a
failure would be reporting a known architectural fact as a lint error.

Exit code 0 when nothing is dead, 1 otherwise, listing every finding.
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys

BACKEND = pathlib.Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
APP = BACKEND / "app"

#: Where a reference could legitimately live. Anything outside this is not
#: scanned, so a name used only in, say, a frontend string would still be
#: reported -- which is correct: a Python function is not called from
#: TypeScript.
SEARCH_ROOTS = (
    BACKEND / "app",
    BACKEND / "tests",
    BACKEND / "scripts",
    BACKEND / "alembic",
    REPO / "infra",
    REPO / "scripts",
)

SEARCH_SUFFIXES = {".py", ".sh", ".yml", ".yaml", ".cfg", ".toml", ".ini"}

#: Packages whose contents are resolved by a framework rather than by a caller.
FRAMEWORK_OWNED = (
    "app/api/",
    "app/models/",
    "app/schemas/",
    "app/workers/",
)

#: Names a framework calls by convention.
CONVENTIONAL = frozenset(
    {
        "main",
        "upgrade",
        "downgrade",
        "setup",
        "teardown",
        "handler",
        "lifespan",
    }
)


def _searchable_files() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for root in SEARCH_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in SEARCH_SUFFIXES:
                continue
            if "__pycache__" in path.parts:
                continue
            files.append(path)
    return files


_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _occurrences() -> dict[str, int]:
    """How often each identifier appears anywhere a reference could live.

    Tokenised once into a count, rather than a regex per candidate over one
    joined blob. The blob version is the obvious implementation and is
    quadratic: a thousand candidates against six megabytes takes minutes, and a
    check slow enough to be skipped locally is a check that only ever fails in
    CI.
    """
    counts: dict[str, int] = {}
    for path in _searchable_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for token in _IDENTIFIER.findall(text):
            counts[token] = counts.get(token, 0) + 1
    return counts


def _is_framework_owned(path: pathlib.Path) -> bool:
    relative = path.relative_to(BACKEND).as_posix()
    return any(relative.startswith(prefix) for prefix in FRAMEWORK_OWNED)


def _declared_all(tree: ast.Module) -> set[str]:
    exported: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "__all__":
                if isinstance(node.value, (ast.List, ast.Tuple)):
                    for element in node.value.elts:
                        if isinstance(element, ast.Constant) and isinstance(
                            element.value, str
                        ):
                            exported.add(element.value)
    return exported


def candidates() -> list[tuple[pathlib.Path, int, str, str]]:
    """(path, line, kind, name) for every symbol worth asking about."""
    found: list[tuple[pathlib.Path, int, str, str]] = []
    for path in sorted(APP.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        # A package `__init__` exists to re-export. Nothing in it is dead by
        # this script's definition, and everything in it looks it.
        if path.name == "__init__.py":
            continue
        if _is_framework_owned(path):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        exported = _declared_all(tree)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.decorator_list:
                    continue
                if node.name.startswith("__") or node.name in CONVENTIONAL:
                    continue
                if node.name in exported:
                    continue
                found.append((path, node.lineno, "function", node.name))
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if not isinstance(target, ast.Name):
                        continue
                    name = target.id
                    if not name.startswith("_") or name.startswith("__"):
                        continue
                    if not name.upper() == name:
                        continue
                    if name in exported:
                        continue
                    found.append((path, node.lineno, "constant", name))
    return found


def main() -> int:
    counts = _occurrences()
    found = candidates()
    dead: list[str] = []
    for path, line, kind, name in found:
        # One occurrence is the definition itself; anything more is a reference.
        if counts.get(name, 0) <= 1:
            dead.append(f"{path.relative_to(BACKEND).as_posix()}:{line} {kind} {name}")

    print(f"{len(found)} undecorated module-level symbols checked under app/")
    if not dead:
        print("No dead code.")
        return 0

    print(f"\n{len(dead)} symbol(s) referenced nowhere:")
    for entry in sorted(dead):
        print(f"  {entry}")
    print(
        "\nDelete rather than deprecate (spec-doc6 §10.1). If one of these is "
        "genuinely reached by something this script cannot see, that is a "
        "finding about the reachability, not a reason to widen the check."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
