"""The 2026-09 compatibility cutoff (CONTRACT v4 item 6, v6).

Compatibility aliases, redirects and deprecated projections are deleted unless
an old STORED row needs them to render. Pilot held no application links when
the cutoff was taken, so no stored Updates entry or email names a bare path
these redirects existed for. Each deletion is pinned here, because a deleted
alias nobody defends is one somebody re-adds "for safety" in a hotfix.

What was deliberately KEPT, with the reason: `/org` (the org home, a real
server redirect, not a compatibility alias); `JDGenerateIn.key_requirements`
(its docstring calls it a deprecated alias, but the live Create Job form still
sends the AI brief box through it, so it is an input with a writer);
`TenantCreateIn.domain` and `client_phone` (optional inputs, not projections);
the `LinkSource` and `resume_storage_provider` CHECK values (stored data).
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


def test_the_second_band_order_alias_is_gone() -> None:
    """`ASSESSMENT_BAND_ORDER` was a deprecated alias for the one four-grade
    order, kept "so older imports keep compiling" after nothing imported it."""
    source = (REPO / "frontend" / "components" / "rating-label.tsx").read_text(
        encoding="utf-8"
    )
    assert "ASSESSMENT_BAND_ORDER" not in source
    assert "MATCHING_BAND_ORDER" in source


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


def test_no_code_reads_the_pre_aws_object_store() -> None:
    """Pilot holds no `gs://` row (CONTRACT v3), so the readers that named the
    old store went. Swept over CODE (string literals and identifiers), not
    comments, which may still record the history."""
    hits: list[str] = []
    for path in (BACKEND / "app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            value = None
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                value = node.value
            elif isinstance(node, ast.Name):
                value = node.id
            elif isinstance(node, ast.Attribute):
                value = node.attr
            if value is None:
                continue
            if (
                "gs://" in value
                or value.lower() == "gcs"
                or value in {"LEGACY_STORAGE_PROVIDER", "LEGACY_GCS_SCHEME", "is_legacy_uri"}
            ):
                hits.append(f"{path.relative_to(BACKEND)}:{node.lineno}")
    assert hits == []
