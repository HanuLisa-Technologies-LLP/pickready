"""No NEW dispatch-before-commit call site in a request path.

A request handler that writes a row and then calls `dispatch(...)` hands the
task off BEFORE `get_tenant_db` commits: the task can run before the row is
visible, and a request that raises afterwards rolls the row back while the task
is already on its way (PLAN-p3 3.1; CONTRACT v2). The fix is
`workers.dispatch.dispatch_after_commit(session, name, args=...)`, which
returns the same client-side handle and invokes only from SQLAlchemy's
`after_commit` (`tests/test_after_commit.py` proves the ordering).

This sweep walks the request-path code by AST and fails on a bare `dispatch(`
call that is not on `LEGACY_CALL_SITES`. The allowlist is the set of call
sites that existed when the rule landed (2026-09-24, at `2c3a695` plus the
WP0 carve), each keyed by file and enclosing function, with the phase that is
expected to convert it. It must SHRINK TO EMPTY by the end of the release, and
the sweep enforces the shrinking too: an entry whose call is gone, or whose
count is higher than what is still there, fails as stale, so a converted call
site cannot leave its allowance behind for a new one to reuse.

Scope is the plan's: every router under `app/api/`, plus
`services/proctoring/` and `services/video/`, which complete an assessment
from inside a request. Worker tasks dispatching from their own committed
sessions are out of scope.

An aliased import of `dispatch` (`from app.workers.dispatch import dispatch
as send`) would hide its calls from a sweep that looks for the name, so the
detector follows the alias and counts `send(...)` as a dispatch, and the alias
itself is refused unless it is on `LEGACY_ALIASES`. The first run of this
sweep found exactly one such site, in `api/email_senders.py`, which is why the
detector follows aliases rather than trusting names.
"""
from __future__ import annotations

import ast
import pathlib
import tempfile
from collections import Counter

APP = pathlib.Path(__file__).resolve().parents[1] / "app"

SCOPE = (
    APP / "api",
    APP / "services" / "proctoring",
    APP / "services" / "video",
)

#: (file relative to app/, enclosing function) -> (allowed bare dispatch
#: calls, proposed owner phase). Owners marked "proposed" were not assigned by
#: a phase plan and are the orchestrator's to confirm.
LEGACY_CALL_SITES: dict[tuple[str, str], tuple[int, str]] = {
    ("api/assessment_conversation.py", "_ensure_conversation_ready"): (1, "P3"),
    ("api/assessment_conversation.py", "respond"): (2, "P3"),
    ("api/assessment_recording.py", "finalize_video_interview"): (1, "P3"),
    ("api/assessment_recording.py", "retry_video_processing"): (1, "P3"),
    ("api/bgv.py", "_resend_after_correction"): (1, "P6 proposed"),
    ("api/bgv.py", "append_employer_route"): (1, "P6 proposed"),
    ("api/bgv.py", "send"): (1, "P6 proposed"),
    ("api/bgv.py", "submit_employer_checkbox_form"): (1, "P6 proposed"),
    ("api/candidates.py", "schedule_interview"): (1, "P6 proposed"),
    ("api/candidates.py", "upload_resume"): (1, "P6"),
    ("api/companies.py", "_issue_invite"): (1, "P6 proposed"),
    ("api/jobs.py", "upload_databank_candidates"): (2, "P2"),
    ("api/matching.py", "run_matching"): (1, "P2"),
    ("api/outreach.py", "send_outreach"): (1, "P6"),
    ("api/pipeline.py", "_queue_transition_email"): (1, "P3"),
    ("api/pipeline.py", "select_candidates_for_assessment"): (1, "P3"),
    ("api/portal.py", "dispatch_bgv_inquiry"): (1, "P6 proposed"),
    ("api/provider.py", "set_primary_contact"): (1, "P7 proposed"),
    ("services/proctoring/ingestion.py", "enqueue_assessment"): (1, "P3"),
    ("services/video/processing.py", "complete_assessment"): (1, "P3"),
}

#: (file relative to app/, the alias) -> proposed owner phase. The same
#: shrink-to-empty rule as the call sites.
LEGACY_ALIASES: dict[tuple[str, str], str] = {
    # EMPTY since Phase 6 WP6-C: the one alias (`dispatch_email` in
    # api/email_senders.py) went when its call became dispatch_after_commit.
}


def _dispatch_aliases(tree: ast.AST) -> set[str]:
    """Every name `dispatch` is imported under in this file."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "app.workers.dispatch":
            for alias in node.names:
                if alias.name == "dispatch":
                    names.add(alias.asname or "dispatch")
    return names


def _is_bare_dispatch(call: ast.Call, names: set[str]) -> bool:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id in names
    if isinstance(func, ast.Attribute):
        return func.attr == "dispatch"
    return False


def _scan(path: pathlib.Path) -> tuple[Counter, list[str]]:
    """Bare dispatch calls per enclosing function, and the aliases in use."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported = _dispatch_aliases(tree)
    names = {"dispatch", *imported}
    aliased = sorted(name for name in imported if name != "dispatch")
    found: Counter = Counter()
    stack: list[str] = []

    class _Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.AST) -> None:
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node: ast.Call) -> None:
            if _is_bare_dispatch(node, names):
                found[".".join(stack) or "<module>"] += 1
            self.generic_visit(node)

    _Visitor().visit(tree)
    return found, aliased


def _found() -> tuple[dict[tuple[str, str], int], set[tuple[str, str]]]:
    sites: dict[tuple[str, str], int] = {}
    aliases: set[tuple[str, str]] = set()
    for root in SCOPE:
        for path in sorted(root.rglob("*.py")):
            relative = path.relative_to(APP).as_posix()
            counts, aliased = _scan(path)
            for function, count in counts.items():
                sites[(relative, function)] = count
            aliases.update((relative, name) for name in aliased)
    return sites, aliases


def test_the_sweep_reads_the_request_path() -> None:
    """A sweep that silently reads nothing passes for ever."""
    sites, _ = _found()
    assert sum(len(list(root.rglob("*.py"))) for root in SCOPE) > 40
    assert sites, "no dispatch call found anywhere: the sweep is not reading the tree"


def test_no_new_dispatch_before_commit_in_a_request_path() -> None:
    sites, _ = _found()
    new = {
        site: count
        for site, count in sites.items()
        if count > LEGACY_CALL_SITES.get(site, (0, ""))[0]
    }
    assert not new, (
        "bare dispatch(...) in a request path, which hands the task off before "
        "the transaction commits. Use dispatch_after_commit(session, name, "
        "args=...) from app.workers.dispatch instead (tests/test_after_commit.py "
        f"shows why): {new}"
    )


def test_the_legacy_allowlist_only_shrinks() -> None:
    """A converted call site must take its allowance with it."""
    sites, _ = _found()
    stale = {
        site: allowed
        for site, (allowed, _owner) in LEGACY_CALL_SITES.items()
        if sites.get(site, 0) < allowed
    }
    assert not stale, (
        "these LEGACY_CALL_SITES entries allow more bare dispatch calls than "
        f"remain; lower or delete them: {stale}"
    )


def test_dispatch_is_never_imported_under_another_name() -> None:
    _, aliases = _found()
    new = sorted(aliases - set(LEGACY_ALIASES))
    stale = sorted(set(LEGACY_ALIASES) - aliases)
    assert not new, f"an aliased dispatch import hides its calls: {new}"
    assert not stale, f"LEGACY_ALIASES entries no longer in use; delete them: {stale}"


def test_the_detector_sees_every_shape_it_claims_to() -> None:
    """Mutation guard for the detector itself, so a refactor of `_scan` that
    stops matching fails here rather than turning the sweep vacuous."""
    sample = "\n".join(
        [
            "from app.workers.dispatch import dispatch",
            "from app.workers.dispatch import dispatch as send",
            "from app.workers import dispatch as dispatch_module",
            "async def handler(session):",
            "    dispatch('pickready.x', args=[])",
            "    send('pickready.w')",
            "    dispatch_module.dispatch('pickready.y')",
            "    dispatch_module.dispatch_after_commit(session, 'pickready.z')",
            "",
        ]
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "sample.py"
        path.write_text(sample, encoding="utf-8")
        counts, aliased = _scan(path)
    assert counts == Counter({"handler": 3})
    assert aliased == ["send"]
