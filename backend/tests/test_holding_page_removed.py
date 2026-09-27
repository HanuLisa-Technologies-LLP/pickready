"""The holding page and its launch gate are gone. This keeps them gone.

Owner, 2026-09-28, verbatim: "remove the site under construction page and
rewire the landing page for readypick.ai".

Until then `/` chose between the landing page and a "Site Under Construction"
holding page on a build-time variable, `NEXT_PUBLIC_LANDING_LIVE`, and the
public header and footer took a `landingLive` prop so their anchors into the
landing page rendered only when it was served. The variable was never set in
any build path, so readypick.ai served the holding page. `app/page.tsx` called
the gate a one-way door: when the owner flipped it, the holding page and the
branch would leave in the same change. They did, together with the
`.env.example` block, its `EXTERNAL` declaration in
`test_env_example_parity.py`, and the prop on both frame components.

A removal ships with a sweep (CLAUDE.md rule 10), whitespace-normalised through
`tests/removal_sweep.py`, so a mention wrapped across a line cannot hide.
"""
from __future__ import annotations

import re

from tests.removal_sweep import BACKEND, REPO, sweep

PATTERN = re.compile(
    r"site under construction|SiteUnderConstruction|NEXT_PUBLIC_LANDING_LIVE|landingLive",
    re.I,
)

#: Every exemption, each with its reason. Nothing is inferred.
EXEMPT = (
    # This file has to name what it forbids.
    BACKEND / "tests" / "test_holding_page_removed.py",
)

FRONTEND_APP = REPO / "frontend" / "app"


def test_no_source_names_the_holding_page_or_its_gate() -> None:
    hits = sweep(PATTERN, exempt=EXEMPT)
    assert not hits, hits


def test_the_root_route_serves_the_landing_page_unconditionally() -> None:
    """Asserted over the source, so a new gate cannot come back under a name
    this sweep does not know. The root page reads no environment variable at
    all: whatever `/` serves is decided in code review, not at build time."""
    source = (FRONTEND_APP / "page.tsx").read_text(encoding="utf-8")
    assert "<LandingPage />" in source
    assert "process.env" not in source
    assert "?" not in source.split("export default function RootPage", 1)[1]


def test_the_public_frame_takes_no_gate() -> None:
    """The header and footer carry every landing anchor on every public page,
    so neither may be told to hide them again."""
    public = FRONTEND_APP / "(public)"
    for name in ("site-header.tsx", "site-footer.tsx", "layout.tsx", "landing-page.tsx"):
        source = (public / name).read_text(encoding="utf-8")
        assert "process.env" not in source, name
    header = (public / "site-header.tsx").read_text(encoding="utf-8")
    footer = (public / "site-footer.tsx").read_text(encoding="utf-8")
    assert "export function SiteHeader()" in header
    assert "export function SiteFooter()" in footer


def test_the_sweep_is_not_vacuous() -> None:
    """A sweep that reads nothing passes for ever. It must see the frontend
    route that renders the landing page, and `.env.example`."""
    hits = sweep(re.compile(r"<LandingPage />"), roots=(FRONTEND_APP,))
    assert any("page.tsx" in hit for hit in hits), hits
    env_hits = sweep(re.compile(r"NEXT_PUBLIC_FIREBASE_"), roots=(REPO / ".env.example",))
    assert env_hits, env_hits
