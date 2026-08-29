"""RBAC 5 and 39's cardinality invariants, asserted against a real Postgres.

spec-doc6 9.1: "Enforce at the database level with constraints, not only in
application code, and test that the constraint fires."

The second half of that sentence is why this file exists separately from
`test_rbac_conformance.py`. A test that asserts an index NAME appears in a
migration proves somebody typed it. What proves the invariant is a real INSERT
that Postgres refuses, and that needs a database.

WHY APPLICATION-LEVEL CHECKS ARE NOT EQUIVALENT
-----------------------------------------------
The obvious alternative is a SELECT-then-INSERT in the handler. Two concurrent
requests both read zero and both insert, and the loser of that race is a row
nobody knows about until somebody reads the table and finds two Super Admins.
A partial unique index has no such window.

PARTIAL, BECAUSE THE RULE IS ABOUT THE ACTIVE ONE
-------------------------------------------------
"Exactly one ACTIVE Super Admin" and "exactly one Recruiter per job" are both
statements about the live row. A total unique constraint would refuse to keep
the history, so a revoked assignment could not sit beside its replacement and
"who owned this job in March" would become unanswerable.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.db import superadmin_scope


async def _factory_or_skip():
    engine = create_async_engine(get_settings().database_url, pool_size=1, max_overflow=0)
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001 -- reachability, not a behaviour under test
        await engine.dispose()
        pytest.skip("no database reachable; the cardinality constraints need one")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


class _Fixture:
    """Two tenants, each with people and a job. Torn down by tenant cascade."""

    def __init__(self) -> None:
        self.tenant_a = uuid.uuid4()
        self.tenant_b = uuid.uuid4()
        self.job_a = uuid.uuid4()
        self.users: dict[str, uuid.UUID] = {}


async def _seed(session, fx: _Fixture) -> None:
    for tenant, label in ((fx.tenant_a, "A"), (fx.tenant_b, "B")):
        await session.execute(
            text(
                "INSERT INTO tenants (id, name, domain, spf_dkim_status) "
                "VALUES (:tid, :name, :domain, 'pending')"
            ),
            {"tid": str(tenant), "name": f"Cardinality {label}", "domain": f"{tenant}.card.test"},
        )
    for key, tenant, role in (
        ("a_admin", fx.tenant_a, "client"),
        ("a_second", fx.tenant_a, "recruiter"),
        ("a_third", fx.tenant_a, "recruiter"),
        ("a_hm", fx.tenant_a, "hiring_manager"),
        ("a_hm2", fx.tenant_a, "hiring_manager"),
        ("a_im", fx.tenant_a, "interview_manager"),
        ("a_im2", fx.tenant_a, "interview_manager"),
        ("b_admin", fx.tenant_b, "client"),
    ):
        user_id = uuid.uuid4()
        fx.users[key] = user_id
        await session.execute(
            text(
                "INSERT INTO users (id, tenant_id, role, email, status) "
                "VALUES (:uid, :tid, :role, :email, 'active')"
            ),
            {"uid": str(user_id), "tid": str(tenant), "role": role,
             "email": f"{user_id}@card.test"},
        )
    await session.execute(
        text(
            "INSERT INTO jobs (id, tenant_id, title, jd_json, status) "
            "VALUES (:jid, :tid, 'Cardinality', '{}'::jsonb, 'draft')"
        ),
        {"jid": str(fx.job_a), "tid": str(fx.tenant_a)},
    )


async def _cleanup(session, fx: _Fixture) -> None:
    for tenant in (fx.tenant_a, fx.tenant_b):
        await session.execute(
            text("DELETE FROM tenants WHERE id = :tid"), {"tid": str(tenant)}
        )


async def _assign(session, fx: _Fixture, user_key: str, role: str, *, active: bool = True):
    await session.execute(
        text(
            "INSERT INTO job_assignments "
            "(tenant_id, job_id, user_id, assignment_role, active, revoked_at) "
            "VALUES (:tid, :jid, :uid, :role, :active, :revoked)"
        ),
        {
            "tid": str(fx.tenant_a),
            "jid": str(fx.job_a),
            "uid": str(fx.users[user_key]),
            "role": role,
            "active": active,
            "revoked": None if active else datetime(2026, 8, 1, tzinfo=timezone.utc),
        },
    )


async def _run(body) -> None:
    """Seed, run `body`, and always clean up.

    Runs under the bypass scope because the fixture spans two tenants and the
    point of these tests is the CONSTRAINT, not the RLS policy (which
    `test_cross_tenant_isolation.py` covers).
    """
    engine, factory = await _factory_or_skip()
    fx = _Fixture()
    try:
        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await _seed(session, fx)
        await body(factory, fx)
    finally:
        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await _cleanup(session, fx)
        await engine.dispose()


# ── Exactly one active Super Admin PER CLIENT (RBAC 5, 7.1, 39) ──────────────

async def test_a_second_active_super_admin_in_one_tenant_is_refused() -> None:
    async def body(factory, fx):
        with pytest.raises(IntegrityError) as caught:
            async with factory() as session:
                async with session.begin():
                    async with superadmin_scope(session):
                        await session.execute(
                            text(
                                "INSERT INTO users (id, tenant_id, role, email, status) "
                                "VALUES (:uid, :tid, 'client', :email, 'active')"
                            ),
                            {"uid": str(uuid.uuid4()), "tid": str(fx.tenant_a),
                             "email": f"{uuid.uuid4()}@card.test"},
                        )
        assert "uq_users_one_active_super_admin_per_tenant" in str(caught.value)

    await _run(body)


async def test_each_tenant_holds_its_own_super_admin() -> None:
    """The invariant is PER CLIENT ORGANIZATION (7.1), not global.

    This is the case a global uniqueness rule would break, and it would break
    it invisibly: every test written against one seeded tenant would pass, and
    the failure would arrive as the second customer ReadyPick ever onboards.
    The fixture seeds a Super Admin in each of two tenants, so the seed itself
    is the assertion.
    """
    async def body(factory, fx):
        async with factory() as session:
            async with superadmin_scope(session):
                rows = (
                    await session.execute(
                        text(
                            "SELECT tenant_id, count(*) FROM users "
                            "WHERE role = 'client' AND status <> 'disabled' "
                            "AND tenant_id IN (:a, :b) GROUP BY tenant_id"
                        ),
                        {"a": str(fx.tenant_a), "b": str(fx.tenant_b)},
                    )
                ).all()
        assert sorted(count for _, count in rows) == [1, 1]

    await _run(body)


async def test_a_disabled_super_admin_leaves_the_seat_free() -> None:
    """Partial on `status <> 'disabled'`, which is what makes a handover
    possible at all: the outgoing holder's row stays, and a replacement can be
    inserted beside it."""
    async def body(factory, fx):
        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await session.execute(
                        text("UPDATE users SET status = 'disabled' WHERE id = :uid"),
                        {"uid": str(fx.users["a_admin"])},
                    )
                    await session.execute(
                        text(
                            "INSERT INTO users (id, tenant_id, role, email, status) "
                            "VALUES (:uid, :tid, 'client', :email, 'active')"
                        ),
                        {"uid": str(uuid.uuid4()), "tid": str(fx.tenant_a),
                         "email": f"{uuid.uuid4()}@card.test"},
                    )

    await _run(body)


async def test_the_seat_transfers_atomically() -> None:
    """RBAC 7.1's second sentence, against the real constraint.

    Demote then promote, in the caller's transaction. The order is not
    interchangeable: a unique index is checked per statement, so promoting
    first is refused while the outgoing holder is still active. That is
    asserted here rather than described, because the description would still
    be true of code that did it the other way round and happened to work in a
    test with no existing Super Admin.
    """
    from app.services import rbac

    async def body(factory, fx):
        # The wrong order is genuinely refused.
        with pytest.raises(IntegrityError):
            async with factory() as session:
                async with session.begin():
                    async with superadmin_scope(session):
                        await session.execute(
                            text("UPDATE users SET role = 'client' WHERE id = :uid"),
                            {"uid": str(fx.users["a_second"])},
                        )

        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    result = await rbac.transfer_super_admin(
                        session,
                        tenant_id=fx.tenant_a,
                        to_user_id=fx.users["a_second"],
                    )
        assert result["new_state"]["super_admin_user_id"] == str(fx.users["a_second"])
        assert result["previous_state"]["super_admin_user_id"] == str(fx.users["a_admin"])

        async with factory() as session:
            async with superadmin_scope(session):
                roles = dict(
                    (
                        await session.execute(
                            text(
                                "SELECT id::text, role FROM users WHERE id IN (:a, :b)"
                            ),
                            {"a": str(fx.users["a_admin"]), "b": str(fx.users["a_second"])},
                        )
                    ).all()
                )
        assert roles[str(fx.users["a_second"])] == "client"
        # Demoted, never deleted and never disabled.
        assert roles[str(fx.users["a_admin"])] == "hr_manager"

    await _run(body)


# ── Exactly one Recruiter and one Hiring Manager per job (RBAC 5, 39) ────────

@pytest.mark.parametrize(
    "assignment_role,index_name",
    [
        ("recruiter", "uq_job_assignments_one_active_recruiter"),
        ("hiring_manager", "uq_job_assignments_one_active_hiring_manager"),
    ],
)
async def test_a_job_admits_one_active_holder_of_a_singular_assignment(
    assignment_role: str, index_name: str
) -> None:
    second = {"recruiter": "a_third", "hiring_manager": "a_hm2"}[assignment_role]
    first = {"recruiter": "a_second", "hiring_manager": "a_hm"}[assignment_role]

    async def body(factory, fx):
        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await _assign(session, fx, first, assignment_role)

        with pytest.raises(IntegrityError) as caught:
            async with factory() as session:
                async with session.begin():
                    async with superadmin_scope(session):
                        await _assign(session, fx, second, assignment_role)
        assert index_name in str(caught.value)

    await _run(body)


@pytest.mark.parametrize("assignment_role", ["recruiter", "hiring_manager"])
async def test_a_revoked_assignment_frees_the_slot_and_stays_as_history(
    assignment_role: str,
) -> None:
    """Both halves matter. Freeing the slot is what makes a reassignment
    possible; keeping the row is what makes "who owned this job in March"
    answerable."""
    second = {"recruiter": "a_third", "hiring_manager": "a_hm2"}[assignment_role]
    first = {"recruiter": "a_second", "hiring_manager": "a_hm"}[assignment_role]

    async def body(factory, fx):
        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await _assign(session, fx, first, assignment_role, active=False)
                    await _assign(session, fx, second, assignment_role)

        async with factory() as session:
            async with superadmin_scope(session):
                rows = (
                    await session.execute(
                        text(
                            "SELECT active FROM job_assignments "
                            "WHERE job_id = :jid AND assignment_role = :role"
                        ),
                        {"jid": str(fx.job_a), "role": assignment_role},
                    )
                ).scalars().all()
        assert sorted(rows) == [False, True], "the revoked row must survive"

    await _run(body)


# ── A job MAY have many Interview Managers (RBAC 13.1, 39) ───────────────────

async def test_a_job_admits_several_interview_managers() -> None:
    """13.1 is explicit that there is no requirement of exactly one, and 29
    draws three of them. Enforced by the ABSENCE of a singular index, which is
    a thing only a test can notice somebody adding."""
    async def body(factory, fx):
        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await _assign(session, fx, "a_im", "interview_manager")
                    await _assign(session, fx, "a_im2", "interview_manager")

        async with factory() as session:
            async with superadmin_scope(session):
                count = (
                    await session.execute(
                        text(
                            "SELECT count(*) FROM job_assignments WHERE job_id = :jid "
                            "AND assignment_role = 'interview_manager' AND active"
                        ),
                        {"jid": str(fx.job_a)},
                    )
                ).scalar()
        assert count == 2

    await _run(body)


async def test_one_person_cannot_hold_the_same_assignment_twice() -> None:
    """The one thing still refused for Interview Managers. A duplicate row
    would double that person's presence in any future count and is never
    intentional."""
    async def body(factory, fx):
        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await _assign(session, fx, "a_im", "interview_manager")

        with pytest.raises(IntegrityError) as caught:
            async with factory() as session:
                async with session.begin():
                    async with superadmin_scope(session):
                        await _assign(session, fx, "a_im", "interview_manager")
        assert "uq_job_assignments_no_duplicate_holder" in str(caught.value)

    await _run(body)


# ── The row's own consistency ────────────────────────────────────────────────

async def test_an_inactive_assignment_must_say_when_it_stopped() -> None:
    """Without this CHECK a revoked row with a null timestamp is
    indistinguishable from a live one that lost its flag."""
    async def body(factory, fx):
        with pytest.raises(IntegrityError) as caught:
            async with factory() as session:
                async with session.begin():
                    async with superadmin_scope(session):
                        await session.execute(
                            text(
                                "INSERT INTO job_assignments "
                                "(tenant_id, job_id, user_id, assignment_role, active, revoked_at) "
                                "VALUES (:tid, :jid, :uid, 'interview_manager', false, NULL)"
                            ),
                            {"tid": str(fx.tenant_a), "jid": str(fx.job_a),
                             "uid": str(fx.users["a_im"])},
                        )
        assert "ck_job_assignments_revoked_at_matches_active" in str(caught.value)

    await _run(body)


async def test_an_unknown_assignment_role_is_refused() -> None:
    async def body(factory, fx):
        with pytest.raises(IntegrityError) as caught:
            async with factory() as session:
                async with session.begin():
                    async with superadmin_scope(session):
                        await session.execute(
                            text(
                                "INSERT INTO job_assignments "
                                "(tenant_id, job_id, user_id, assignment_role) "
                                "VALUES (:tid, :jid, :uid, 'approver')"
                            ),
                            {"tid": str(fx.tenant_a), "jid": str(fx.job_a),
                             "uid": str(fx.users["a_im"])},
                        )
        assert "ck_job_assignments_role" in str(caught.value)

    await _run(body)


async def test_an_assigned_persons_row_cannot_be_deleted_from_under_them() -> None:
    """ON DELETE RESTRICT, alone among the user references this table could
    have used. An assignment whose person was erased asserts that somebody
    owns this job while being unable to say who, which is indistinguishable
    from nobody owning it. Same argument `review_dispositions.decided_by`
    already makes for a human disposition."""
    async def body(factory, fx):
        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await _assign(session, fx, "a_second", "recruiter")

        with pytest.raises(IntegrityError):
            async with factory() as session:
                async with session.begin():
                    async with superadmin_scope(session):
                        await session.execute(
                            text("DELETE FROM users WHERE id = :uid"),
                            {"uid": str(fx.users["a_second"])},
                        )

    await _run(body)


# ── RBAC 34: the database refuses a half-attributed agent row ────────────────

async def test_the_database_refuses_an_agent_audit_row_with_no_human() -> None:
    """The service raises `AgentPrincipalError`, and this is the same rule one
    layer down, because the service is not the only writer a database gets."""
    async def body(factory, fx):
        with pytest.raises(IntegrityError) as caught:
            async with factory() as session:
                async with session.begin():
                    async with superadmin_scope(session):
                        await session.execute(
                            text(
                                "INSERT INTO audit_log "
                                "(id, tenant_id, actor_user_id, action, agent_name) "
                                "VALUES (gen_random_uuid(), :tid, NULL, 'job_criteria_edited', 'sutra')"
                            ),
                            {"tid": str(fx.tenant_a)},
                        )
        assert "ck_audit_log_agent_has_principal" in str(caught.value)

    await _run(body)


async def test_a_job_defaults_to_the_lifecycle_state_that_grants_least() -> None:
    """A writer that does not know about `lifecycle_state` must not produce a
    row `rbac.decide` reads as unconstrained. DRAFT cannot be published (21)
    and its JD is still in Recruiter drafting scope (24***)."""
    async def body(factory, fx):
        async with factory() as session:
            async with superadmin_scope(session):
                state = (
                    await session.execute(
                        text("SELECT lifecycle_state FROM jobs WHERE id = :jid"),
                        {"jid": str(fx.job_a)},
                    )
                ).scalar()
        assert state == "DRAFT"

    await _run(body)
