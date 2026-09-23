"""An erasure deletes exactly one person, and this file counts the survivors.

WHY IT IS SEPARATE FROM `test_erasure_cascade.py`
---------------------------------------------------
That file proves the erasure reaches far enough: no residual vectors, no
residual cache keys. This one proves it does not reach too far, which is the
opposite failure and the more expensive one. A cascade that took a recruiter's
account, a hiring manager's, another candidate's profile or another tenant's
data with it would be unrecoverable, would look exactly like a successful
erasure in every log line, and would pass every assertion phrased about the
subject.

WHAT WAS COVERED BEFORE: the `users` role guard, and nothing else. The existing
suite asserted that a candidate erasure does not delete a STAFF user row.
Nothing anywhere asserted that it leaves another candidate, another tenant's
rows, or a recruiter's own work alone.

THE BYSTANDERS ARE CHOSEN TO BE THE ONES A PLAUSIBLE BUG WOULD REACH:

  * a candidate in the SAME tenant, which a query missing its `candidate_id`
    predicate would take,
  * a candidate in ANOTHER tenant who shares nothing but a table, which a
    bypass-scoped statement with a wrong predicate would take,
  * the tenant rows themselves, and the job, which a cascade walking the wrong
    direction up a foreign key would take,
  * a recruiter and a hiring manager, which the `users` delete would take if
    its role guard were ever dropped,
  * the SUBJECT'S OWN `audit_log` row, which must SURVIVE: an audit trail a
    subject can delete is not an audit trail, and the erasure writes one more
    row saying it happened.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.db import superadmin_scope
from app.services import erasure


async def _factory_or_skip():
    engine = create_async_engine(
        get_settings().database_url, pool_size=1, max_overflow=0
    )
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001
        await engine.dispose()
        pytest.skip("no database reachable, skipping erasure blast radius test")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


class World:
    def __init__(self) -> None:
        self.tenant = uuid.uuid4()
        self.other_tenant = uuid.uuid4()
        self.job = uuid.uuid4()
        self.other_job = uuid.uuid4()
        self.subject = uuid.uuid4()
        self.subject_user = uuid.uuid4()
        self.subject_profile = uuid.uuid4()
        self.same_tenant_candidate = uuid.uuid4()
        self.same_tenant_profile = uuid.uuid4()
        self.same_tenant_user = uuid.uuid4()
        self.other_tenant_candidate = uuid.uuid4()
        self.other_tenant_profile = uuid.uuid4()
        self.recruiter = uuid.uuid4()
        self.hiring_manager = uuid.uuid4()


async def _seed(session, w: World) -> None:
    for tenant, name in ((w.tenant, "employer"), (w.other_tenant, "rival")):
        await session.execute(
            text(
                "INSERT INTO tenants (id, name, domain, spf_dkim_status) "
                "VALUES (:t, :n, :d, 'pending')"
            ),
            {"t": str(tenant), "n": f"{name}-{tenant}", "d": f"{tenant}.blast.test"},
        )
    for job, tenant in ((w.job, w.tenant), (w.other_job, w.other_tenant)):
        await session.execute(
            text(
                "INSERT INTO jobs (id, tenant_id, title, jd_json, status) "
                "VALUES (:j, :t, 'Backend Engineer', '{}'::jsonb, 'draft')"
            ),
            {"j": str(job), "t": str(tenant)},
        )
    # The staff. Two roles, because the `users` delete is role-guarded and a
    # single role would not show that the guard is what is doing the work.
    for user, role in ((w.recruiter, "recruiter"), (w.hiring_manager, "hiring_manager")):
        await session.execute(
            text(
                "INSERT INTO users (id, tenant_id, email, role, status) "
                "VALUES (:u, :t, :e, :r, 'active')"
            ),
            {
                "u": str(user),
                "t": str(w.tenant),
                "e": f"{user}@blast.test",
                "r": role,
            },
        )
    # Two candidate sign-in accounts: the subject's, which must go, and
    # another candidate's, which must not.
    for user in (w.subject_user, w.same_tenant_user):
        await session.execute(
            text(
                "INSERT INTO users (id, email, role, status) "
                "VALUES (:u, :e, 'candidate', 'active')"
            ),
            {"u": str(user), "e": f"{user}@blast.test"},
        )
    for candidate, profile, tenant, user in (
        (w.subject, w.subject_profile, w.tenant, w.subject_user),
        (
            w.same_tenant_candidate,
            w.same_tenant_profile,
            w.tenant,
            w.same_tenant_user,
        ),
        (w.other_tenant_candidate, w.other_tenant_profile, w.other_tenant, None),
    ):
        await session.execute(
            text(
                "INSERT INTO candidates (id, tenant_id, user_id, full_name, "
                "email, consent_databank) "
                "VALUES (:c, :t, :u, 'Test Person', :e, false)"
            ),
            {
                "c": str(candidate),
                "t": str(tenant),
                "u": str(user) if user else None,
                "e": f"{candidate}@blast.test",
            },
        )
        await session.execute(
            text(
                "INSERT INTO profiles (id, candidate_id, source_tenant_id, "
                "resume_text) VALUES (:p, :c, :t, 'Kafka and Postgres')"
            ),
            {"p": str(profile), "c": str(candidate), "t": str(tenant)},
        )
    # An audit row about the SUBJECT, written before the erasure. It has to
    # survive: `audit_log.candidate_id` carries no foreign key precisely so
    # that a subject cannot delete the record of what was done.
    await session.execute(
        text(
            "INSERT INTO audit_log (id, tenant_id, action, candidate_id) "
            "VALUES (:i, :t, 'blast_radius_probe', :c)"
        ),
        {"i": str(uuid.uuid4()), "t": str(w.tenant), "c": str(w.subject)},
    )


async def _cleanup(session, w: World) -> None:
    for candidate in (
        w.subject,
        w.same_tenant_candidate,
        w.other_tenant_candidate,
    ):
        await session.execute(
            text("DELETE FROM candidates WHERE id = :c"), {"c": str(candidate)}
        )
        await session.execute(
            text("DELETE FROM candidate_deletion_requests WHERE candidate_id = :c"),
            {"c": str(candidate)},
        )
    for user in (w.recruiter, w.hiring_manager, w.subject_user, w.same_tenant_user):
        await session.execute(
            text("DELETE FROM users WHERE id = :u"), {"u": str(user)}
        )
    # `audit_log` is deliberately NOT cleaned: the application role holds no
    # DELETE grant on it, which is the append-only property the erasure
    # receipt depends on. A test that could tidy it away would be testing a
    # database this product does not run on.
    for tenant in (w.tenant, w.other_tenant):
        await session.execute(
            text("DELETE FROM tenants WHERE id = :t"), {"t": str(tenant)}
        )
    await session.commit()


async def _exists(session, table: str, identifier: uuid.UUID) -> bool:
    row = (
        await session.execute(
            text(f"SELECT 1 FROM {table} WHERE id = :i"), {"i": str(identifier)}
        )
    ).first()
    return row is not None


@pytest.mark.asyncio
async def test_erasing_one_candidate_touches_nobody_else() -> None:
    engine, factory = await _factory_or_skip()
    w = World()
    try:
        async with factory() as session:
            async with superadmin_scope(session):
                await _seed(session, w)
                await session.commit()
                receipt = await erasure.cascade_erasure(session, w.subject)
                await session.commit()

        assert receipt.sign_in_accounts_deleted == 1

        # Read every survivor back from a SECOND session, because an
        # assertion against objects the erasing session still holds cannot
        # see what was actually committed.
        async with factory() as session:
            async with superadmin_scope(session):
                assert not await _exists(session, "candidates", w.subject), (
                    "the erasure did not delete its own subject"
                )
                assert not await _exists(session, "users", w.subject_user), (
                    "the subject's sign-in account survived, so an erased "
                    "person still holds a working door into the product"
                )

                survivors = (
                    ("candidates", w.same_tenant_candidate, "a candidate in the same tenant"),
                    ("profiles", w.same_tenant_profile, "that candidate's profile"),
                    ("candidates", w.other_tenant_candidate, "a candidate in another tenant"),
                    ("profiles", w.other_tenant_profile, "that candidate's profile"),
                    ("users", w.same_tenant_user, "another candidate's sign-in account"),
                    ("users", w.recruiter, "a recruiter"),
                    ("users", w.hiring_manager, "a hiring manager"),
                    ("tenants", w.tenant, "the employer the candidate applied to"),
                    ("tenants", w.other_tenant, "an unrelated employer"),
                    ("jobs", w.job, "the job the candidate applied to"),
                    ("jobs", w.other_job, "an unrelated job"),
                )
                for table, identifier, description in survivors:
                    assert await _exists(session, table, identifier), (
                        f"erasing one candidate deleted {description}. An "
                        "erasure that reaches past its subject is "
                        "unrecoverable and looks identical to a successful "
                        "one in every log line."
                    )

                # The record of the act outlives the subject, in both
                # directions: the probe row written before, and the erasure's
                # own row written last.
                actions = {
                    row[0]
                    for row in (
                        await session.execute(
                            text(
                                "SELECT action FROM audit_log "
                                "WHERE candidate_id = :c"
                            ),
                            {"c": str(w.subject)},
                        )
                    ).all()
                }
                assert "blast_radius_probe" in actions, (
                    "the erasure deleted audit rows about its subject. An "
                    "audit trail a subject can delete is not an audit trail."
                )
                assert erasure.ACTION_CANDIDATE_ERASED in actions, (
                    "the erasure left no record that it happened"
                )
    finally:
        async with factory() as session:
            async with superadmin_scope(session):
                await _cleanup(session, w)
        await engine.dispose()
