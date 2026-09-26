"""The 2026-09 compatibility cutoff (CONTRACT v4 item 6, v6).

Compatibility aliases, redirects and deprecated projections are deleted unless
an old STORED row needs them to render. Pilot held no application links when
the cutoff was taken, so no stored Updates entry or email names a bare path
these redirects existed for. Each deletion is pinned here, because a deleted
alias nobody defends is one somebody re-adds "for safety" in a hotfix.

What was deliberately KEPT is named in
`docs/release/2026-09-vivekium/claude-final-sweeps.md`, with the reason.
"""
from __future__ import annotations

import ast
from pathlib import Path

from app.models.candidate import JobCandidateLink
from app.schemas.portal import ApplicationOut

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
FRONTEND_APP = REPO / "frontend" / "app"


def test_the_compatibility_redirect_pages_are_gone() -> None:
    gone = [
        FRONTEND_APP / "(candidate)" / "portal" / "(app)" / "settings" / "page.tsx",
        FRONTEND_APP / "(candidate)" / "portal" / "(app)" / "assessments" / "page.tsx",
        FRONTEND_APP / "(bd)" / "bd" / "social" / "page.tsx",
    ]
    assert [str(p.relative_to(REPO)) for p in gone if p.exists()] == []
    # The per-application assessment page is NOT an alias and must survive:
    # every invitation and Updates entry links to it.
    assert (
        FRONTEND_APP / "(candidate)" / "portal" / "(app)" / "assessments" / "[link_id]"
    ).is_dir()


def test_the_old_five_value_stage_projection_is_gone() -> None:
    assert "stage" not in ApplicationOut.model_fields
    assert {"status", "stage_label"} <= set(ApplicationOut.model_fields)


def test_nothing_reads_a_permission_flag_nothing_can_set() -> None:
    """`hm_access_granted` lost its only writer in the route scrap. A reader
    of it could only ever answer "not granted", which is a permission rule
    that exists in prose only."""
    assert not hasattr(JobCandidateLink, "hm_access_granted")
    readers: list[str] = []
    for path in (BACKEND / "app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "hm_access_granted":
                readers.append(f"{path.relative_to(BACKEND)}:{node.lineno}")
            elif isinstance(node, ast.keyword) and node.arg == "hm_access_granted":
                readers.append(f"{path.relative_to(BACKEND)}:{node.lineno}")
    assert readers == []
