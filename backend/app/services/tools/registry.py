"""What a tool IS, and the one table that knows they exist.

A `ToolSpec` is deliberately data rather than a base class to subclass. The
executor reads it; the permission matrix names it; a test enumerates it. That
is only possible while a tool's timeout, idempotence and cache policy are
fields somebody can read off a page, rather than behaviour buried in whichever
handler happened to implement them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from pydantic import BaseModel

# A handler receives its VALIDATED input model and an optional database
# session, and returns anything its output model accepts. It never receives the
# raw payload: a handler that re-parses its own input is a second, divergent
# copy of the schema.
Handler = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class ToolSpec:
    """One callable unit of agent work, and everything the executor needs.

    `idempotent` is the load-bearing field. It is what permits caching at all:
    a tool declared idempotent promises that the same inputs yield the same
    outputs for `cache_ttl_seconds`, and every built-in read tool below is a
    pure database read that satisfies it. A write tool declaring itself
    idempotent would serve a stale result to the caller that just changed the
    row, so the default is False and the cache is skipped entirely unless a
    positive TTL is also set.
    """

    name: str
    handler: Handler
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    description: str
    #: Same inputs -> same outputs. Required before anything is cached.
    idempotent: bool = False
    #: Per-ATTEMPT ceiling. The executor's deadline bounds the total.
    timeout_seconds: float = 5.0
    #: 0 disables caching regardless of `idempotent`.
    cache_ttl_seconds: int = 0
    #: Attempts, including the first. 1 means no retry.
    max_attempts: int = 2
    #: Wall clock across every attempt, checked BEFORE each one.
    deadline_seconds: float = 12.0
    #: The handler takes `session=`. False for pure computation.
    needs_session: bool = True


_REGISTRY: dict[str, ToolSpec] = {}


def register(spec: ToolSpec) -> ToolSpec:
    """Add a tool. A duplicate name is a programming error, not a replacement.

    Silently overwriting would make the resolved tool depend on module import
    order, which is exactly the class of bug that stays invisible until a
    deploy reorders something.
    """
    if spec.name in _REGISTRY:
        raise ValueError(f"tool already registered: {spec.name}")
    if spec.max_attempts < 1:
        raise ValueError(f"{spec.name}: max_attempts must be at least 1")
    if spec.cache_ttl_seconds and not spec.idempotent:
        raise ValueError(
            f"{spec.name}: only an idempotent tool may declare a cache TTL"
        )
    _REGISTRY[spec.name] = spec
    return spec


def get(name: str) -> ToolSpec | None:
    return _REGISTRY.get(name)


def names() -> frozenset[str]:
    return frozenset(_REGISTRY)


def specs() -> tuple[ToolSpec, ...]:
    return tuple(_REGISTRY[name] for name in sorted(_REGISTRY))


def _reset_for_tests() -> dict[str, ToolSpec]:
    """Snapshot-and-clear, for a test that registers a throwaway tool."""
    snapshot = dict(_REGISTRY)
    _REGISTRY.clear()
    return snapshot


def _restore_for_tests(snapshot: dict[str, ToolSpec]) -> None:
    _REGISTRY.clear()
    _REGISTRY.update(snapshot)


def describe() -> list[dict[str, Any]]:
    """The registry as plain data, for an operator view or a test."""
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "idempotent": spec.idempotent,
            "timeout_seconds": spec.timeout_seconds,
            "cache_ttl_seconds": spec.cache_ttl_seconds,
            "max_attempts": spec.max_attempts,
            "deadline_seconds": spec.deadline_seconds,
        }
        for spec in specs()
    ]
