"""Routing one task to its agent, and running many tasks that depend on others.

Routing is a table that a test forces to agree with the tool permission matrix.
Coordination is a DAG with bounded concurrency, per-node failure isolation, and
a cycle check that refuses before any work rather than deadlocking.
"""
from __future__ import annotations

from app.services.orchestration import coordinator, router
from app.services.orchestration.coordinator import (
    MAX_CONCURRENCY,
    STATE_DONE,
    STATE_FAILED,
    STATE_UNREACHABLE,
    CyclicGraph,
    GraphResult,
    Node,
    run_graph,
)
from app.services.orchestration.router import ROUTES, Route, UnroutableTask, route, validate_routes

__all__ = [
    "CyclicGraph",
    "GraphResult",
    "MAX_CONCURRENCY",
    "Node",
    "ROUTES",
    "Route",
    "STATE_DONE",
    "STATE_FAILED",
    "STATE_UNREACHABLE",
    "UnroutableTask",
    "coordinator",
    "route",
    "router",
    "run_graph",
    "validate_routes",
]
