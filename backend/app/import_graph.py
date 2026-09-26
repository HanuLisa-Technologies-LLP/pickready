"""The static import graph of `app`, and the cross-package invariants it answers.

WHAT THIS FILE WAS
------------------
It was the orchestration checks module, which also checked the orchestration
router against the permission matrix, the reasoning planner's subtasks against
the routed agent's tools, and the enforcement layer's halt table against the
kill switch. All three of those subjects were deleted in the Vivekium release
(no route and no worker could reach any of them), so the checks about them went
with them. What survives is what live code still needs: the reachability walk
the isolation tests are built on, the agent-identity activation check, and the
tool-layer invariants Phase 5 wires the tools under.

WHY HERE AND NOT IN EACH PACKAGE
---------------------------------
Every problem this file finds is a DISAGREEMENT between two packages that are
each individually correct. The identity table is fine and the import graph is
fine; an agent name pointing at a module nothing reaches is neither package's
bug and is exactly the kind of gap that ships. Putting the check inside either
one would mean importing the other, which is how a cycle starts.

It is called by `app/scripts/eval_agents.py` and by the test suite, and it
returns a list of readable strings rather than raising: an operator wants all
the problems at once, not the first one.
"""
from __future__ import annotations

import ast
import functools
import pathlib

from app.services.agents import identity
from app.services.tools import permissions, registry
from app.services.tools.policy import RiskClass

# ── Reachability: what a route or a worker can actually get to ───────────────
#
# WHY THIS EXISTS.
#
# `services/agents/identity.py` pointed every Part A agent name at the OLD
# modules, so logs and A2A artifacts showed Bodha, Sutra, Yukti, Vaada, Miti and
# Siddhi succeeding while the three-layer framework in `hiring/`, `miti/` and
# `siddhi/` was imported by nothing. Every unit test passed. Every gate in
# `hiring/gates.py` was a real check that guarded nothing, because its only
# caller was `miti/pipeline.py`, which no route and no worker imports.
#
# The check that would have caught it is not a unit test of any module. It is
# this: does a request handler or a background task have any import path to the
# code a name claims. So the graph is computed statically, from `app/api/**`,
# `app/workers/**` and `app/main.py`, and the answer is DATA that both the test
# and `eval_agents.py` read.
#
# STATIC, AND DELIBERATELY SO. Importing the package to find out would answer a
# different question -- what pytest's import order happens to have loaded --
# which is the same ordering luck that hid the import-cycle defect for weeks.

_APP = pathlib.Path(__file__).resolve().parent

#: Where a user's request or a scheduled task enters the process. Everything
#: reachable from here is code that can run in production; everything else is
#: code that exists.
ENTRY_POINT_PREFIXES: tuple[str, ...] = ("app.api.", "app.workers.")
ENTRY_POINT_MODULES: tuple[str, ...] = ("app.main",)


def _module_name(path: pathlib.Path) -> str:
    return ".".join(("app",) + path.relative_to(_APP).with_suffix("").parts)


@functools.lru_cache(maxsize=1)
def _import_edges() -> dict[str, frozenset[str]]:
    """module -> the app modules it imports, read from the source with `ast`.

    A file that does not parse RAISES. It used to be read as importing nothing,
    which made a syntax error look like an unreachable module rather than a
    broken one, and a reachability answer built on a file nobody could read is
    not an answer.
    """
    edges: dict[str, frozenset[str]] = {}
    for path in sorted(_APP.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        targets: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app"):
                targets.add(node.module or "")
                # `from app.services.hiring import gates` names the module in
                # the alias, not in `node.module`. Missing this reads a package
                # import as reaching only the package's __init__, which is how
                # a reachability check quietly answers "no" for everything.
                targets.update(f"{node.module}.{alias.name}" for alias in node.names)
            elif isinstance(node, ast.Import):
                targets.update(a.name for a in node.names if a.name.startswith("app"))
        edges[_module_name(path)] = frozenset(targets)
    return edges


def _resolve(target: str, known: frozenset[str]) -> str | None:
    """A dotted import target to the module file that satisfies it, if ours."""
    if target in known:
        return target
    package_init = f"{target}.__init__"
    if package_init in known:
        return package_init
    return None


@functools.lru_cache(maxsize=1)
def reachable_modules() -> frozenset[str]:
    """Every app module transitively importable from a route, a worker or main.

    A package is reported both as `app.services.miti` and as
    `app.services.miti.__init__`, because callers name it either way and a
    reachability answer that depends on the spelling is one nobody trusts.
    """
    edges = _import_edges()
    known = frozenset(edges)
    seen: set[str] = set()
    stack = [
        module
        for module in edges
        if module.startswith(ENTRY_POINT_PREFIXES) or module in ENTRY_POINT_MODULES
    ]
    while stack:
        module = stack.pop()
        if module in seen:
            continue
        seen.add(module)
        for target in edges.get(module, ()):
            resolved = _resolve(target, known)
            if resolved and resolved not in seen:
                stack.append(resolved)
    # Report packages under their bare name as well as their `__init__` form.
    return frozenset(seen) | frozenset(
        module[: -len(".__init__")] for module in seen if module.endswith(".__init__")
    )


def unreachable_agent_modules() -> list[str]:
    """Agent identities whose live module nothing can reach. Empty is healthy.

    This is the invariant that was violated for the whole of spec-doc5. It is
    here rather than in the identity table because the answer needs the whole
    import graph, and a naming table that depended on a static analyser would be
    a naming table nobody could read.
    """
    problems: list[str] = []
    reachable = reachable_modules()
    for agent_id, status in identity.activation_status(reachable).items():
        for module in status["live_but_unreachable"]:  # type: ignore[union-attr]
            problems.append(
                f"{agent_id} says it is implemented by {module!r}, which no route "
                "or worker can reach. A name pointing at unreachable code makes "
                "every log line and artifact claim work that cannot have happened."
            )
        unmapped = status["activated_but_unmapped"]
        if unmapped:
            problems.append(
                f"{agent_id}'s Part A implementation is reachable ({unmapped}) and "
                f"{agent_id}.implemented_by names none of it, so every log line "
                "and artifact for this agent still points at the module it "
                "replaced. Move it across in the same commit that activates it."
            )
    return problems


def tool_layer_problems() -> list[str]:
    """What must hold between the tool registry and the permission matrix.

    THE READ-ONLY INVARIANT IS WHY THE ACTION LEDGER COULD BE DELETED. The
    `agent_actions` package was a side-effect ledger (idempotency keys, an
    UNKNOWN outcome resolved by reading back) for tools that change something.
    Every registered tool is `RiskClass.READ`, so nothing in the tool layer has
    a side effect to ledger, and the package was deleted in the Vivekium
    release. The first tool that writes must bring a ledger with it; this check
    is what makes that a failing build rather than a discovery.
    """
    problems: list[str] = []
    registered = registry.names()
    for agent, granted in permissions.AGENT_TOOLS.items():
        unknown = granted - registered
        if unknown:
            problems.append(f"{agent} is granted unregistered tools: {sorted(unknown)}")

    for name in registered:
        if not permissions.agents_holding(name):
            problems.append(f"tool {name!r} is registered but no agent holds it")

    for spec in registry.specs():
        if spec.timeout_seconds > spec.deadline_seconds:
            problems.append(
                f"tool {spec.name!r} cannot finish one attempt inside its deadline"
            )
        if spec.cache_ttl_seconds and not spec.idempotent:
            problems.append(f"tool {spec.name!r} caches without declaring idempotence")
        if spec.risk is not RiskClass.READ:
            problems.append(
                f"tool {spec.name!r} declares risk {spec.risk.value!r}. Every tool "
                "has been a bounded read since the action ledger was deleted; a "
                "tool with a side effect needs an idempotency key from stable "
                "logical inputs and an UNKNOWN outcome resolved by reading back, "
                "and there is no ledger to provide either."
            )
    return problems


def structural_invariants() -> list[str]:
    """Everything that must agree across the agent framework. Empty is healthy."""
    problems: list[str] = []
    problems.extend(identity.validate_identities())
    problems.extend(unreachable_agent_modules())
    problems.extend(tool_layer_problems())
    return problems
