"""Inspect mounted routes across FastAPI's flat and nested router layouts."""
from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from fastapi.routing import APIRoute, APIWebSocketRoute


def mounted_routes(
    routes: Any, prefix: str = "", inherited: tuple[Callable, ...] = ()
) -> Iterator[tuple[str, APIRoute | APIWebSocketRoute, tuple[Callable, ...]]]:
    """Yield published paths, endpoints, and router-level dependencies."""
    for route in routes:
        if isinstance(route, (APIRoute, APIWebSocketRoute)):
            yield prefix + route.path, route, inherited
            continue

        context = getattr(route, "include_context", None)
        if context is not None:
            dependencies = tuple(
                call
                for dependency in (getattr(context, "dependencies", None) or ())
                if (call := getattr(dependency, "dependency", None)) is not None
            )
            yield from mounted_routes(
                context.included_router.routes,
                prefix + (getattr(context, "prefix", "") or ""),
                inherited + dependencies,
            )
            continue

        nested = getattr(route, "routes", None)
        if nested:
            yield from mounted_routes(
                nested, prefix + (getattr(route, "prefix", "") or ""), inherited
            )
