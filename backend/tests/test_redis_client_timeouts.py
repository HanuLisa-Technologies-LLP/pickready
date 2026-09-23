"""Every Redis client in `app/` bounds its socket, or is named here with a reason.

WHY A SWEEP AND NOT A REVIEW
-----------------------------
`services/tenant_cache.py` built its client with no `socket_connect_timeout`
and no `socket_timeout` for its whole life, and that module backs
`rbac._permission_rows`, which `require_capability` reaches on essentially
every authorized route in the product. It looked safe, because every operation
was wrapped in `except Exception`. It was not: a guard catches a RAISED error,
and an unreachable Redis does not raise, it BLOCKS. With no socket timeout a
partition between the API task and ElastiCache stalls the request rather than
failing it, the fallback to Postgres never runs, and nothing anywhere logs a
thing. This repository has already been taught the same lesson once by a broker
publish with no timeout (claude.md, 2026-08-05) and once by botocore's
60-second default (claude.md, 2026-09-05).

So the rule is structural: no client without both bounds. This file is the
regression guard that would have caught the original defect, and it fails in
the direction that matters -- a NEW client added for a new cache is the exact
shape of the next occurrence.

WHAT COUNTS AS A CLIENT CONSTRUCTION
--------------------------------------
Two shapes, because the tree uses both:

  * `<something redis>.from_url(...)`, however the module was imported
    (`aioredis`, `redis_asyncio`, `redis.Redis`);
  * a direct `Redis(...)` constructor call.

THE LEDGER IS SHORT AND IT IS NOT AN EXEMPTION LIST
-----------------------------------------------------
`OPERATOR_SCRIPTS_BY_DESIGN` holds clients that are not on a request path at
all, with the reason written beside each. The ledger is pinned by its exact
contents, so swapping an entry for another is as visible as adding one.
"""
from __future__ import annotations

import ast
import pathlib

import app as _app_package

BACKEND_APP = pathlib.Path(_app_package.__file__).resolve().parent

#: Both keywords, because either one alone leaves a hang available: a connect
#: timeout does not bound a read on an established connection that stops
#: answering, which is what a mid-flight partition looks like.
REQUIRED_KEYWORDS = ("socket_connect_timeout", "socket_timeout")


#: Clients that never run inside a request, with the reason. Not oversights.
OPERATOR_SCRIPTS_BY_DESIGN: dict[str, str] = {
    "scripts/validate_stack.py": (
        "the stack validator's own Redis PING probe, run by a person from a "
        "shell to answer whether the infrastructure is up; a hang there is the "
        "answer it was run to get rather than an outage it causes"
    ),
}


def _module_key(path: pathlib.Path) -> str:
    return path.relative_to(BACKEND_APP).as_posix()


def _dotted(node: ast.AST) -> str:
    """`redis.asyncio.Redis` from the attribute chain, best effort."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    return ""


def _is_redis_construction(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == "from_url":
        return "redis" in _dotted(func.value).lower()
    if isinstance(func, ast.Attribute) and func.attr == "Redis":
        return True
    if isinstance(func, ast.Name) and func.id == "Redis":
        return True
    return False


def _client_sites() -> dict[str, list[tuple[int, set[str]]]]:
    """Every Redis client construction in `app/`: module -> [(line, kwargs)]."""
    sites: dict[str, list[tuple[int, set[str]]]] = {}
    for path in sorted(BACKEND_APP.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a broken file fails elsewhere
            continue
        found = [
            (node.lineno, {kw.arg for kw in node.keywords if kw.arg})
            for node in ast.walk(tree)
            if _is_redis_construction(node)
        ]
        if found:
            sites[_module_key(path)] = sorted(found, key=lambda item: item[0])
    return sites


# ── The sweep ────────────────────────────────────────────────────────────────


def test_the_sweep_actually_finds_redis_clients() -> None:
    """The guard on the guard.

    A matcher that found nothing would pass for ever and protect nothing. The
    floor is above the ledger's size so the ledger cannot be what satisfies it,
    and one module is named outright: `core/redis_loop.py` builds the client
    every tenant-scoped cache read, every run-status poll and the web-search
    breaker go through (since 2026-09-24 those three share it rather than each
    building their own, so the floor moved from five to four with that reason).
    """
    sites = _client_sites()
    assert len(sites) >= 4, f"only {sorted(sites)} matched; the sweep is broken"
    assert "core/redis_loop.py" in sites


def test_the_loop_bound_callers_build_no_client_of_their_own() -> None:
    """The three modules that share `LoopBoundRedis` must not grow a second,
    process-global client back: that is exactly the warm-worker defect."""
    sites = _client_sites()
    for module in ("core/cache.py", "workers/status.py", "services/web_research.py"):
        assert module not in sites, module
        source = (BACKEND_APP / module).read_text(encoding="utf-8")
        assert "LoopBoundRedis(" in source, module


def test_every_redis_client_bounds_its_socket() -> None:
    offenders: list[str] = []
    for module, found in _client_sites().items():
        if module in OPERATOR_SCRIPTS_BY_DESIGN:
            continue
        for line, keywords in found:
            missing = [kw for kw in REQUIRED_KEYWORDS if kw not in keywords]
            if missing:
                offenders.append(f"{module}:{line} missing {', '.join(missing)}")
    assert not offenders, (
        "These Redis clients can HANG rather than fail. An unreachable Redis "
        "does not raise, so the `except Exception` around the call never runs "
        "and the request stalls instead of degrading. Pass "
        + " and ".join(REQUIRED_KEYWORDS)
        + ", or add an entry to OPERATOR_SCRIPTS_BY_DESIGN WITH THE REASON:\n  "
        + "\n  ".join(offenders)
    )


def test_the_hot_path_has_exactly_one_client() -> None:
    """One implementation per concept, asserted where it was broken.

    `tenant_cache` used to build its own client -- a second cache layer, and
    the one on the RBAC hot path was the one missing the guard. It is a facade
    over `core/cache` now and must construct nothing of its own.
    """
    sites = _client_sites()
    assert "services/tenant_cache.py" not in sites, (
        "services/tenant_cache.py builds a Redis client again. It is a facade "
        "over app/core/cache.py; a second client there is the defect this "
        "consolidation removed."
    )

    from app.services import tenant_cache

    source = pathlib.Path(tenant_cache.__file__).read_text(encoding="utf-8")
    assert "from app.core import cache" in source


def test_the_ledger_does_not_grow() -> None:
    """An exemption list anybody may append to is not a rule."""
    # `scripts/validate_auth.py` left the ledger on 2026-09-24: its Redis
    # client only reset the retired code-login counters, and went with them.
    assert set(OPERATOR_SCRIPTS_BY_DESIGN) == {
        "scripts/validate_stack.py",
    }
    for reason in OPERATOR_SCRIPTS_BY_DESIGN.values():
        assert len(reason.split()) >= 10, (
            "a one-word reason is an exemption nobody has to defend"
        )


def test_every_ledger_entry_still_names_a_real_client() -> None:
    """A ledger that outlives the code it excuses is a rule quietly relaxed."""
    sites = _client_sites()
    stale = [
        module for module in OPERATOR_SCRIPTS_BY_DESIGN if module not in sites
    ]
    assert not stale, f"these ledger entries no longer build a client: {stale}"


def test_the_matcher_recognises_the_shape_the_defect_had() -> None:
    """The sweep is only worth what its matcher catches.

    Exercised against the exact source `tenant_cache` carried before the
    repair, so a future refactor of `_is_redis_construction` that stopped
    matching an aliased import fails here rather than going quiet.
    """
    tree = ast.parse(
        "from redis import asyncio as aioredis\n"
        "c = aioredis.from_url(url, decode_responses=True)\n"
    )
    calls = [node for node in ast.walk(tree) if _is_redis_construction(node)]
    assert len(calls) == 1
    assert {kw.arg for kw in calls[0].keywords} == {"decode_responses"}
