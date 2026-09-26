"""Every background task declares its RLS scope, and the tenant scope is real.

PLAN-p7 WP-B1. Until this release every task ran on `worker_session`, which
sets `app.bypass_rls = 'on'` for the whole run, so a task dispatched for ONE
customer could read and write every customer's rows and only its own WHERE
clauses stood between them. claude.md rule 3 says the Postgres policy is the
boundary and the WHERE clause is defence in depth; for background work that
had never been true.

What is pinned here, in four layers:

1. THE DECLARATION. `registry.task` takes `rls` as a REQUIRED keyword, and a
   "bypass" registration must say why in words. Every registered task is swept
   for it, so a task added tomorrow cannot run without deciding.
2. THE DECLARATION IS BINDING. Each registered body is read as source: a
   "tenant" task that opens the bypass session, or a "bypass" task that opens
   the tenant one, fails here. A declaration nothing checks is a comment.
3. THE TENANT SESSION BINDS, against real Postgres: another tenant's rows are
   invisible, a write into another tenant is refused by the policy, and the
   scope survives the several commits a task makes.
4. EACH CONVERTED TASK writes the same rows under the tenant session as under
   bypass, read back from a SECOND connection; and pointed at another tenant's
   row it finds nothing, which is the policy doing the work rather than a
   WHERE clause.

Mutation checks recorded in the release report: the `role` startup parameter
removed from `tenant_worker_session` (the test database logs in as a
superuser, so the policy stops binding) fails five tests here, including
`test_another_tenants_rows_are_invisible`; the scope applied as statements on
the first connection instead of as startup parameters fails
`test_a_replacement_connection_carries_the_same_scope`; a converted task
switched back to `_worker_session` fails
`test_a_declaration_is_binding_on_the_body`.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import textwrap
import uuid

import pytest
import sqlalchemy as sa

import app.workers.tasks  # noqa: F401  -- registration side effect
from app.workers import registry, runtime, tasks
from app.workers.registry import RLS_BYPASS, RLS_SCOPES, RLS_TENANT, Route, all_specs
from app.workers.runtime import (
    TENANT_OWNER_TABLES,
    TaskContext,
    resolve_tenant_id,
    tenant_worker_session,
    worker_session,
)
from tests import comms_world

#: The tasks this release converted. Pinned by name so a conversion is a
#: reviewed change to this set, and so a revert to bypass is visible here.
CONVERTED = {
    "pickready.send_lifecycle_email",
    "pickready.send_payment_failed_email",
    "pickready.send_credit_warning_email",
    "pickready.send_credit_invoice_email",
}

_TENANT_OPENERS = {"tenant_worker_session", "_tenant_worker_session"}
_BYPASS_OPENERS = {"worker_session", "_worker_session"}


# -- 1. The declaration -------------------------------------------------------


def test_every_registered_task_declares_its_rls_scope() -> None:
    specs = list(all_specs())
    assert len(specs) >= 40, "the registry came back nearly empty; the sweep would pass by vacuum"
    undeclared = [s.name for s in specs if s.rls not in RLS_SCOPES]
    assert not undeclared, f"task(s) with no RLS scope: {undeclared}"
    unexplained = [s.name for s in specs if s.rls == RLS_BYPASS and not s.rls_reason]
    assert not unexplained, f"bypass task(s) with no written reason: {unexplained}"
    noisy = [s.name for s in specs if s.rls == RLS_TENANT and s.rls_reason]
    assert not noisy, f"tenant task(s) carrying a bypass reason: {noisy}"


def test_the_converted_set_is_exactly_the_tenant_set() -> None:
    tenant = {s.name for s in all_specs() if s.rls == RLS_TENANT}
    assert tenant == CONVERTED


def test_a_registration_without_a_scope_is_refused_at_import() -> None:
    with pytest.raises(TypeError):
        registry.task(name="pickready.test_no_scope", route=Route.LAMBDA)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("rls", "reason"),
    [("bypass", ""), ("bypass", "   "), ("tenant", "because"), ("everything", "x")],
)
def test_a_malformed_scope_is_refused(rls: str, reason: str) -> None:
    name = f"pickready.test_malformed_{uuid.uuid4().hex[:8]}"
    with pytest.raises(ValueError):
        registry.task(name=name, route=Route.LAMBDA, rls=rls, rls_reason=reason)(lambda: None)
    with pytest.raises(registry.UnknownTask):
        registry.resolve(name)


# -- 2. The declaration is binding ---------------------------------------------


def _names_used(fn) -> set[str]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}


@pytest.mark.parametrize("spec", list(all_specs()), ids=lambda s: s.name)
def test_a_declaration_is_binding_on_the_body(spec) -> None:
    used = _names_used(spec.fn)
    if spec.rls == RLS_TENANT:
        assert used & _TENANT_OPENERS, f"{spec.name} is declared tenant but never opens the tenant session"
        assert not used & _BYPASS_OPENERS, f"{spec.name} is declared tenant but opens the bypass session"
    else:
        assert not used & _TENANT_OPENERS, (
            f"{spec.name} opens the tenant session but is declared bypass; "
            "declare it rls='tenant' and add it to CONVERTED with its proof"
        )


# -- 3. The tenant session binds ------------------------------------------------


@pytest.fixture
async def two_tenants():
    if not await comms_world.reachable():
        pytest.skip("no database reachable -- skipping tenant worker session tests")
    sessions = comms_world.factory()
    a = await comms_world.seed(sessions)
    b = await comms_world.seed(sessions)
    try:
        yield a, b
    finally:
        await comms_world.cleanup(sessions, a)
        await comms_world.cleanup(sessions, b)


async def test_the_owner_tables_carry_a_not_null_tenant() -> None:
    if not await comms_world.reachable():
        pytest.skip("no database reachable")
    rows = await comms_world.rows(
        comms_world.factory(),
        "SELECT table_name, is_nullable FROM information_schema.columns "
        "WHERE column_name = 'tenant_id' AND table_name = ANY(:names)",
        {"names": list(TENANT_OWNER_TABLES.values())},
    )
    found = {r["table_name"]: r["is_nullable"] for r in rows}
    assert found == {t: "NO" for t in TENANT_OWNER_TABLES.values()}


async def test_another_tenants_rows_are_invisible(two_tenants) -> None:
    a, b = two_tenants
    async with tenant_worker_session(a.tenant) as session:
        role = (await session.execute(sa.text("SELECT current_user"))).scalar_one()
        seen = set(
            (await session.execute(
                sa.text("SELECT id FROM tenants WHERE id = ANY(:ids)"),
                {"ids": [a.tenant, b.tenant]},
            )).scalars()
        )
        links = set(
            (await session.execute(
                sa.text("SELECT id FROM job_candidate_links WHERE id = ANY(:ids)"),
                {"ids": [a.link, b.link]},
            )).scalars()
        )
    assert role == runtime.get_settings().postgres_rls_app_role
    assert seen == {a.tenant}
    assert links == {a.link}


async def test_the_scope_survives_the_tasks_own_commits(two_tenants) -> None:
    a, b = two_tenants
    async with tenant_worker_session(a.tenant) as session:
        await session.commit()
        await session.rollback()
        await session.commit()
        seen = set(
            (await session.execute(
                sa.text("SELECT id FROM jobs WHERE id = ANY(:ids)"), {"ids": [a.job, b.job]}
            )).scalars()
        )
        flag = (await session.execute(
            sa.text("SELECT current_setting('app.bypass_rls', true)")
        )).scalar_one()
    assert seen == {a.job}
    assert flag == "off"


async def test_a_replacement_connection_carries_the_same_scope(two_tenants) -> None:
    """A connection that dies between two of the task's commits is replaced by
    `pool_pre_ping`, and the replacement is scoped exactly like the first.

    This is why the scope is a connection startup parameter: a `SET ROLE` and
    `set_config` run once on the first connection would be absent on the
    replacement, and under a login role that owns the tables (this database)
    the rest of the run would see every tenant.
    """
    a, b = two_tenants
    async with tenant_worker_session(a.tenant) as session:
        first = (await session.execute(sa.text("SELECT pg_backend_pid()"))).scalar_one()
        await session.commit()
        await comms_world.rows(
            comms_world.factory(), "SELECT pg_terminate_backend(:pid) AS ok", {"pid": first}
        )
        second = (await session.execute(sa.text("SELECT pg_backend_pid()"))).scalar_one()
        role = (await session.execute(sa.text("SELECT current_user"))).scalar_one()
        seen = set(
            (await session.execute(
                sa.text("SELECT id FROM jobs WHERE id = ANY(:ids)"), {"ids": [a.job, b.job]}
            )).scalars()
        )
    assert second != first, "the connection was not replaced; the test proves nothing"
    assert role == runtime.get_settings().postgres_rls_app_role
    assert seen == {a.job}


async def test_a_write_into_another_tenant_is_refused_by_the_policy(two_tenants) -> None:
    a, b = two_tenants
    async with tenant_worker_session(a.tenant) as session:
        with pytest.raises(sa.exc.DBAPIError):
            await session.execute(
                sa.text(
                    "INSERT INTO email_log (id, tenant_id, email_type, recipient_email, "
                    " subject, body, status) VALUES (:id, :tid, 'shortlist', "
                    " 'x@comms.test', 's', 'b', 'queued')"
                ),
                {"id": uuid.uuid4(), "tid": b.tenant},
            )
            await session.flush()


async def test_resolve_tenant_id_reads_the_owner_and_answers_none_for_a_missing_row(
    two_tenants,
) -> None:
    a, b = two_tenants
    assert await resolve_tenant_id("link", a.link) == a.tenant
    assert await resolve_tenant_id("job", b.job) == b.tenant
    assert await resolve_tenant_id("job", uuid.uuid4()) is None


# -- 4. Each converted task, under the tenant session ---------------------------


def _ctx(name: str) -> TaskContext:
    return TaskContext(run_id="", name=name, attempt=1, max_attempts=1)


async def _queue(world) -> uuid.UUID:
    from app.services import email_outbox

    async with comms_world.factory()() as session:
        async with session.begin():
            await comms_world.bypass(session)
            row = await email_outbox.queue_candidate_email(
                session,
                tenant_id=world.tenant,
                email_type="shortlist",
                recipient_email=world.candidate_email,
                candidate_id=world.candidate,
                job_id=world.job,
                link_id=world.link,
                subject="Subject",
                body="Body",
                generated_by_ai=False,
                edited_by_human=True,
                sent_by=None,
                thread=False,
            )
            return row.id


async def _sent_state(email_log_id) -> dict:
    rows = await comms_world.rows(
        comms_world.factory(),
        "SELECT e.status, e.error, e.transport, e.provider_message_id, "
        " e.sent_at IS NOT NULL AS stamped, "
        " (SELECT count(*) FROM audit_log al WHERE al.target_id = CAST(e.id AS text) "
        "   AND al.action = 'lifecycle_email_sent' AND al.tenant_id = e.tenant_id) AS audits "
        "FROM email_log e WHERE e.id = :id",
        {"id": str(email_log_id)},
    )
    return rows[0]


async def test_send_lifecycle_email_writes_the_same_rows_as_under_bypass(
    two_tenants, monkeypatch
) -> None:
    a, _b = two_tenants
    tenant_row, bypass_row = await _queue(a), await _queue(a)

    async def _transport(**kwargs):
        return "provider-id"

    monkeypatch.setattr(tasks, "_deliver_email", _transport)

    result = await asyncio.to_thread(
        tasks.send_lifecycle_email, _ctx("pickready.send_lifecycle_email"), str(tenant_row)
    )
    async with worker_session() as session:
        baseline = await tasks._send_lifecycle_email_async(session, str(bypass_row))

    assert result == baseline == {"status": "sent"}
    under_tenant, under_bypass = await _sent_state(tenant_row), await _sent_state(bypass_row)
    assert under_tenant == under_bypass
    assert under_tenant["status"] == "sent" and under_tenant["audits"] == 1


async def test_another_tenants_session_cannot_see_the_row_it_was_not_given(
    two_tenants, monkeypatch
) -> None:
    """The policy, not a WHERE clause: B's session running A's row finds none."""
    a, b = two_tenants
    row_id = await _queue(a)
    sent: list[str] = []

    async def _transport(**kwargs):
        sent.append(kwargs["to"])
        return "provider-id"

    monkeypatch.setattr(tasks, "_deliver_email", _transport)
    async with tenant_worker_session(b.tenant) as session:
        outcome = await tasks._send_lifecycle_email_async(session, str(row_id))
    assert outcome == {"status": "skipped", "reason": "log row not found"}
    assert sent == []
    assert (await _sent_state(row_id))["status"] == "queued"


async def test_a_missing_log_row_is_skipped_exactly_as_before(two_tenants) -> None:
    result = await asyncio.to_thread(
        tasks.send_lifecycle_email, _ctx("pickready.send_lifecycle_email"), str(uuid.uuid4())
    )
    assert result == {"status": "skipped", "reason": "log row not found"}


async def _make_client(world) -> str:
    email = f"owner-{world.tenant.hex[:8]}@comms.test"
    async with comms_world.factory()() as session:
        async with session.begin():
            await comms_world.bypass(session)
            await session.execute(
                sa.text(
                    "INSERT INTO users (id, tenant_id, email, full_name, role, status) "
                    "VALUES (:id, :tid, :email, 'Owner', 'client', 'active')"
                ),
                {"id": uuid.uuid4(), "tid": world.tenant, "email": email},
            )
    return email


def _capture(monkeypatch) -> list[tuple]:
    captured: list[tuple] = []
    monkeypatch.setattr(
        tasks, "dispatch", lambda name, args=None, kwargs=None: captured.append((name, args, kwargs))
    )
    return captured


@pytest.mark.parametrize(
    ("task_name", "extra"),
    [("send_payment_failed_email", ()), ("send_credit_warning_email", (2,))],
)
async def test_billing_notices_resolve_the_same_recipient_as_under_bypass(
    two_tenants, monkeypatch, task_name, extra
) -> None:
    a, b = two_tenants
    email = await _make_client(a)
    await _make_client(b)
    captured = _capture(monkeypatch)
    fn = getattr(tasks, task_name)

    under_tenant = await asyncio.to_thread(fn, str(a.tenant), *extra)
    monkeypatch.setattr(tasks, "_tenant_worker_session", lambda _tid: worker_session())
    under_bypass = await asyncio.to_thread(fn, str(a.tenant), *extra)

    assert under_tenant == under_bypass
    assert under_tenant["sent"] is True
    assert captured[0] == captured[1]
    assert captured[0][1][1] == email


async def _paid_purchase(world) -> uuid.UUID:
    purchase_id = uuid.uuid4()
    async with comms_world.factory()() as session:
        async with session.begin():
            await comms_world.bypass(session)
            await session.execute(
                sa.text(
                    "INSERT INTO credit_purchases (id, tenant_id, pack_slug, "
                    " credits_purchased, subtotal_inr, gst_inr, total_inr, status, "
                    " invoice_number) VALUES (:id, :tid, 'starter', 10, 1000, 180, 1180, "
                    " 'paid', :inv)"
                ),
                {"id": purchase_id, "tid": world.tenant, "inv": f"INV-{purchase_id.hex[:8]}"},
            )
    return purchase_id


async def test_the_invoice_email_is_the_same_under_the_tenant_session(
    two_tenants, monkeypatch
) -> None:
    a, _b = two_tenants
    email = await _make_client(a)
    purchase_id = await _paid_purchase(a)
    captured = _capture(monkeypatch)

    under_tenant = await asyncio.to_thread(tasks.send_credit_invoice_email, str(purchase_id))
    monkeypatch.setattr(tasks, "_tenant_worker_session", lambda _tid: worker_session())
    under_bypass = await asyncio.to_thread(tasks.send_credit_invoice_email, str(purchase_id))

    assert under_tenant == under_bypass
    assert under_tenant.get("sent") is True
    # The PDF bytes carry a render timestamp, so the attachment is compared by
    # name and presence; everything else in the dispatch must be identical.
    (name_t, args_t, kw_t), (name_b, args_b, kw_b) = captured
    assert (name_t, args_t) == (name_b, args_b)
    assert [a["filename"] for a in kw_t["attachments"]] == [
        a["filename"] for a in kw_b["attachments"]
    ]
    assert all(a["content"] for a in kw_t["attachments"])
    assert args_t[1] == email


async def test_a_missing_purchase_is_skipped_exactly_as_before(two_tenants) -> None:
    result = await asyncio.to_thread(tasks.send_credit_invoice_email, str(uuid.uuid4()))
    assert result == {"sent": False, "reason": "purchase not paid"}
