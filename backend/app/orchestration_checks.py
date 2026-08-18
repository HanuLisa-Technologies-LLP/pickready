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

from app.services.orchestration import router
from app.services.reasoning import planner
from app.services.tools import permissions, registry


def structural_invariants() -> list[str]:
    """Everything that must agree across the agent framework. Empty is healthy."""
    problems: list[str] = []

    problems.extend(router.validate_routes())

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
