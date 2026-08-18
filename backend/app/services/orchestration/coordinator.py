"""Running several agent tasks with dependencies, concurrently where possible.

THE WORKFLOW THIS EXISTS FOR
-----------------------------
Rank a job's candidates, write a report for each of the top five, then write
probes from each report. That is a dependency graph, and the parts of it that
can run at once genuinely should: five reports run serially is five times the
wall clock for no reason, and a recruiter is waiting.

WHY IT IS A DAG AND NOT A LIST
-------------------------------
Because "then" is doing two different jobs in that sentence. Probes for
candidate A depend on A's report and on nothing about candidate B, so a barrier
between the report phase and the probe phase would make every probe wait for the
slowest report. Declaring dependencies per node lets B's probes start while A's
report is still running.

FAILURE IS PER NODE, NOT PER GRAPH
-----------------------------------
One candidate's report failing must not cost the other four. A failed node marks
its dependents unreachable and everything else proceeds, which is the same
posture as the bulk resume upload: partial success beats discarding twenty-four
good results because the twenty-fifth PDF was unreadable.

CONCURRENCY IS BOUNDED
----------------------
Every node is database and provider work. Unbounded fan-out over fifty
candidates opens fifty connections and fifty provider calls, trips the router's
rate limits, and turns a slow page into an outage.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

#: Concurrent nodes. Matched to the connection pool rather than to the CPU: the
#: work is IO, and the pool is what actually runs out.
MAX_CONCURRENCY = 8

STATE_PENDING = "pending"
STATE_RUNNING = "running"
STATE_DONE = "done"
STATE_FAILED = "failed"
STATE_UNREACHABLE = "unreachable"


@dataclass
class Node:
    """One unit of work and what it waits for."""

    name: str
    run: Callable[[dict[str, Any]], Awaitable[Any]]
    depends_on: tuple[str, ...] = ()
    state: str = STATE_PENDING
    result: Any = None
    error: str | None = None


@dataclass
class GraphResult:
    results: dict[str, Any] = field(default_factory=dict)
    states: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def completed(self) -> int:
        return sum(1 for state in self.states.values() if state == STATE_DONE)


class CyclicGraph(ValueError):
    """Nodes depend on each other. An authoring error, refused before any work."""


def _validate(nodes: dict[str, Node]) -> None:
    for node in nodes.values():
        for dependency in node.depends_on:
            if dependency not in nodes:
                raise CyclicGraph(f"{node.name} depends on unknown node {dependency!r}")

    # Kahn's algorithm, purely as a cycle check. Refusing up front beats
    # deadlocking: a graph that never completes looks exactly like a slow one.
    indegree = {name: len(node.depends_on) for name, node in nodes.items()}
    queue = [name for name, degree in indegree.items() if degree == 0]
    seen = 0
    while queue:
        current = queue.pop()
        seen += 1
        for name, node in nodes.items():
            if current in node.depends_on:
                indegree[name] -= 1
                if indegree[name] == 0:
                    queue.append(name)
    if seen != len(nodes):
        raise CyclicGraph("the task graph contains a cycle")


async def run_graph(
    nodes: list[Node], *, max_concurrency: int = MAX_CONCURRENCY
) -> GraphResult:
    """Run every node whose dependencies succeeded. Never raises for a node."""
    by_name = {node.name: node for node in nodes}
    _validate(by_name)

    result = GraphResult()
    semaphore = asyncio.Semaphore(max(1, max_concurrency))

    async def execute(node: Node) -> None:
        async with semaphore:
            node.state = STATE_RUNNING
            try:
                node.result = await node.run(dict(result.results))
                node.state = STATE_DONE
                result.results[node.name] = node.result
            except Exception as exc:  # noqa: BLE001 -- isolated to this node
                node.state = STATE_FAILED
                node.error = f"{type(exc).__name__}: {exc}"
                result.errors[node.name] = node.error
                logger.warning(
                    "orchestration.node_failed node=%s err=%s",
                    node.name,
                    type(exc).__name__,
                )

    while True:
        failed_or_unreachable = {
            name
            for name, node in by_name.items()
            if node.state in (STATE_FAILED, STATE_UNREACHABLE)
        }
        for node in by_name.values():
            if node.state == STATE_PENDING and set(node.depends_on) & failed_or_unreachable:
                node.state = STATE_UNREACHABLE

        ready = [
            node
            for node in by_name.values()
            if node.state == STATE_PENDING
            and all(by_name[dep].state == STATE_DONE for dep in node.depends_on)
        ]
        if not ready:
            break
        await asyncio.gather(*(execute(node) for node in ready))

    result.states = {name: node.state for name, node in by_name.items()}
    return result
