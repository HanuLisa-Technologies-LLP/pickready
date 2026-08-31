"""Cross-package invariants nobody owns individually, checked in one place.

WHY HERE AND NOT IN EACH PACKAGE
---------------------------------
Every problem this file finds is a DISAGREEMENT between two packages that are
each individually correct. The routing table is fine; the permission matrix is
fine; a task routed to an agent holding none of the tools its plan calls for is
neither package's bug and is exactly the kind of gap that ships. Putting the
check inside either one would mean importing the other, which is how a cycle
starts.

It is called by `app/scripts/eval_agents.py` and by the test suite, and it
returns a list of readable strings rather than raising: an operator wants all
the problems at once, not the first one.
"""
from __future__ import annotations

import ast
import functools
import pathlib

from app.services.agents import identity
from app.services.orchestration import router
from app.services.reasoning import planner
from app.services.tools import permissions, registry

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
# this: does a request handler or a Celery task have any import path to the code
# a name claims. So the graph is computed statically, from `app/api/**`,
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
    """module -> the app modules it imports, read from the source with `ast`."""
    edges: dict[str, frozenset[str]] = {}
    for path in sorted(_APP.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # a file that cannot parse cannot import anything
            edges[_module_name(path)] = frozenset()
            continue
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


def halt_coverage() -> list[str]:
    """Every stage the enforcement layer can halt must be a stage the kill
    switch knows about. Empty is healthy.

    The two modules name their stages differently on purpose -- `pipeline_halt`
    names them after the AGENT (`sutra_matrix`), `provenance` names them after
    what HAPPENED (`tatva_matrix`) -- so the mapping between them is a table.
    A table that silently stopped matching would leave a stage UNHALTABLE while
    the code still read as though it were governed, which is the worst possible
    state for a kill switch: present, referenced, and inert.

    Resolved late, like everything else that touches `hiring`, and reported as
    a problem rather than raised when the halt module is absent.
    """
    from app.services.orchestration import activation, enforcement

    try:
        halt = activation.load("pipeline_halt")
    except activation.StageModuleMissing as exc:
        return [str(exc)]

    declared = set(halt.declared_stages())
    problems = [
        f"enforcement maps {stage!r} to halt stage {halt_stage!r}, which "
        f"RPN_PIPELINE_HALT does not declare; that stage cannot be halted"
        for stage, halt_stage in enforcement.HALT_STAGE_FOR.items()
        if halt_stage not in declared
    ]
    return problems


def unreachable_agent_modules() -> list[str]:
    """Agent identities whose live module nothing can reach. Empty is healthy.

    This is the invariant that was violated for the whole of the previous
    phase. It is here rather than in the identity table because the answer needs
    the whole import graph, and a naming table that depended on a static
    analyser would be a naming table nobody could read.
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


def structural_invariants() -> list[str]:
    """Everything that must agree across the agent framework. Empty is healthy."""
    problems: list[str] = []

    problems.extend(router.validate_routes())
    problems.extend(identity.validate_identities())
    problems.extend(unreachable_agent_modules())
    problems.extend(halt_coverage())

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

    # Every planned subtask that names a tool must be a tool the routed agent
    # actually holds. This is the check that catches a plan quietly calling
    # something it cannot call, which surfaces otherwise as a permission error
    # deep inside a generative step.
    for task_type, agent in router.ROUTES.items():
        granted = permissions.granted_tools(agent)
        for subtask in planner.plan(task_type, agent).order:
            if subtask in registered and subtask not in granted:
                problems.append(
                    f"{task_type} plans {subtask!r} but {agent!r} does not hold it"
                )

    return problems
