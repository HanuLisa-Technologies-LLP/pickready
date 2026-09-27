"""The public site makes no claim that candidate code is executed.

OWNER DECISION, 2026-09-28: Judge0 is ON HOLD and every AWS resource it had
is destroyed, so pilot runs `CODE_EXECUTION_BACKEND=disabled` with no sandbox
behind it. The landing package of this release had written "Coding run in a
sandbox" into the hero, the features, how it works and the pricing card. A
sentence on the marketing site is a promise to a buyer, and this one described
a capability that is not provisioned anywhere. Those lines now name what ships
(typed or spoken prose answers, multiple choice, fill in the blank).

The code (`services/code_execution`) and the Terraform module stay, disabled,
so this sweep reads only the PUBLIC surfaces: the marketing pages, the root
page and layout, the share card, the product tour and the site constants.
Resuming the sandbox is an owner decision, and the change that turns it on is
the change that removes this test, with the reason.
"""
from __future__ import annotations

import pathlib
import re

from tests.removal_sweep import sweep

BACKEND = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = BACKEND.parent / "frontend"

PUBLIC_ROOTS = [
    FRONTEND / "app" / "(public)",
    FRONTEND / "app" / "page.tsx",
    FRONTEND / "app" / "layout.tsx",
    FRONTEND / "app" / "opengraph-image.tsx",
    FRONTEND / "components" / "workflow-animation",
    FRONTEND / "lib" / "site.ts",
]

PATTERNS = (
    re.compile(r"\bsandbox(ed)?\b", re.I),
    re.compile(r"\bcoding (questions )?(are )?(run|runs|executed|tested)\b", re.I),
    re.compile(r"\bhidden tests?\b", re.I),
    re.compile(r"\bcode (is |gets )?(run|executed|compiled)\b", re.I),
)


def test_the_public_site_claims_no_code_execution() -> None:
    hits = [hit for pattern in PATTERNS for hit in sweep(pattern, roots=PUBLIC_ROOTS)]
    assert not hits, "public copy claims candidate code is executed:\n" + "\n".join(hits)


def test_the_sweep_reads_the_public_pages() -> None:
    """A root list that resolved to nothing would pass everything."""
    for root in PUBLIC_ROOTS:
        assert root.exists(), f"missing public root: {root}"
    known = sweep(re.compile(r"PRISM Report"), roots=PUBLIC_ROOTS)
    assert known, "the sweep read no public page"
