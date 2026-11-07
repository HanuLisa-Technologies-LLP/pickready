"""Outbound email delivery hardening tests (no network).

Covers:
  * the From identity `_send_email_async` hands to SES: platform mail, tenant
    mail with no active sender (a RECORDED fallback), and tenant_id=None;
  * that a permanent failure is NOT retried while a transient one IS;
  * that every template name the app sends has a default.

SES is replaced by a fake client throughout: these tests must never hit the
network. The resolver itself is proven in `test_company_sender_ses.py`.
"""
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest


@asynccontextmanager
async def _noop_session():
    """Stand-in for tasks._worker_session — no DB, no engine."""
    yield SimpleNamespace()

from app.services.delivery_errors import (
    PermanentDeliveryError,
    TransientDeliveryError,
)


# ── From identity chosen by _send_email_async ────────────────────────────────
# Exercises the real _send_email_async with the DB/render/SES boundaries
# stubbed, so we assert purely on which From and which audit fields are chosen.

class _FakeSession:
    async def get(self, model, ident):
        return self._tenant

    async def execute(self, *a, **k):
        return SimpleNamespace(scalar_one_or_none=lambda: None)

    async def commit(self):
        return None


async def _capture_sender(monkeypatch, *, tenant):
    """Run _send_email_async with dependencies stubbed and capture SES fields."""
    from app.services import ses_service
    from app.workers import tasks

    captured = {}

    async def _fake_ses(
        from_email, from_name, to, subject, html, text=None, attachments=None,
        reply_to=None, correlation=None,
    ):
        captured["from"] = from_email
        captured["from_name"] = from_name
        captured["html"] = html
        captured["text"] = text
        captured["reply_to"] = reply_to
        return "msg_123"

    async def _fake_render(session, tid, name, ctx):
        return ("subject", "body")

    async def _fake_audit(session, tenant_id, action, ttype, tid, meta):
        captured["audit"] = meta
        return None

    monkeypatch.setattr(ses_service, "send_email_async", _fake_ses)
    monkeypatch.setattr(tasks, "_audit", _fake_audit)

    import app.services.email_render as er
    monkeypatch.setattr(er, "render", _fake_render)

    session = _FakeSession()
    session._tenant = tenant
    tenant_id = str(tenant.id) if tenant is not None else None
    await tasks._send_email_async(
        session, tenant_id, "hr@corp.test", "otp", {"code": "1"}
    )
    return captured


@pytest.fixture(autouse=True)
def _no_default_sender(monkeypatch):
    """No tenant here has an active default sender."""
    from app.services import email_sender_resolution

    async def _none(session, tenant_id):
        return None

    monkeypatch.setattr(email_sender_resolution, "default_active_sender", _none)


@pytest.mark.asyncio
async def test_tenant_without_a_sender_falls_back_and_says_so(monkeypatch):
    tenant = SimpleNamespace(id=uuid.uuid4(), name="Acme Corp")
    captured = await _capture_sender(monkeypatch, tenant=tenant)
    assert captured["from"] == "contact@readypick.ai"
    assert captured["from_name"] == "Acme Corp via Vivekium"
    assert captured["audit"]["from_source"] == "platform_fallback"
    assert captured["audit"]["from_email"] == "contact@readypick.ai"


@pytest.mark.asyncio
async def test_sender_tenant_none_does_not_crash(monkeypatch):
    # Platform users (Owner OTP) have tenant_id=None — this was a real past bug.
    captured = await _capture_sender(monkeypatch, tenant=None)
    assert captured["from"] == "contact@readypick.ai"
    assert captured["from_name"] == "Vivekium"
    assert captured["audit"]["from_source"] == "platform"
    # html body is derived from the plain-text body and sent alongside text.
    assert captured["text"] == "body"
    assert "body" in captured["html"]


# ── permanent failure is not retried; transient is ──────────────────────────

async def _noop_coroutine():
    return None


def _ctx(attempt: int = 1, max_attempts: int = 3):
    """A task context, as `runtime.run_task` would build one.

    Task bodies are plain functions now, so a test calls one directly instead of
    going through Celery's `.run`, which auto-bound `self` and defaulted
    `self.request.retries` to zero.
    """
    from app.workers.runtime import TaskContext

    return TaskContext(
        run_id="",
        name="pickready.send_email",
        attempt=attempt,
        max_attempts=max_attempts,
    )


def test_permanent_failure_not_retried(monkeypatch):
    """send_email must swallow a PermanentDeliveryError (return, no retry)."""
    from app.workers import tasks

    async def _boom(session, *a, **k):
        raise PermanentDeliveryError(
            "resend", 403, "validation_error", "unverified", hint="verify domain"
        )

    monkeypatch.setattr(tasks, "_send_email_async", _boom)
    monkeypatch.setattr(tasks, "_worker_session", _noop_session)

    result = tasks.send_email(_ctx(), None, "a@b.test", "otp", {})
    assert result == {"status": "failed", "error": "validation_error"}


def test_transient_failure_reraises(monkeypatch):
    """A transient failure must propagate so the runtime's retry loop backs off."""
    from app.workers import tasks

    async def _boom(session, *a, **k):
        raise TransientDeliveryError("resend", 503, "server_error", "later")

    monkeypatch.setattr(tasks, "_send_email_async", _boom)
    monkeypatch.setattr(tasks, "_worker_session", _noop_session)

    audited = []
    monkeypatch.setattr(
        tasks,
        "_audit_delivery_exhausted",
        lambda *a, **k: (audited.append(a), _noop_coroutine())[1],
    )

    with pytest.raises(TransientDeliveryError):
        tasks.send_email(_ctx(attempt=1, max_attempts=3), None, "a@b.test", "otp", {})
    assert not audited, (
        "the terminal-failure audit fired on an intermediate attempt; one "
        "undelivered email would be recorded as several"
    )


def test_the_exhausted_audit_fires_on_the_last_attempt_only(monkeypatch):
    """The audit row means "this email was given up on", so it must be written
    once, at the point that becomes true."""
    from app.workers import tasks

    async def _boom(session, *a, **k):
        raise TransientDeliveryError("resend", 503, "server_error", "later")

    monkeypatch.setattr(tasks, "_send_email_async", _boom)
    monkeypatch.setattr(tasks, "_worker_session", _noop_session)

    audited = []
    monkeypatch.setattr(
        tasks,
        "_audit_delivery_exhausted",
        lambda *a, **k: (audited.append(a), _noop_coroutine())[1],
    )

    with pytest.raises(TransientDeliveryError):
        tasks.send_email(_ctx(attempt=3, max_attempts=3), None, "a@b.test", "otp", {})
    assert len(audited) == 1


def test_every_template_name_the_app_sends_has_a_default() -> None:
    """Every literal template name passed to `pickready.send_email` anywhere in
    backend/app MUST resolve without a tenant row.

    This is the invariant that was broken in production. `email_render.render`
    raises ValueError when a name matches neither a tenant row nor a default,
    and it raises inside the Celery task — long after the API answered 200. Two
    names (`candidate_outreach`, `client_invite`) were being sent with no
    default and nothing seeding a row, so those invitations were discarded with
    no email_log row and no audit_log row. Nothing in the product could show
    the user that their invitation had evaporated.

    Scanning the source rather than listing the names by hand is deliberate: a
    hand-maintained list is exactly what drifted in the first place.
    """
    import ast
    from pathlib import Path

    from app.services.email_render import DEFAULT_TEMPLATES

    app_root = Path(__file__).resolve().parents[1] / "app"
    # Parsed, not regexed: the call is
    # `dispatch("pickready.send_email", args=[<tenant>, <to>, "<name>", {...}])`
    # and the template name is args[2]. A regex over this shape kept picking up
    # keys out of the context dict that follows it.
    found: set[str] = set()
    for path in app_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            first = node.args[0]
            if not (
                isinstance(first, ast.Constant)
                and first.value == "pickready.send_email"
            ):
                continue
            task_args = next(
                (kw.value for kw in node.keywords if kw.arg == "args"), None
            )
            if isinstance(task_args, ast.List) and len(task_args.elts) >= 3:
                name = task_args.elts[2]
                if isinstance(name, ast.Constant) and isinstance(name.value, str):
                    found.add(name.value)

    assert found, "scanner matched nothing; the dispatch shape must have changed"
    missing = sorted(name for name in found if name not in DEFAULT_TEMPLATES)
    assert not missing, (
        f"template name(s) {missing} are sent by the app but have no entry in "
        "DEFAULT_TEMPLATES; every one of those emails would be silently lost"
    )


# -- Delivery is never starved by AI work (regression, 2026-08-01) -----------
#
# Production incident: every task shared one `celery` queue against a
# `--concurrency=2` worker. Two technical-question generation runs wedged both
# slots in an unterminating loop, and a staff invitation enqueued at 14:07 UTC
# was not delivered until 14:13, when a slot briefly freed at the soft-time-limit
# boundary. The API had already answered 201 with `email_dispatch: "queued"`, so
# nothing surfaced the delay.
#
# Celery answered it with a second queue and a second worker pool, and these
# tests asserted the routing that made the split real. There are no queues and
# no pools any more, so the assertions moved to the thing that now carries the
# guarantee: WHERE a task runs. Delivery runs in the per-invocation worker
# function; the work that wedged the pool runs as its own on-demand task. The
# two cannot contend for one another's capacity because neither of them holds
# any capacity to contend for.


def _delivery_specs():
    from app.workers.registry import all_specs

    return [
        spec
        for spec in all_specs()
        if "send_" in spec.name or spec.name.endswith("_email")
    ]


def test_every_outbound_delivery_task_runs_per_invocation() -> None:
    """Delivery must never sit behind an LLM chain that legitimately takes
    minutes. Scanned from the registry rather than a hand-kept list, because a
    hand-kept list is exactly what drifts."""
    from app.workers.registry import Route

    delivery = _delivery_specs()
    assert delivery, "registry scan matched no delivery tasks"
    misrouted = sorted(
        spec.name for spec in delivery if spec.route is not Route.LAMBDA
    )
    assert not misrouted, (
        f"delivery task(s) {misrouted} are routed to on-demand compute; an "
        "invitation there waits on a container start it does not need"
    )


def test_no_long_running_task_shares_the_delivery_route() -> None:
    """The other half of the split: the work that wedged the pool must be on
    the on-demand route, so it has nowhere to starve delivery from."""
    from app.workers.registry import Route, resolve

    for name in (
        "pickready.generate_candidate_questions",
        "pickready.run_functional_assessment",
        "pickready.run_matching",
    ):
        assert resolve(name).route is Route.ECS, (
            f"{name} runs minutes of model work and must not share a "
            "short-work function with delivery"
        )


def test_a_timed_out_task_is_not_retried() -> None:
    """A task killed at its platform ceiling gets no retry, structurally.

    Celery needed `dont_autoretry_for=(SoftTimeLimitExceeded,)` for this,
    because its soft limit raised an ordinary Exception inside the task and
    `autoretry_for=(Exception,)` caught it: the incident log shows
    `retry: Retry in 1s: SoftTimeLimitExceeded()` and the slot re-occupied in
    the same second.

    The retry loop now lives INSIDE the invocation, so a killed invocation takes
    its retry loop with it. This asserts the property that makes that true:
    nothing in the runtime re-dispatches, so no failed run can schedule another.
    """
    import ast
    import pathlib as _pathlib

    source = (
        _pathlib.Path(__file__).resolve().parents[1]
        / "app"
        / "workers"
        / "runtime.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    } | {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "dispatch" not in called, (
        "runtime re-dispatches on failure; a task killed at its ceiling would "
        "then be retried by a fresh invocation, which is exactly what the "
        "soft-timeout exclusion list existed to prevent"
    )


def test_the_retry_loop_refuses_an_attempt_that_cannot_finish() -> None:
    """Predictive, not observational.

    A delivery task backing off sixty seconds with forty seconds of budget left
    must not start the retry. Being killed mid-send is the expensive case: SES
    may already have accepted the message, and the next attempt sends it twice.
    """
    from app.workers import runtime
    from app.workers.registry import Route, TaskSpec

    attempts = []

    def _always_fails():
        attempts.append(1)
        raise RuntimeError("transient")

    spec = TaskSpec(
        name="test.backoff",
        fn=_always_fails,
        route=Route.LAMBDA,
        max_attempts=5,
        max_attempts_setting=None,
        backoff_seconds=60.0,
        backoff_max_seconds=60.0,
        retry_on=(Exception,),
        bind=False,
        summary="",
    )
    original = runtime.resolve
    runtime.resolve = lambda _name: spec
    try:
        with pytest.raises(RuntimeError):
            runtime.run_task(
                {"run_id": "", "task": "test.backoff", "args": [], "kwargs": {}},
                remaining_seconds=lambda: 40.0,
            )
    finally:
        runtime.resolve = original
    assert attempts == [1], (
        "a retry was started that could not have finished inside the budget"
    )


# -- An unreachable dispatch target must fail, not hang ----------------------


def test_the_dispatch_client_bounds_every_call() -> None:
    """Publishing to Redis had NO timeout by default, so `send_task` against a
    private IP with no route blocked forever instead of raising.

    That is worse than an error, because every caller that carefully wraps its
    enqueue in try/except never gets to run the handler: nothing is raised.
    Observed in production, a job with REDIS_URL but no VPC egress hung on its
    first enqueue and was killed at the 900s ceiling having written nothing.

    botocore has the same shape of default, a 60-second connect timeout with
    retries stacked on top, so the same assertion is worth making about the
    client that replaced it.
    """
    from app.workers import dispatch as dispatch_mod

    dispatch_mod.reset_client()
    try:
        config = dispatch_mod._lambda_client().meta.config
    finally:
        dispatch_mod.reset_client()
    assert config.connect_timeout and config.connect_timeout <= 10, (
        "no bounded connect timeout; an unreachable endpoint will hang callers"
    )
    assert config.read_timeout and config.read_timeout <= 30, (
        "no bounded read timeout; a host that accepts and never answers hangs"
    )
    # botocore normalises `max_attempts` to `total_max_attempts` (which counts
    # the first call), so both spellings are accepted rather than the one this
    # happens to have been written with.
    retries = config.retries or {}
    assert retries.get("max_attempts") or retries.get("total_max_attempts"), (
        "retries are unbounded; a dead endpoint retries forever"
    )
