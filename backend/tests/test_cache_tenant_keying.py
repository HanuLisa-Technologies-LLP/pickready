"""Every cache key builder in the tree carries a tenant, or is named here.

RPN-AI-UP-001 W4.3: "greps every cache key builder for a tenant component and
fails on one that lacks it. A rule enforced at one call site is a rule the next
call site breaks."

WHY A SWEEP AND NOT A REVIEW
-----------------------------
The literature's finding, and this specification's, is that multi-tenant
isolation rarely disappears in the code somebody designed. It disappears LATER,
in a cache somebody added for a performance fix, keyed on the entity id alone
because the entity id was what the slow query took. `core/cache.py` rule 2
already states the rule in prose and has done since the latency pass; what it
did not have was anything that fails when the next contributor does not read it.

WHAT COUNTS AS A KEY BUILDER
-----------------------------
Two shapes, because this codebase uses two:

  * a call to `cache.key(...)`, the namespaced builder in `app/core/cache.py`;
  * an f-string whose first literal opens `pickready:`, which is how the
    `tenant_cache` callers spell theirs.

WHAT COUNTS AS A TENANT COMPONENT
----------------------------------
The token `tenant` appearing in the SCOPE the key is built in: the enclosing
function's whole source, or the statement itself at module level. Deliberately
generous. A narrower rule (the tenant must be a positional argument, say) would
fail `rbac._role_cache_key`, which is correct, and would push people into
spelling it in whatever way the checker happened to accept. The looser rule
still catches the failure that actually happens, which is a builder that has no
tenant anywhere near it.

THE LEDGERS ARE SHORT AND THEY MEAN DIFFERENT THINGS
------------------------------------------------------
`GLOBAL_BY_DESIGN` is data that is genuinely not tenant scoped, with the reason
written beside each entry. `KNOWN_TENANT_GAPS` is the opposite: builders that
SHOULD carry a tenant and do not. Listing a real gap rather than exempting it
quietly is the honest option, and the test below asserts that neither ledger
grows, so a new builder cannot join either one without somebody writing the
reason down.
"""
from __future__ import annotations

import ast
import pathlib

import app as _app_package

BACKEND_APP = pathlib.Path(_app_package.__file__).resolve().parent


#: Keys whose data has no tenant, with the reason. Every entry is a deliberate
#: platform-wide or public surface, not an oversight.
GLOBAL_BY_DESIGN: dict[str, str] = {
    "api/billing.py": (
        "the subscription plan catalogue is the platform's own price list; it "
        "is identical for every customer and is read by the unauthenticated "
        "checkout config"
    ),
    "api/jobs.py": (
        "the public application page at /apply/{job_id}. It is served with no "
        "authentication by design, so there is no tenant to scope it to and "
        "the job id IS the whole identity of the thing being cached"
    ),
    "services/erasure.py": (
        "a candidate is a PLATFORM-level entity in this schema rather than a "
        "tenant-scoped one; it is `job_candidate_links` that carries the "
        "tenant. An erasure sweep keyed by candidate is scoping to the right "
        "entity, and adding a tenant would make it miss the rows it exists to "
        "remove"
    ),
}

#: Builders that should carry a tenant and do not. OPEN, not exempt.
#:
#: EMPTY, and it was not always. `tools/executor._cache_key` keyed an
#: idempotent tool's cached OUTPUT on the tool name and a digest of its
#: validated input alone. Every cached tool takes a globally unique primary key
#: (a job id, a profile id), so a cross-tenant COLLISION was impossible; what
#: was possible is cross-tenant SERVING, because the cache is read before the
#: handler and therefore before the RLS session that would have refused the
#: row. Repaired 2026-09-17: the tenant is now a key segment and part of the
#: digest, and a call that declares no tenant is not cached at all. The entry
#: is DELETED rather than reworded, because the sweep below is what enforces it
#: from here on and a ledger that outlives its defect is an exemption.
KNOWN_TENANT_GAPS: dict[str, str] = {}


#: The module that DEFINES the namespace rather than building a key with it.
#:
#: `core/cache.py` holds `_PREFIX = f"pickready:{_VERSION}"` and the `key()`
#: function that joins a caller's parts onto it. Neither names an entity, so
#: neither can carry a tenant; the tenant is the CALLER's part to pass, which is
#: exactly what `key()`'s own docstring says and what this file checks
#: everywhere else. Skipped as a definition site rather than exempted as a
#: violation, and the distinction is checkable: this module builds no key that
#: identifies anything.
_NAMESPACE_DEFINITION = "core/cache.py"


def _module_key(path: pathlib.Path) -> str:
    return path.relative_to(BACKEND_APP).as_posix()


def _is_cache_key_call(node: ast.AST) -> bool:
    """`cache.key(...)`, however the module was imported."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return isinstance(func, ast.Attribute) and func.attr == "key" and (
        isinstance(func.value, ast.Name) and func.value.id.endswith("cache")
    )


def _is_namespaced_fstring(node: ast.AST) -> bool:
    """An f-string that opens with this platform's cache namespace."""
    if not isinstance(node, ast.JoinedStr) or not node.values:
        return False
    head = node.values[0]
    return (
        isinstance(head, ast.Constant)
        and isinstance(head.value, str)
        and head.value.startswith("pickready:")
    )


def _scopes(tree: ast.AST) -> list[tuple[int, int, ast.AST]]:
    """(first line, last line, node) for every function in the module."""
    found: list[tuple[int, int, ast.AST]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.append((node.lineno, getattr(node, "end_lineno", node.lineno), node))
    return found


def _key_builder_sites() -> dict[str, list[int]]:
    """Every key-building site in `app/`, and the lines they sit on."""
    sites: dict[str, list[int]] = {}
    for path in sorted(BACKEND_APP.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError:  # pragma: no cover - a broken file fails elsewhere
            continue
        lines = [
            node.lineno
            for node in ast.walk(tree)
            if _is_cache_key_call(node) or _is_namespaced_fstring(node)
        ]
        module = _module_key(path)
        if lines and module != _NAMESPACE_DEFINITION:
            sites[module] = sorted(set(lines))
    return sites


def _scope_source(path: pathlib.Path, line: int) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    innermost: str | None = None
    span = None
    for start, end, node in _scopes(tree):
        if start <= line <= end and (span is None or (end - start) < span):
            span = end - start
            innermost = ast.get_source_segment(source, node)
    if innermost is not None:
        return innermost
    return source.splitlines()[line - 1]


# ── The sweep ────────────────────────────────────────────────────────────────


def test_the_sweep_actually_finds_key_builders() -> None:
    """The guard on the guard.

    A sweep that matched nothing would pass for ever and protect nothing, which
    is exactly how `test_platform_audit` spent its whole life green inside the
    backend container. The floor is deliberately above the number of ledger
    entries, so the ledgers cannot be what is keeping it satisfied.
    """
    sites = _key_builder_sites()
    assert len(sites) >= 5, f"only {sorted(sites)} matched; the sweep is not working"
    assert "services/coalescing.py" in sites
    assert "services/rbac.py" in sites


def test_every_cache_key_builder_carries_a_tenant_or_is_named_here() -> None:
    offenders: list[str] = []
    for module, lines in _key_builder_sites().items():
        if module in GLOBAL_BY_DESIGN or module in KNOWN_TENANT_GAPS:
            continue
        path = BACKEND_APP / module
        for line in lines:
            if "tenant" not in _scope_source(path, line).lower():
                offenders.append(f"{module}:{line}")
    assert not offenders, (
        "These cache keys carry no tenant component. A key without one serves "
        "one tenant's derived data to another, and it is the most common place "
        "multi-tenant isolation silently disappears. Add the tenant, or add an "
        "entry to GLOBAL_BY_DESIGN in this file WITH THE REASON:\n  "
        + "\n  ".join(offenders)
    )


def test_the_ledgers_do_not_grow() -> None:
    """An exemption list that anybody may append to is not a rule.

    Both ledgers are pinned by their exact contents rather than by a length, so
    swapping one entry for another is as visible as adding one.
    """
    assert set(GLOBAL_BY_DESIGN) == {
        "api/billing.py",
        "api/jobs.py",
        "services/erasure.py",
    }
    assert set(KNOWN_TENANT_GAPS) == set()
    for reason in list(GLOBAL_BY_DESIGN.values()) + list(KNOWN_TENANT_GAPS.values()):
        assert len(reason.split()) >= 10, (
            "a one-word reason is an exemption nobody has to defend"
        )


def test_every_ledger_entry_still_names_a_real_key_builder() -> None:
    """A ledger that outlives the code it excuses is a rule quietly relaxed.

    If `tools/executor.py` is repaired and stops building a key, or the public
    job cache is removed, the entry must go with it rather than sitting there
    exempting a file that no longer needs exempting.
    """
    sites = _key_builder_sites()
    stale = [
        module
        for module in list(GLOBAL_BY_DESIGN) + list(KNOWN_TENANT_GAPS)
        if module not in sites
    ]
    assert not stale, f"these ledger entries no longer build a cache key: {stale}"


def test_the_known_gap_is_still_a_gap_rather_than_a_forgotten_entry() -> None:
    """The other direction, and the one that turns a ledger into a to-do list.

    An entry whose builder HAS grown a tenant is a recorded defect that has
    quietly become a permanent exemption, so it fails here until somebody
    deletes it. The ledger is empty today (the tool result cache was repaired
    on 2026-09-17), which makes this a guard on whatever is added next rather
    than on nothing: the entry that joins the ledger has to be a real gap.
    """
    for module in KNOWN_TENANT_GAPS:
        path = BACKEND_APP / module
        scopes = [
            _scope_source(path, line) for line in _key_builder_sites()[module]
        ]
        assert not any("tenant" in scope.lower() for scope in scopes), (
            f"{module} now carries a tenant in its cache key. Delete its "
            f"KNOWN_TENANT_GAPS entry: the gap is closed."
        )


def test_the_tool_result_cache_key_is_tenant_scoped() -> None:
    """The repair the ledger used to record, asserted on the VALUES.

    The sweep above reads source text, which is the net. This reads the key
    itself, because the failure it guards against is two tenants issuing the
    same tool call with the same input and the second being handed the first
    one's answer out of Redis, before the handler and therefore before RLS.
    """
    import uuid

    import pytest
    from pydantic import BaseModel

    from app.services.coalescing import TenantScopeMissing
    from app.services.tools import executor

    class _In(BaseModel):
        job_id: str

    payload = _In(job_id="7c0f6d9e-0000-0000-0000-000000000001")
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()

    key_a = executor._cache_key("extract_jd", payload, tenant_a)
    key_b = executor._cache_key("extract_jd", payload, tenant_b)

    assert key_a != key_b, "one input shape, two tenants, one cache entry"
    assert str(tenant_a) in key_a, (
        "the tenant is a visible segment so an operator can read whose entry "
        "an item is without decoding the digest"
    )
    assert executor._cache_key("extract_jd", payload, tenant_a) == key_a, (
        "the key must be stable, or nothing is ever a hit"
    )

    # Refused rather than defaulted: a global key is the failure this exists to
    # make impossible, and the caller declines to cache instead.
    for absent in (None, "", "   "):
        with pytest.raises(TenantScopeMissing):
            executor._cache_key("extract_jd", payload, absent)


def test_the_shared_key_builder_cannot_be_called_without_a_tenant() -> None:
    """The sweep above is the net; this is the structure underneath it.

    `coalescing.request_key` is the builder every new derived-representation
    cache is meant to use, and it REFUSES rather than defaulting. A sweep can be
    evaded by spelling a key inline; a builder that raises cannot.
    """
    import pytest

    from app.services import coalescing

    with pytest.raises(coalescing.TenantScopeMissing):
        coalescing.request_key(tenant_id=None, kind="anything", payload={})
