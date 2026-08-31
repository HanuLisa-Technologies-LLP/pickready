#!/usr/bin/env python
"""Refuse an import cycle in `backend/app/` (spec-doc6 §10.3, §11.3).

    "Module boundaries: no import cycles (CI-checked, since one was found
     before), no reaching into another package's internals, no circular agent
     dependencies."

WHAT THIS CHECKS THAT THE EXISTING GUARD DOES NOT
--------------------------------------------------
`tests/test_import_graph.py` pins the shape that makes a cycle FATAL: a
module-level statement reading an attribute off a module that may itself be
half-initialised. That guard is precise and it is not this one. It says nothing
about whether a cycle exists, only that if one forms it will not explode at
that particular site. A cycle with no such read is legal Python and still a
design problem: it means two modules cannot be reasoned about, tested or moved
independently, and the next person to add a module-level constant to either one
turns it into the `AttributeError: partially initialized module` this codebase
has already shipped twice.

WHAT COUNTS AS AN EDGE
-----------------------
Only imports that RUN AT IMPORT TIME:

  * module-scope `import x` / `from x import y`, and
  * nothing else.

Deliberately excluded, because none of them can close a runtime cycle:

  * imports inside a function or method. That is the documented remedy in this
    codebase -- `llm_router._load_key` imports `get_settings` inside the
    function with a comment naming the cycle -- and flagging the remedy would
    make the check argue with the fix.
  * imports under `if TYPE_CHECKING:`, which never execute.
  * imports inside `try:`/`except ImportError:` optional-dependency guards, for
    the same reason a function-scope import is excluded: they are conditional.

Exit code 0 when the graph is acyclic, 1 when it is not, printing every cycle
found rather than the first.
"""
from __future__ import annotations

import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "app"
PACKAGE = "app"


def module_name(path: pathlib.Path) -> str:
    """`app/services/llm_router.py` -> `app.services.llm_router`."""
    relative = path.relative_to(ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _runtime_import_nodes(tree: ast.Module) -> list[ast.stmt]:
    """Top-level import statements that actually execute on import."""
    nodes: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            nodes.append(node)
        elif isinstance(node, ast.Try):
            # An optional-dependency guard. Its body is conditional, so it
            # cannot be relied on to close a cycle and is not counted.
            continue
        elif isinstance(node, ast.If):
            # `if TYPE_CHECKING:` never runs. Any other module-level `if`
            # around an import is conditional for a reason of its own and is
            # treated the same way.
            continue
    return nodes


def _targets(node: ast.stmt, current: str, known: set[str]) -> set[str]:
    """The `app.*` modules one import statement actually depends on.

    `from app.services.rag import chunking` binds a SUBMODULE, and the
    dependency is on `app.services.rag.chunking`. It is not a dependency on
    `app.services.rag`'s own namespace, even though Python imports the parent
    package on the way past -- every submodule import does that, and counting
    it would make each package's own `__init__` re-export look like a cycle
    with each of its members. `from app.services.rag import RetrievedChunk`
    IS a dependency on the package's namespace, because that name only exists
    once `__init__` has finished running.
    """
    found: set[str] = set()
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name.split(".")[0] == PACKAGE:
                found.add(alias.name)
    elif isinstance(node, ast.ImportFrom):
        if node.level:
            # A relative import. Resolve it against the current package.
            base = current.split(".")
            # `from . import x` inside `app.services.foo` resolves against
            # `app.services`; level 2 strips one package more.
            anchor = base[: len(base) - node.level]
            module = ".".join(anchor + ([node.module] if node.module else []))
        else:
            module = node.module or ""
        if module.split(".")[0] != PACKAGE:
            return found
        for alias in node.names:
            submodule = f"{module}.{alias.name}"
            if submodule in known:
                found.add(submodule)
            else:
                found.add(module)
    return found


def build_graph() -> dict[str, set[str]]:
    files = sorted(APP.rglob("*.py"))
    known = {module_name(p) for p in files}
    graph: dict[str, set[str]] = {name: set() for name in known}
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:  # pragma: no cover -- a broken file is a
            print(f"could not parse {path}: {exc}", file=sys.stderr)
            raise
        name = module_name(path)
        for node in _runtime_import_nodes(tree):
            for target in _targets(node, name, known):
                if target in known and target != name:
                    graph[name].add(target)
    return graph


def find_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """Every strongly connected component larger than one node.

    Tarjan, iterative. Recursion would be the obvious implementation and would
    also be the one that dies on this graph's depth in a CI container with a
    small stack, which is a failure that reads as a check being flaky.
    """
    index_of: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    counter = 0
    components: list[list[str]] = []

    for root in sorted(graph):
        if root in index_of:
            continue
        work: list[tuple[str, list[str]]] = [(root, sorted(graph[root]))]
        index_of[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, successors = work[-1]
            if successors:
                nxt = successors.pop(0)
                if nxt not in index_of:
                    index_of[nxt] = low[nxt] = counter
                    counter += 1
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, sorted(graph[nxt])))
                elif nxt in on_stack:
                    low[node] = min(low[node], index_of[nxt])
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index_of[node]:
                component: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                if len(component) > 1:
                    components.append(sorted(component))
    # A one-node component that imports itself is still a cycle.
    for node, edges in graph.items():
        if node in edges:
            components.append([node])
    return components


def main() -> int:
    graph = build_graph()
    edges = sum(len(v) for v in graph.values())
    cycles = find_cycles(graph)

    print(f"{len(graph)} modules, {edges} import-time edges under {PACKAGE}/")
    if not cycles:
        print("No import cycle.")
        return 0

    print(f"\n{len(cycles)} import cycle(s):")
    for component in cycles:
        print(f"\n  cycle of {len(component)}:")
        for member in component:
            inside = sorted(graph[member] & set(component))
            print(f"    {member} -> {', '.join(inside)}")
    print(
        "\nA cycle is legal Python and still a defect: two modules in one "
        "cannot be reasoned about, tested or moved independently, and the "
        "next module-level constant added to either turns it into the "
        "'partially initialized module' AttributeError this codebase has "
        "already shipped twice. Break it by moving the import inside the "
        "function that needs it, as llm_router._load_key does."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
