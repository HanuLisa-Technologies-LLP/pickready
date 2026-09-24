"""The HR review screen's two routes are gone, and stay gone.

WHAT WAS WRONG WITH THEM
------------------------
`POST /candidates/links/{link_id}/decision` and
`POST /candidates/links/{link_id}/status` wrote a `pipeline_status` history row
and an audit row and did NOTHING ELSE. They never touched
`job_candidate_links.status`, and they skipped `hiring_pipeline.apply_transition`,
which is the one chokepoint every other pipeline move goes through: the FSM's
legal-edge check, the BGV offer gate, the candidate's Updates feed row and the
stage email. So a recruiter could "shortlist" or "offer" through them and the
candidate table, the feed, the offer gate and the candidate all disagreed about
what had happened. Their only screens (`/org/review`, which no navigation
linked to, and two decision components one of which nothing imported) are
removed with them by the frontend half of the same change.

WHAT SURVIVES, DELIBERATELY
---------------------------
* The `decide_profile` and `update_pipeline_status` CAPABILITIES. They are
  live: `POST /pipeline/applications/{link_id}/change-status` and the
  dashboard decision control are gated on them, and a capability is data with a
  seeding migration behind it.
* `EV_HM_DECISION` telemetry, emitted by `api/pipeline.py` and
  `api/dashboard.py`, so the intelligence metrics lose no source.
* The `profile_decision` and `pipeline_status_updated` rows already in
  `audit_log` are history and are not rewritten.

The source sweep is the shared whitespace-normalised one
(`tests/removal_sweep.py`).
"""
from __future__ import annotations

import pathlib
import re

import pytest

from app.main import app
from app.services import capabilities as caps
from tests.removal_sweep import BACKEND, REPO, sweep

GONE_PATHS = (
    "/api/v1/candidates/links/{link_id}/decision",
    "/api/v1/candidates/links/{link_id}/status",
)

#: The backend half: the two handlers, the forward-status gate only `/status`
#: used, the refusal sentence only it raised, and a route string either way.
BACKEND_GONE = re.compile(
    r"\bFORWARD_STATUSES\b"
    r"|def (?:decide_profile|update_pipeline_status)\b"
    r"|has not completed the 40-question application"
    r"|/links/\{link_id\}/(?:decision|status)\b"
    r"|candidates/links/\$\{[^}]+\}/(?:decision|status)\b"
)

#: The frontend half: the unlinked review and templates pages and the four
#: components that served nothing else.
FRONTEND_GONE = re.compile(
    r"org/review\b"
    r"|org/templates\b"
    r"|candidate-decision-actions"
    r"|hm-decision-actions"
    r"|candidate-selection\b"
    r"|components/jobs-list\b"
    r"|\b(?:CandidateDecisionActions|HmDecisionActions|CandidateSelection|JobsList)\b"
)

THIS_FILE = pathlib.Path(__file__)


def test_the_routes_are_not_served() -> None:
    served = {
        (path, method)
        for route in app.routes
        for path in [getattr(route, "path", "")]
        for method in (getattr(route, "methods", None) or ())
    }
    for path in GONE_PATHS:
        assert (path, "POST") not in served, path
    schema_paths = app.openapi()["paths"]
    for path in GONE_PATHS:
        assert path not in schema_paths, path


def test_no_backend_source_still_carries_them() -> None:
    hits = sweep(
        BACKEND_GONE,
        exempt=(THIS_FILE,),
        roots=(BACKEND / "app", BACKEND / "tests", BACKEND / "scripts"),
    )
    assert not hits, "the decision/status routes are back:\n" + "\n".join(hits)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "WP6-F deletes these frontend files in the same release. Until that "
        "merges this fails as expected; once it merges it passes, strict xfail "
        "turns that into a failure, and the marker must be removed in that "
        "merge so the absence is enforced from then on."
    ),
)
def test_no_frontend_source_still_carries_them() -> None:
    # A route directory is named by its PATH, which a content sweep never
    # reads, so the two pages are asserted absent on disk as well.
    pages = REPO / "frontend" / "app" / "(org)" / "org"
    present = [name for name in ("review", "templates") if (pages / name).exists()]
    assert not present, f"unlinked org pages are back: {present}"
    hits = sweep(
        FRONTEND_GONE,
        exempt=(THIS_FILE,),
        roots=(
            REPO / "frontend" / "app",
            REPO / "frontend" / "components",
            REPO / "frontend" / "lib",
        ),
    )
    assert not hits, "the HR review screen is back:\n" + "\n".join(hits)


def test_the_capabilities_that_gate_the_live_paths_survive() -> None:
    """Deleting a route is not deleting the permission other routes read."""
    assert caps.DECIDE_PROFILE == "decide_profile"
    assert caps.UPDATE_PIPELINE_STATUS == "update_pipeline_status"
    assert "/api/v1/pipeline/applications/{link_id}/change-status" in app.openapi()[
        "paths"
    ]


def test_the_sweep_sees_the_patterns_it_forbids() -> None:
    """A sweep that matches nothing passes for ever. Feed it each shape."""
    samples = {
        "FORWARD_STATUSES = {}": True,
        "_FORWARD_STATUSES = ()": False,
        "async def decide_profile(link_id):": True,
        '@router.post("/links/{link_id}/decision")': True,
        '@router.post("/links/{link_id}/status")': True,
        "apiPost(`/candidates/links/${row.link_id}/decision`)": True,
        '@router.get("/applications/{link_id}/status")': False,
        "Candidate has not completed the 40-question\n application yet": True,
        'DECIDE_PROFILE = "decide_profile"': False,
    }
    for sample, expected in samples.items():
        flat = " ".join(sample.split())
        assert bool(BACKEND_GONE.search(flat)) is expected, sample
    for sample, expected in {
        'import { HmDecisionActions } from "@/components/hm-decision-actions";': True,
        'href="/org/review"': True,
        'href="/org/reviews-archive"': False,
        "export function JobsList() {}": True,
    }.items():
        assert bool(FRONTEND_GONE.search(sample)) is expected, sample
