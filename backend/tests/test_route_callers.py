"""Every Owner-console, billing and telemetry route has a caller, or says why not.

WHY THIS EXISTS (Vivekium release, PLAN-p7 WP-B6)
--------------------------------------------------
The route audit found a report library, an Owner-side tenant editor, a
cross-tenant staff list, a permission-template editor, a telemetry counter,
a public billing config route and a paged credit statement that no screen
called. Some were dead and are deleted (`tests/test_dead_routes_removed.py`
keeps them deleted); two were promises the product had stopped keeping and
are now wired into the billing page. The failure they share is that a route
with no caller looks exactly like a route somebody is about to build a screen
for, so nobody deletes it and nobody wires it.

So for the three routers this package owns, every registered route must be
one of:

* CALLED: a string or template literal in the frontend source names its path
  (comments are stripped first, so a code comment quoting a path is not a
  caller);
* an OPERATOR route: an act that has no screen BY DESIGN, declared below with
  the reason, so it cannot be mistaken for a dead route and deleted, and
  cannot quietly acquire a screen without this file being updated;
* an EXTERNAL route: called by a vendor, never by the browser.

A declared operator route that a screen starts calling fails too: the
declaration would then be a false statement about who uses it.
"""
from __future__ import annotations

import functools
import re

from tests.removal_sweep import REPO

FRONTEND = REPO / "frontend"
FRONTEND_ROOTS = ("app", "components", "lib")
V1 = "/api/v1"

#: The routers under this check, by mounted prefix.
SCOPED_PREFIXES = (f"{V1}/admin/", f"{V1}/billing/", f"{V1}/telemetry/")

#: (method, path) -> why it has no screen. Every reason is a sentence a
#: reviewer can disagree with, never a label.
OPERATOR_SURFACE: dict[tuple[str, str], str] = {
    ("GET", f"{V1}/admin/audit-log"): (
        "The Owner reads the cross-tenant audit trail from the API while "
        "investigating an incident; the Provider Portal deliberately has no "
        "Audit Log page (2026-07-27) and every Provider request is still "
        "audited."
    ),
    ("POST", f"{V1}/admin/tenants/{{tenant_id}}/super-admin"): (
        "RBAC 7.1's Super Admin transfer: a rare, deliberate operator act on "
        "a customer's behalf, audited as an exceptional action, and "
        "intentionally one API call rather than a button beside the tenant "
        "list."
    ),
    ("DELETE", f"{V1}/admin/tenants/{{tenant_id}}"): (
        "The irreversible tenant delete, which requires the company name "
        "retyped in the query. It is kept off every screen on purpose so it "
        "is never one click away from Edit; archive is the reversible hide "
        "the Provider Portal offers."
    ),
    ("GET", f"{V1}/admin/llm/stats"): (
        "Per-model health, latency, token and cost counters for the operator "
        "diagnosing a provider incident; the Cost page reads the assessment "
        "cost summary, and this diagnostic stays an API read."
    ),
}

#: (method, path) -> the external caller.
EXTERNAL_CALLERS: dict[tuple[str, str], str] = {
    ("POST", f"{V1}/billing/webhook/razorpay"): (
        "Razorpay's signed webhook delivery, configured in the Razorpay "
        "dashboard against this exact path; a browser never calls it."
    ),
}

_HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


@functools.lru_cache(maxsize=1)
def _scoped_routes() -> frozenset[tuple[str, str]]:
    from app.main import app

    found = set()
    for path, operations in app.openapi()["paths"].items():
        if not path.startswith(SCOPED_PREFIXES):
            continue
        for method in operations:
            if method in _HTTP_METHODS:
                found.add((method.upper(), path))
    return frozenset(found)


_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT = re.compile(r"(?m)^\s*//.*$")
_TEMPLATE_EXPR = re.compile(r"\$\{[^{}]*\}")
_LITERAL = re.compile(r"""["'`]((?:\*)?/[^"'`\s]*)["'`]""")


@functools.lru_cache(maxsize=1)
def _frontend_literals() -> frozenset[str]:
    """Every path-shaped literal in the shipped frontend source.

    A template expression becomes `*`, so `${API_BASE}/billing/x` reads as
    `*/billing/x` and `/jobs/${id}` as `/jobs/*`. Tests are excluded: a test
    naming a route is not a screen calling it.
    """
    literals: set[str] = set()
    for root in FRONTEND_ROOTS:
        for path in (FRONTEND / root).rglob("*"):
            if path.suffix not in {".ts", ".tsx"} or "node_modules" in path.parts:
                continue
            if ".test." in path.name:
                continue
            text = path.read_text(encoding="utf-8")
            text = _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", text))
            text = _TEMPLATE_EXPR.sub("*", text)
            for match in _LITERAL.finditer(text):
                literal = match.group(1).split("?")[0]
                literals.add(literal.removeprefix("*"))
    return frozenset(literals)


def _called(path: str) -> bool:
    relative = path.removeprefix(V1)
    pattern = re.compile(
        "^" + re.sub(r"\\\{[^}]*\\\}", r"[^/]+", re.escape(relative)) + "$"
    )
    return any(pattern.match(literal.replace("*", "X")) for literal in _frontend_literals())


def test_the_route_table_is_real() -> None:
    """A check that iterates an empty table passes without checking anything."""
    routes = _scoped_routes()
    assert ("GET", f"{V1}/billing/overview") in routes
    assert len(routes) >= 15, sorted(routes)


def test_the_literal_scan_is_not_vacuous() -> None:
    """The scan must see the calls it exists to find, including a template
    literal with an expression inside it."""
    assert _called(f"{V1}/billing/overview")
    assert _called(f"{V1}/billing/ledger")
    assert _called(f"{V1}/billing/purchases/{{purchase_id}}/invoice")
    assert not _called(f"{V1}/billing/no-such-route")


def test_every_scoped_route_has_a_caller_or_a_declared_reason() -> None:
    declared = set(OPERATOR_SURFACE) | set(EXTERNAL_CALLERS)
    orphans = sorted(
        route
        for route in _scoped_routes()
        if route not in declared and not _called(route[1])
    )
    assert not orphans, (
        "These routes have no frontend caller and no declared reason. Wire "
        "them into a screen, delete them, or declare them in OPERATOR_SURFACE "
        f"or EXTERNAL_CALLERS with the reason: {orphans}"
    )


def test_every_declaration_names_a_real_route() -> None:
    routes = _scoped_routes()
    stale = sorted(
        route for route in (*OPERATOR_SURFACE, *EXTERNAL_CALLERS) if route not in routes
    )
    assert not stale, f"declared routes that no longer exist: {stale}"


def test_a_declared_operator_route_has_no_screen() -> None:
    """If a screen starts calling an operator route, the declaration is now a
    false statement about who uses it and must move."""
    called = sorted(route for route in OPERATOR_SURFACE if _called(route[1]))
    assert not called, f"operator routes a screen now calls: {called}"


def test_every_reason_is_a_sentence() -> None:
    for route, reason in {**OPERATOR_SURFACE, **EXTERNAL_CALLERS}.items():
        assert len(reason.split()) >= 12, (route, "a one-line label is not a reason")
        assert chr(8212) not in reason
