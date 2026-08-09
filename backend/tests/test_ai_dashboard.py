"""The Customer Portal's AI Dashboard (2026-08-09).

It is the one new client-facing surface that aggregates assessment data, so it
is the obvious place to break the standing rule that no number reaches a
client. The rule covers a SCORE, percentage, rank or band for an assessment or
a match; a count of things (jobs, candidates, assessments, reports) is what the
existing dashboard already reports and is outside it. These tests hold that
line at the schema, not at the handler, because a schema field is what actually
reaches the browser.

The second thing pinned here is that framework health is measured against the
COMPETENCY ROWS and never against `framework_generated_at`. That distinction is
the whole of the 2026-08-06 finding: 19 of 35 live jobs carried the stamp with
zero rows, were permanently stuck, and no health check saw it because every one
of them asked the stamp.
"""
from __future__ import annotations

import pytest

from app.api import dashboard as dashboard_api
from app.schemas.dashboard import (
    AIDashboardOut,
    AssessmentFunnelOut,
    FrameworkHealthOut,
    GradeCountOut,
)
from app.services import capabilities as caps
from app.services import rating

FORBIDDEN_FIELDS = {
    "score",
    "scores",
    "percent",
    "percentage",
    "rank",
    "ranking",
    "band",
    "band_index",
    "rating",
    "average",
    "mean",
    "points",
}


def _all_fields() -> set[str]:
    names: set[str] = set()
    for model in (
        AIDashboardOut,
        AssessmentFunnelOut,
        FrameworkHealthOut,
        GradeCountOut,
    ):
        names |= set(model.model_fields)
    return names


# ── No numbers reach a client ────────────────────────────────────────────────

def test_no_score_shaped_field_reaches_the_client() -> None:
    assert _all_fields() & FORBIDDEN_FIELDS == set()


def test_a_grade_is_a_word_from_the_one_rating_scale() -> None:
    """Never a number, and never a fifth label invented here. There is ONE
    scale and it lives in services/rating."""
    field = GradeCountOut.model_fields["grade"]
    assert field.annotation is str
    for grade in rating.GRADES:
        assert GradeCountOut(grade=grade, candidates=0).grade in rating.GRADES


def test_every_grade_is_reported_even_at_zero() -> None:
    """A breakdown that omits the empty grades reads as "nobody landed there"
    rather than "nobody has been assessed", which are different facts."""
    import inspect

    source = inspect.getsource(dashboard_api.ai_dashboard)
    assert "for grade in rating.GRADES" in source
    assert "{grade: 0 for grade in rating.GRADES}" in source


# ── Framework health asks the table, not the stamp ───────────────────────────

def test_framework_health_is_measured_against_the_competency_rows() -> None:
    import inspect

    source = inspect.getsource(dashboard_api.ai_dashboard)
    assert "JobCompetency.job_id" in source
    # A timestamp is not evidence that work happened. `framework_approved_at`
    # is read (approval IS a stamp), `framework_generated_at` is not.
    #
    # Asserted against the parsed ATTRIBUTES, not the text, so the comment and
    # the docstring recording WHY that column is not read may go on naming it.
    # Same reasoning as
    # `test_no_canned_acknowledgments_in_the_conversation_path`, which checks
    # code lines only for exactly this reason.
    import ast
    import textwrap

    tree = ast.parse(textwrap.dedent(source))
    attributes = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    assert "framework_generated_at" not in attributes
    assert "framework_approved_at" in attributes


def test_a_stuck_job_is_its_own_visible_state() -> None:
    """Not folded into "awaiting approval": a framework nobody can approve
    because it has no rows is a different problem with a different fix."""
    assert "pending_generation" in FrameworkHealthOut.model_fields
    assert "awaiting_approval" in FrameworkHealthOut.model_fields


# ── Scope and gating ─────────────────────────────────────────────────────────

def test_the_route_is_capability_gated_and_tenant_scoped() -> None:
    route = next(
        r for r in dashboard_api.router.routes
        if getattr(r, "path", "") == "/ai-insights"
    )
    assert "GET" in route.methods
    # The tenant-scoped session is the RLS boundary; the handler's own tenant
    # predicates are defence in depth (CLAUDE.md rule 1).
    names = {dep.call for dep in route.dependant.dependencies}
    assert dashboard_api.get_tenant_db in names

    import inspect

    source = inspect.getsource(dashboard_api.ai_dashboard)
    assert "Job.tenant_id == user.tenant_id" in source
    assert caps.VIEW_DASHBOARD


def test_it_sits_beside_the_existing_dashboard_rather_than_replacing_it() -> None:
    paths = {getattr(r, "path", "") for r in dashboard_api.router.routes}
    assert {"/summary", "/ai-insights"} <= paths


# ── The fallback count means one specific thing ──────────────────────────────

def test_only_a_provider_outage_counts_as_scored_offline() -> None:
    """`no_transcript` means the candidate answered nothing, which is not the
    AI failing. Counting it here would tell a customer their reports were
    degraded when they were not."""
    import inspect

    source = inspect.getsource(dashboard_api.ai_dashboard)
    assert 'scoring_mode == "deterministic_fallback"' in source
    assert "no_transcript" in source  # named in the comment that explains why


@pytest.mark.parametrize(
    "payload",
    [
        {"grade": rating.GRADE_HIGHLY, "candidates": 0},
        {"grade": rating.GRADE_NOT, "candidates": 12},
    ],
)
def test_a_grade_row_carries_a_headcount_and_nothing_else(payload: dict) -> None:
    row = GradeCountOut(**payload)
    assert set(row.model_dump()) == {"grade", "candidates"}
