"""Every Updates-feed link leads to a page that exists.

`ASSESSMENT_INVITED` and `ASSESSMENT_STARTED` linked to `/portal/assessments`,
and the frontend has no such page: only `/portal/assessments/[link_id]`. The
feed rendered a working-looking link that 404'd, on the one surface that
exists because email might not arrive. Nothing caught it because nothing
compared the catalogue with the route tree.

This resolves every `link_path` in `services/candidate_updates.TEMPLATES`
against `frontend/app` the way Next.js's App Router does: a `(group)` folder
adds no segment, a `[param]` folder matches any one segment, and a route
exists where a `page.tsx` is. The query string is not part of the route.
"""
from __future__ import annotations

import pathlib
import uuid
from urllib.parse import urlsplit

import pytest

from app.services import candidate_updates


def _find_frontend_app() -> pathlib.Path | None:
    """`frontend/app`, or None inside the backend container.

    The same convention as `test_platform_audit._find_frontend`: the frontend
    tree is legitimately absent in the backend image and CI has both trees.
    """
    for parent in pathlib.Path(__file__).resolve().parents:
        candidate = parent / "frontend" / "app"
        if candidate.is_dir():
            return candidate
    return None


FRONTEND_APP = _find_frontend_app()


def _children(directory: pathlib.Path) -> list[pathlib.Path]:
    """Folders reachable one URL segment down, with route groups flattened."""
    found: list[pathlib.Path] = []
    for child in directory.iterdir():
        if not child.is_dir():
            continue
        if child.name.startswith("(") and child.name.endswith(")"):
            found.extend(_children(child))
        else:
            found.append(child)
    return found


def _has_page(directory: pathlib.Path) -> bool:
    if (directory / "page.tsx").is_file():
        return True
    return any(
        _has_page(child)
        for child in directory.iterdir()
        if child.is_dir() and child.name.startswith("(") and child.name.endswith(")")
    )


def route_exists(path: str, root: pathlib.Path | None = None) -> bool:
    root = root or FRONTEND_APP
    assert root is not None
    segments = [part for part in urlsplit(path).path.split("/") if part]
    frontier = [root]
    for segment in segments:
        nxt: list[pathlib.Path] = []
        for directory in frontier:
            for child in _children(directory):
                dynamic = child.name.startswith("[") and child.name.endswith("]")
                if dynamic or child.name == segment:
                    nxt.append(child)
        if not nxt:
            return False
        frontier = nxt
    return any(_has_page(directory) for directory in frontier)


def _filled(link_path: str) -> str:
    return link_path.format(link_id=uuid.uuid4(), job_id=uuid.uuid4())


@pytest.mark.parametrize("kind", candidate_updates.KINDS)
def test_every_feed_link_resolves_to_a_frontend_page(kind: str) -> None:
    template = candidate_updates.TEMPLATES[kind]
    if template.link_path is None or FRONTEND_APP is None:
        return
    assert template.link_path.startswith("/"), "a feed link must be relative"
    target = _filled(template.link_path)
    assert route_exists(target), (
        f"the {kind!r} update links to {template.link_path!r}, which is no page "
        "in frontend/app; the candidate would click it and land on a 404"
    )


def test_the_resolver_refuses_what_does_not_exist() -> None:
    """A resolver that answers True for everything passes the test above for
    ever. Feed it paths that do and do not exist."""
    if FRONTEND_APP is None:
        return
    assert route_exists("/portal/applications")
    assert route_exists(f"/portal/assessments/{uuid.uuid4()}")
    assert not route_exists("/portal/no-such-page")
    assert not route_exists(f"/portal/applications/{uuid.uuid4()}/nothing")
