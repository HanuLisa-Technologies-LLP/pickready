"""No route asks for a candidate and a staff member at the same time.

THE DEFECT CLASS
----------------
Every `/bgv/me*` route declared `get_current_user` (it accepts the OWNER and
ORG audiences only) beside `get_candidate_db` (which itself requires the
CANDIDATE audience through `get_current_candidate`). A token carries one
audience, so no request could satisfy both: every real candidate got a 401 and
Employment History was unreachable. The suite never noticed, because every
test overrode BOTH dependencies and a dependency override answers whatever the
test says.

A route like that is not insecure, it is DEAD, and dead in a way only a real
browser finds. So this sweep reads the resolved dependency tree of every route
FastAPI serves, sub-dependencies included, and refuses one that reaches both a
candidate-audience principal or session and a staff-audience one.

`get_current_any` (`/auth/me`, `/auth/logout`) and `get_public_db` (tokenized
public links) belong to neither side and are allowed anywhere.

Mutation check: restoring `get_current_user` on any `/bgv/me*` route fails
`test_no_route_mixes_candidate_and_staff_sign_in`, naming the route.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator

from fastapi import APIRouter, Depends, FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, APIWebSocketRoute

from app.api.deps import (
    get_candidate_db,
    get_current_candidate,
    get_current_user,
    get_optional_candidate,
    get_superadmin_db,
    get_tenant_db,
)
from app.main import app

#: Dependencies that only a CANDIDATE-audience token satisfies.
CANDIDATE_SIDE: frozenset[Callable] = frozenset(
    {get_current_candidate, get_candidate_db, get_optional_candidate}
)

#: Dependencies that only a staff (OWNER or ORG audience) token satisfies.
STAFF_SIDE: frozenset[Callable] = frozenset(
    {get_current_user, get_tenant_db, get_superadmin_db}
)


def _calls(dependant: Dependant) -> Iterator[Callable]:
    """Every callable in the resolved tree, sub-dependencies included.

    `require_capability(...)` returns a fresh closure per capability, which is
    why the walk goes through the TREE rather than matching names: the closure
    itself depends on `get_current_user` and `get_tenant_db`, and that is what
    is found.
    """
    for dependency in dependant.dependencies:
        if dependency.call is not None:
            yield dependency.call
        yield from _calls(dependency)


def _mixed_routes(application: FastAPI) -> list[str]:
    mixed: list[str] = []
    for route in application.routes:
        if not isinstance(route, (APIRoute, APIWebSocketRoute)):
            continue
        calls = set(_calls(route.dependant))
        candidate = sorted(call.__name__ for call in calls & CANDIDATE_SIDE)
        staff = sorted(call.__name__ for call in calls & STAFF_SIDE)
        if candidate and staff:
            methods = ",".join(sorted(getattr(route, "methods", None) or {"WS"}))
            mixed.append(f"{methods} {route.path}: {candidate} with {staff}")
    return sorted(mixed)


def _audiences(application: FastAPI) -> tuple[set[str], set[str]]:
    candidate_paths: set[str] = set()
    staff_paths: set[str] = set()
    for route in application.routes:
        if not isinstance(route, (APIRoute, APIWebSocketRoute)):
            continue
        calls = set(_calls(route.dependant))
        if calls & CANDIDATE_SIDE:
            candidate_paths.add(route.path)
        if calls & STAFF_SIDE:
            staff_paths.add(route.path)
    return candidate_paths, staff_paths


def test_no_route_mixes_candidate_and_staff_sign_in() -> None:
    mixed = _mixed_routes(app)
    assert not mixed, (
        "these routes need a candidate token AND a staff token, which no "
        "request can carry, so they answer 401 to everybody. A candidate "
        "route takes get_current_candidate with get_candidate_db; a staff "
        "route takes get_current_user with get_tenant_db or "
        f"get_superadmin_db: {mixed}"
    )


def test_the_sweep_sees_both_audiences() -> None:
    """A sweep that resolved no dependencies would pass for ever."""
    candidate_paths, staff_paths = _audiences(app)
    for path in (
        "/api/v1/bgv/me",
        "/api/v1/bgv/me/employers",
        "/api/v1/bgv/me/employers/{employment_id}/hr-email",
        "/api/v1/bgv/me/documents",
        "/api/v1/bgv/me/documents/{document_id}",
    ):
        assert path in candidate_paths, path
        assert path not in staff_paths, path
    # A capability-gated recruiter route is found through require_capability's
    # closure, one level down.
    assert "/api/v1/bgv/candidates/{candidate_id}" in staff_paths
    assert len(candidate_paths) >= 10 and len(staff_paths) >= 50


def test_the_sweep_catches_the_shape_it_forbids() -> None:
    """Feed it the exact defect, directly and one level down."""
    probe = FastAPI()
    router = APIRouter()

    @router.get("/direct")
    async def direct(
        user=Depends(get_current_user), session=Depends(get_candidate_db)
    ) -> None:
        return None

    async def nested_staff(user=Depends(get_current_user)) -> None:
        return None

    @router.get("/nested")
    async def nested(
        _=Depends(nested_staff), user=Depends(get_current_candidate)
    ) -> None:
        return None

    @router.get("/clean")
    async def clean(
        user=Depends(get_current_candidate), session=Depends(get_candidate_db)
    ) -> None:
        return None

    probe.include_router(router)
    found = _mixed_routes(probe)
    assert [entry.split(":")[0] for entry in found] == ["GET /direct", "GET /nested"]
