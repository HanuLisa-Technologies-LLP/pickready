"""Which agent handles this task, and what it is allowed to cost.

WHY ROUTING IS A TABLE
----------------------
Six agents, one task type each today. A router for that could be a dict, and
that is essentially what this is -- the value is not the dispatch, it is that
routing, the permission grant and the cost ceiling are forced to agree. Before
this, an agent could be given a task whose tools it did not hold, and the
failure surfaced as a permission error deep inside a generative call rather than
as a refusal at the front door.

THE ROUTE IS CHECKED AGAINST THE PERMISSION MATRIX
---------------------------------------------------
`validate_routes` asserts every routed agent is one `tools.permissions` knows
about. It runs as a test, not at import: a startup crash on a config mistake is
worse than a red test, because it takes production down for a change that only
affects one task type.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.services.reliability import budget as budgeting
from app.services.tools import permissions

TASK_RANKING = "ranking"
TASK_PPI_REPORT = "ppi_report"
TASK_EMAIL = "email"
TASK_PROBE = "probe"
TASK_INTERVIEWER = "interviewer"
TASK_JOB_SETUP = "job_setup"

#: task type -> the agent that handles it.
ROUTES: dict[str, str] = {
    TASK_RANKING: permissions.AGENT_RANKING,
    TASK_PPI_REPORT: permissions.AGENT_PPI_REPORT,
    TASK_EMAIL: permissions.AGENT_EMAIL,
    TASK_PROBE: permissions.AGENT_PROBE,
    TASK_INTERVIEWER: permissions.AGENT_INTERVIEWER,
    TASK_JOB_SETUP: permissions.AGENT_JOB_SETUP,
}


class UnroutableTask(ValueError):
    """No agent handles this task type. Never guessed at."""


@dataclass(frozen=True)
class Route:
    task_type: str
    agent_type: str
    granted_tools: frozenset[str]
    cost_limit_usd: float


def route(task_type: str) -> Route:
    agent = ROUTES.get(task_type)
    if agent is None:
        raise UnroutableTask(f"no agent handles task type {task_type!r}")
    return Route(
        task_type=task_type,
        agent_type=agent,
        granted_tools=permissions.granted_tools(agent),
        cost_limit_usd=budgeting.COST_BUDGET_USD.get(
            task_type, budgeting.DEFAULT_COST_BUDGET_USD
        ),
    )


def validate_routes() -> list[str]:
    """Problems with the routing table, as readable strings. Empty is healthy."""
    problems: list[str] = []
    for task_type, agent in ROUTES.items():
        if agent not in permissions.AGENTS:
            problems.append(f"{task_type} routes to unknown agent {agent!r}")
        elif not permissions.granted_tools(agent):
            problems.append(f"{task_type} routes to {agent!r}, which holds no tools")
    for agent in permissions.AGENTS:
        if agent not in ROUTES.values():
            problems.append(f"agent {agent!r} handles no task type")
    return problems
