"""Migration 0118_skills_contract: every existing matrix becomes the skills
contract IN PLACE, and nothing live is lost on the way.

What is pinned, and why each one matters on a live database:

* a job with a STARTED assessment is locked with snapshot version 1, built from
  the exact rows its candidates were questioned against, and every started
  session is bound to it; the rows themselves are not rewritten;
* an unlocked job's `force_rank` becomes the per-bucket priority, in place;
* a saved matrix STAYS invitable (an honest empty role summary is stamped);
* a bucket over five is REPORTED, never truncated;
* NO PUBLISHED JOB IS UNPUBLISHED by the lifecycle remap (pilot has 31);
* the creator of a job gets an assignment in their own role, and only then;
* duplicate live answer evidence is superseded, never deleted, so the unique
  index the idempotent ledger writer relies on can be built;
* the migration's canonical digest is the SAME function as
  `assessment_contract.compute_digest`;
* every step is idempotent, and the migration round-trips.

The data steps run SCOPED to the jobs this file seeds (`job_ids`), because the
test database is shared and a real upgrade touches every row. Assertions read
from a SECOND connection after the migration's transaction committed.
"""
from __future__ import annotations

import importlib.util
import pathlib
import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.db import superadmin_scope
from app.scripts import skills_overflow_report
from app.services import assessment_contract as contract_api
from app.services.assessment_contract import ContractSkill

MIGRATION_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0118_skills_contract.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0118", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


migration = _load_migration()

#: 0119 tightened `ck_jobs_lifecycle_state` to six states. This file seeds the
#: two RETIRED approval states on purpose, because 0118's remap exists to move
#: them, so the seeded-world test admits them for its own duration only.
NEXT_MIGRATION_PATH = MIGRATION_PATH.with_name("0119_skills_setup.py")


def _load_next_migration():
    spec = importlib.util.spec_from_file_location("migration_0119", NEXT_MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


next_migration = _load_next_migration()


async def _set_lifecycle_check(engine, states: tuple[str, ...]) -> None:
    quoted = ", ".join(f"'{state}'" for state in states)
    async with engine.begin() as connection:
        await connection.execute(
            text("ALTER TABLE jobs DROP CONSTRAINT ck_jobs_lifecycle_state")
        )
        await connection.execute(
            text(
                "ALTER TABLE jobs ADD CONSTRAINT ck_jobs_lifecycle_state CHECK "
                f"(lifecycle_state IS NULL OR lifecycle_state IN ({quoted}))"
            )
        )

NOW = datetime.now(timezone.utc)


# ── Pure: the two canonicalisers are one function ───────────────────────────


def test_the_migration_digest_is_the_contract_digest() -> None:
    """A migrated snapshot is read back through `compute_digest`, which RAISES
    on a mismatch. So the two must agree on every input, not on one fixture."""
    rng = random.Random(118)
    names = ["Python", "SQL", "Kafka", "Ownership", "Terraform", "Go", "Rust", "Unicode ✓ skill"]
    for _ in range(200):
        skills = []
        for bucket in contract_api.BUCKETS:
            for priority in range(1, rng.randint(0, 6) + 1):
                skills.append(
                    {
                        "id": str(uuid.uuid4()),
                        "name": rng.choice(names),
                        "bucket": bucket,
                        "priority": priority,
                        "evidence_line": rng.choice(["", "Has shipped it.", "Led an incident."]),
                    }
                )
        rng.shuffle(skills)
        summary = rng.choice(["", "Owns the platform."])
        grade = rng.choice(["non_managerial", "managerial", "leadership", "cxo"])
        as_contract = [
            ContractSkill(uuid.UUID(s["id"]), s["name"], s["bucket"], s["priority"], s["evidence_line"])
            for s in skills
        ]
        assert migration.canonical_digest(skills, summary, grade) == contract_api.compute_digest(
            as_contract, summary, grade
        )


def test_the_migration_bucket_order_and_limit_match_the_contract() -> None:
    assert migration.BUCKETS == contract_api.BUCKETS
    assert migration.MAX_PER_BUCKET == skills_overflow_report.MAX_PER_BUCKET == 5


# ── The seeded world ─────────────────────────────────────────────────────────


@dataclass
class _World:
    tenant: uuid.UUID = field(default_factory=uuid.uuid4)
    users: dict[str, uuid.UUID] = field(default_factory=dict)
    jobs: dict[str, uuid.UUID] = field(default_factory=dict)
    skills: dict[str, uuid.UUID] = field(default_factory=dict)
    conversations: dict[str, uuid.UUID] = field(default_factory=dict)
    message: uuid.UUID = field(default_factory=uuid.uuid4)
    link: uuid.UUID | None = None


#: (key, role, status)
_USERS = (
    ("recruiter", "recruiter", "active"),
    ("other_recruiter", "recruiter", "active"),
    ("hiring_manager", "hiring_manager", "active"),
    ("hr_manager", "hr_manager", "active"),
    ("disabled_recruiter", "recruiter", "disabled"),
)

#: (key, creator, published, lifecycle, framework_approved, archived)
_JOBS = (
    ("locked", "hiring_manager", True, "DRAFT", True, False),
    ("unlocked_saved", "recruiter", False, "IN_REVIEW", True, False),
    ("published_sent", "hr_manager", True, "SENT_TO_HIRING_MANAGER", False, False),
    ("draft_retired", "disabled_recruiter", False, "IN_REVIEW", False, False),
    ("already_assigned", "recruiter", False, "DRAFT", False, False),
    ("archived", "recruiter", True, "CLOSED_ARCHIVED", False, True),
)

#: (job, key, bucket, force_rank, ordinal, active, swot_origin, evidence)
_SKILLS = (
    # The locked job: six active Must-haves (over the limit), sparse ranks.
    ("locked", "l_python", "must_have", 9, 0, True, "They need Python.", "Shipped Python."),
    ("locked", "l_sql", "must_have", 2, 1, True, None, "Tuned a query."),
    ("locked", "l_kafka", "must_have", None, 2, True, None, None),
    ("locked", "l_go", "must_have", 4, 3, True, None, "Wrote Go."),
    ("locked", "l_rust", "must_have", None, 4, True, None, None),
    ("locked", "l_java", "must_have", 7, 5, True, None, None),
    ("locked", "l_retired", "must_have", 1, 6, False, None, None),
    ("locked", "l_ownership", "behavioural", 3, 0, True, "Owns outcomes.", "Took a brief to done."),
    # The unlocked, saved job: ranks rewritten per bucket, in place.
    ("unlocked_saved", "u_terraform", "must_have", 7, 0, True, None, "Wrote a module."),
    ("unlocked_saved", "u_aws", "must_have", 3, 1, True, "Needs AWS.", None),
    ("unlocked_saved", "u_k8s", "must_have", None, 2, True, None, None),
    ("unlocked_saved", "u_helm", "nice_to_have", 5, 0, True, None, None),
    ("unlocked_saved", "u_calm", "behavioural", 11, 0, True, None, None),
    ("unlocked_saved", "u_gone", "behavioural", 1, 1, False, None, None),
)


def _ratified(published: bool) -> str:
    return "ratified" if published else "draft"


async def _seed(factory) -> _World:
    w = _World()
    async with factory() as session:
        async with superadmin_scope(session):
            await session.execute(
                text(
                    "INSERT INTO tenants (id, name, domain, spf_dkim_status) "
                    "VALUES (:t, :n, :d, 'pending')"
                ),
                {"t": w.tenant, "n": f"m0118-{w.tenant}", "d": f"{w.tenant}.m0118.test"},
            )
            for key, role, status in _USERS:
                user = uuid.uuid4()
                w.users[key] = user
                await session.execute(
                    text(
                        "INSERT INTO users (id, tenant_id, email, full_name, role, status) "
                        "VALUES (:i, :t, :e, 'Migration Person', :r, :s)"
                    ),
                    {"i": user, "t": w.tenant, "e": f"{user.hex[:12]}@m0118.test",
                     "r": role, "s": status},
                )
            for key, creator, published, lifecycle, approved, archived in _JOBS:
                job = uuid.uuid4()
                w.jobs[key] = job
                await session.execute(
                    text(
                        "INSERT INTO jobs (id, tenant_id, title, jd_json, status, "
                        "assessment_grade, created_by, ratified_at, lifecycle_state, "
                        "framework_approved_at, archived_at) VALUES (:j, :t, :title, "
                        "'{}'::jsonb, :status, 'managerial', :by, :ratified, :life, "
                        ":approved, :archived)"
                    ),
                    {
                        "j": job, "t": w.tenant, "title": f"Job {key}",
                        "status": _ratified(published), "by": w.users[creator],
                        "ratified": NOW if published else None, "life": lifecycle,
                        "approved": NOW if approved else None,
                        "archived": NOW if archived else None,
                    },
                )
            # An active holder already exists: the backfill must not add a second.
            await session.execute(
                text(
                    "INSERT INTO job_assignments (tenant_id, job_id, user_id, "
                    "assignment_role, active) VALUES (:t, :j, :u, 'recruiter', true)"
                ),
                {"t": w.tenant, "j": w.jobs["already_assigned"],
                 "u": w.users["other_recruiter"]},
            )
            for job_key, key, bucket, rank, ordinal, active, origin, evidence in _SKILLS:
                skill = uuid.uuid4()
                w.skills[key] = skill
                await session.execute(
                    text(
                        "INSERT INTO job_competencies (id, tenant_id, job_id, category, name, "
                        "required_level, ordinal, is_active, force_rank, swot_origin, "
                        "observable_evidence) VALUES (:i, :t, :j, :c, :n, 82, :o, :a, :r, "
                        ":origin, :e)"
                    ),
                    {"i": skill, "t": w.tenant, "j": w.jobs[job_key], "c": bucket,
                     "n": key.split("_", 1)[1].title(), "o": ordinal, "a": active,
                     "r": rank, "origin": origin, "e": evidence},
                )
            # Three sessions on the locked job: two started, one invited only.
            for index, started in enumerate((True, True, False)):
                candidate, profile, link = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
                conversation = uuid.uuid4()
                w.conversations[f"c{index}"] = conversation
                if index == 0:
                    w.link = link
                await session.execute(
                    text(
                        "INSERT INTO candidates (id, tenant_id, full_name, email, "
                        "consent_databank) VALUES (:c, :t, 'Subject', :e, false)"
                    ),
                    {"c": candidate, "t": w.tenant, "e": f"{candidate}@m0118.test"},
                )
                await session.execute(
                    text(
                        "INSERT INTO profiles (id, candidate_id, source_tenant_id, "
                        "resume_text) VALUES (:p, :c, :t, 'Python')"
                    ),
                    {"p": profile, "c": candidate, "t": w.tenant},
                )
                await session.execute(
                    text(
                        "INSERT INTO job_candidate_links (id, tenant_id, job_id, "
                        "candidate_id, profile_id, source) VALUES (:l, :t, :j, :c, :p, "
                        "'manual')"
                    ),
                    {"l": link, "t": w.tenant, "j": w.jobs["locked"], "c": candidate,
                     "p": profile},
                )
                await session.execute(
                    text(
                        "INSERT INTO assessment_conversations (id, tenant_id, job_id, "
                        "job_candidate_link_id, grade, status, next_question_index, "
                        "reminders_sent, follow_ups_used, reasks_used, mode, started_at) "
                        "VALUES (:i, :t, :j, :l, 'managerial', 'active', 0, 0, 0, 0, "
                        "'conversational', :s)"
                    ),
                    {"i": conversation, "t": w.tenant, "j": w.jobs["locked"], "l": link,
                     "s": (NOW - timedelta(hours=2 - index)) if started else None},
                )
            await session.execute(
                text(
                    "INSERT INTO assessment_messages (id, tenant_id, conversation_id, "
                    "ordinal, speaker, domain, content, evidence_gap) VALUES (:i, :t, "
                    ":c, 1, 'candidate', 'technical', 'I shipped Python services.', false)"
                ),
                {"i": w.message, "t": w.tenant, "c": w.conversations["c0"]},
            )
            await session.commit()
    return w


async def _drop(factory, w: _World) -> None:
    async with factory() as session:
        async with superadmin_scope(session):
            # `job_assignments.user_id` is ON DELETE RESTRICT, so the rows the
            # backfill wrote go before the tenant cascade reaches the users.
            await session.execute(
                text("DELETE FROM job_assignments WHERE tenant_id = :t"), {"t": w.tenant}
            )
            await session.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": w.tenant})
            await session.commit()


_DUPLICATE_EVIDENCE = (
    "INSERT INTO evidence_items (id, tenant_id, job_id, link_id, source_type, source_id, "
    "text_ref, provenance, freshness, trust, relevance, status, created_at) VALUES "
    "(:i, :t, :j, :l, 'answer', :s, :r, '{}'::jsonb, '{}'::jsonb, 'observed', 1, "
    "'active', :at)"
)


async def _migrate(engine, w: _World) -> dict:
    """The data steps, scoped to this world, in ONE committed transaction.

    Two live rows for one answer cannot exist beside the index, which is the
    point of the index; they are the state BEFORE 0118. So the index is dropped
    for the seeding, the migration's dedupe runs, and the index is rebuilt with
    the migration's own statement in the same transaction: if the dedupe missed
    a pair, the rebuild fails and so does this test.
    """
    async with engine.begin() as connection:
        await connection.execute(text("SELECT set_config('app.bypass_rls', 'on', true)"))
        await connection.execute(text("DROP INDEX ux_evidence_items_live_answer"))
        ids = []
        for offset in (0, 1):
            evidence = uuid.uuid4()
            ids.append(evidence)
            await connection.execute(
                text(_DUPLICATE_EVIDENCE),
                {"i": evidence, "t": w.tenant, "j": w.jobs["locked"], "l": w.link,
                 "s": w.message, "r": f"assessment_messages:{w.message}",
                 "at": NOW + timedelta(seconds=offset)},
            )
        counts = await connection.run_sync(
            migration.run_data_steps, list(w.jobs.values())
        )
        await connection.execute(
            text(
                "CREATE UNIQUE INDEX ux_evidence_items_live_answer ON evidence_items "
                "(tenant_id, link_id, source_id) "
                "WHERE source_type = 'answer' AND status = 'active' AND link_id IS NOT NULL"
            )
        )
    counts["evidence_ids"] = ids
    return counts


async def _read(factory, sql: str, **params) -> list[dict]:
    async with factory() as session:
        async with superadmin_scope(session):
            rows = (await session.execute(text(sql), params)).mappings().all()
            return [dict(row) for row in rows]


async def test_the_migration_converts_every_matrix_in_place() -> None:
    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    # The database is at head, where 0119 refuses the retired states this
    # world seeds. Admit them (0119's own downgrade CHECK) until the world is
    # dropped; restoring the six-state CHECK afterwards also VALIDATES every
    # remaining row, so a remap that left a retired state behind fails here.
    await _set_lifecycle_check(engine, next_migration._PREVIOUS_STATES)
    try:
        w = await _seed(factory)
    except BaseException:
        await _set_lifecycle_check(engine, next_migration.LIFECYCLE_STATES)
        await engine.dispose()
        raise
    try:
        before = {
            r["id"]: r
            for r in await _read(
                factory,
                "SELECT id, name, category, force_rank, is_active, swot_origin "
                "FROM job_competencies WHERE tenant_id = :t",
                t=w.tenant,
            )
        }
        counts = await _migrate(engine, w)

        # ── Locked: snapshot v1 from the exact rows, sessions bound ──────────
        snapshots = await _read(
            factory,
            "SELECT id, version, source, grade, role_summary, digest, skills_json, "
            "context_json, locked_at, locked_by_conversation_id FROM job_skill_snapshots "
            "WHERE job_id = :j",
            j=w.jobs["locked"],
        )
        assert len(snapshots) == 1
        snap = snapshots[0]
        assert (snap["version"], snap["source"], snap["grade"]) == (1, "migration", "managerial")
        assert snap["role_summary"] == "", "nobody wrote a summary, so none is claimed"
        assert snap["context_json"] == {"generated_by": "migration"}
        assert snap["locked_by_conversation_id"] is None
        must = [s["name"] for s in snap["skills_json"] if s["bucket"] == "must_have"]
        # (force_rank NULLS LAST, ordinal): 2, 4, 7, 9, then NULLs by ordinal.
        assert must == ["Sql", "Go", "Java", "Python", "Kafka", "Rust"]
        assert "Retired" not in {s["name"] for s in snap["skills_json"]}
        assert [s["priority"] for s in snap["skills_json"] if s["bucket"] == "must_have"] == [
            1, 2, 3, 4, 5, 6,
        ]
        conversations = {
            r["id"]: r
            for r in await _read(
                factory,
                "SELECT id, skill_snapshot_id, contract_digest FROM assessment_conversations "
                "WHERE job_id = :j",
                j=w.jobs["locked"],
            )
        }
        for started in ("c0", "c1"):
            row = conversations[w.conversations[started]]
            assert row["skill_snapshot_id"] == snap["id"]
            assert row["contract_digest"] == snap["digest"]
        unstarted = conversations[w.conversations["c2"]]
        assert unstarted["skill_snapshot_id"] is None and unstarted["contract_digest"] is None

        # The contract API reads it back, which RE-DERIVES the digest.
        async with factory() as session:
            async with superadmin_scope(session):
                bound = await contract_api.load_contract_for_conversation(
                    session, w.conversations["c0"]
                )
                assert await contract_api.is_locked(session, w.jobs["locked"])
                live_unlocked = await contract_api.load_contract(
                    session, w.jobs["unlocked_saved"]
                )
                assert await contract_api.skills_saved(session, w.jobs["unlocked_saved"])
        assert bound.locked and bound.version == 1 and bound.digest == snap["digest"]

        after = {
            r["id"]: r
            for r in await _read(
                factory,
                "SELECT id, name, category, force_rank, is_active, authored_by "
                "FROM job_competencies WHERE tenant_id = :t",
                t=w.tenant,
            )
        }
        assert set(after) == set(before), "a row was deleted or inserted"
        # The locked job's rows are history: not re-ranked.
        for key in ("l_python", "l_sql", "l_kafka", "l_go", "l_rust", "l_java", "l_ownership"):
            skill = w.skills[key]
            assert after[skill]["force_rank"] == before[skill]["force_rank"], key

        # ── Unlocked: per-bucket priority, in place ─────────────────────────
        ranks = {key: after[w.skills[key]]["force_rank"] for key in w.skills if key.startswith("u_")}
        assert ranks == {
            "u_aws": 1, "u_terraform": 2, "u_k8s": 3, "u_helm": 1, "u_calm": 1,
            "u_gone": 1,  # inactive: untouched
        }
        assert [(s.name, s.priority) for s in live_unlocked.skills if s.bucket == "must_have"] == [
            ("Aws", 1), ("Terraform", 2), ("K8S", 3),
        ]

        # ── Provenance ──────────────────────────────────────────────────────
        assert after[w.skills["l_python"]]["authored_by"] == "sutra"
        assert after[w.skills["u_aws"]]["authored_by"] == "sutra"
        assert after[w.skills["u_terraform"]]["authored_by"] == "human"

        # ── Jobs: context, draft state, lifecycle ───────────────────────────
        jobs = {
            r["id"]: r
            for r in await _read(
                factory,
                "SELECT id, lifecycle_state, ratified_at, status, assessment_context_json, "
                "skills_draft_status FROM jobs WHERE tenant_id = :t",
                t=w.tenant,
            )
        }
        state = {key: jobs[job]["lifecycle_state"] for key, job in w.jobs.items()}
        assert state == {
            "locked": "PUBLISHED",
            "unlocked_saved": "FINALIZED",
            "published_sent": "PUBLISHED",
            "draft_retired": "DRAFT",
            "already_assigned": "DRAFT",
            "archived": "CLOSED_ARCHIVED",
        }
        for key in ("locked", "published_sent", "archived"):
            assert jobs[w.jobs[key]]["ratified_at"] is not None, f"{key} was unpublished"
            assert jobs[w.jobs[key]]["status"] == "ratified"
        assert jobs[w.jobs["unlocked_saved"]]["assessment_context_json"] == {
            "role_summary": "", "generated_by": "migration",
        }
        assert jobs[w.jobs["draft_retired"]]["assessment_context_json"] is None
        assert jobs[w.jobs["locked"]]["skills_draft_status"] == "drafted"
        assert jobs[w.jobs["draft_retired"]]["skills_draft_status"] == "not_started"

        # ── Assignments ─────────────────────────────────────────────────────
        assignments = await _read(
            factory,
            "SELECT job_id, user_id, assignment_role FROM job_assignments "
            "WHERE tenant_id = :t AND active",
            t=w.tenant,
        )
        assert sorted(
            (a["job_id"], a["user_id"], a["assignment_role"]) for a in assignments
        ) == sorted(
            [
                (w.jobs["locked"], w.users["hiring_manager"], "hiring_manager"),
                (w.jobs["unlocked_saved"], w.users["recruiter"], "recruiter"),
                (w.jobs["archived"], w.users["recruiter"], "recruiter"),
                (w.jobs["already_assigned"], w.users["other_recruiter"], "recruiter"),
            ]
        )

        # ── Evidence: superseded onto the earliest, never deleted ───────────
        evidence = await _read(
            factory,
            "SELECT id, status, superseded_by FROM evidence_items WHERE source_id = :m "
            "ORDER BY created_at",
            m=w.message,
        )
        first, second = counts["evidence_ids"]
        assert [(e["id"], e["status"], e["superseded_by"]) for e in evidence] == [
            (first, "active", None),
            (second, "superseded", first),
        ]

        # ── Overflow: reported, not truncated, and the script agrees ────────
        reported = {(r["job_id"], r["category"], r["active"], r["locked"]) for r in counts["over_limit"]}
        assert reported == {(w.jobs["locked"], "must_have", 6, True)}
        async with factory() as session:
            async with superadmin_scope(session):
                scripted = {
                    (r.job_id, r.bucket, r.active, r.locked)
                    for r in await skills_overflow_report.overflow_rows(session)
                    if r.tenant_id == w.tenant
                }
        assert scripted == reported

        # ── Idempotent: a second run changes nothing ────────────────────────
        again = await _migrate_again(engine, w)
        assert again["locked_jobs"] == []
        assert again["reranked_skills"] == 0
        assert again["authored_by_sutra"] == 0
        assert again["drafted_jobs"] == 0
        assert again["saved_context_stamped"] == 0
        assert again["creator_assignments"] == 0
        assert again["superseded_answer_evidence"] == 0
        assert again["lifecycle"] == {"published": 0, "drafted": 0, "finalized": 0}
        assert len(
            await _read(
                factory, "SELECT id FROM job_skill_snapshots WHERE job_id = :j",
                j=w.jobs["locked"],
            )
        ) == 1
    finally:
        try:
            await _drop(factory, w)
        finally:
            await _set_lifecycle_check(engine, next_migration.LIFECYCLE_STATES)
            await engine.dispose()


async def _migrate_again(engine, w: _World) -> dict:
    async with engine.begin() as connection:
        await connection.execute(text("SELECT set_config('app.bypass_rls', 'on', true)"))
        return await connection.run_sync(migration.run_data_steps, list(w.jobs.values()))


# ── The round trip ───────────────────────────────────────────────────────────


def _round_trip(connection) -> None:
    """downgrade() then upgrade() of THIS revision, inside the caller's
    transaction, which is rolled back afterwards: DDL is transactional in
    Postgres, so the shared test database is left exactly as it was."""
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        migration.downgrade()
        inspector = sa_inspect(connection)
        assert not inspector.has_table("job_skill_snapshots")
        assert "skill_snapshot_id" not in {
            c["name"] for c in inspector.get_columns("assessment_conversations")
        }
        assert "authored_by" not in {c["name"] for c in inspector.get_columns("job_competencies")}
        migration.upgrade()
    inspector = sa_inspect(connection)
    assert inspector.has_table("job_skill_snapshots")
    assert {"skill_snapshot_id", "contract_digest"} <= {
        c["name"] for c in inspector.get_columns("assessment_conversations")
    }
    assert "ux_evidence_items_live_answer" in {
        i["name"] for i in inspector.get_indexes("evidence_items")
    }
    trigger = connection.execute(
        text(
            "SELECT count(*) FROM pg_trigger WHERE tgname = 'trg_job_skill_snapshots_immutable'"
        )
    ).scalar_one()
    assert trigger == 1
    privileges = connection.execute(
        text(
            "SELECT has_table_privilege('pickready_app', 'job_skill_snapshots', 'UPDATE'), "
            "relforcerowsecurity FROM pg_class WHERE relname = 'job_skill_snapshots'"
        )
    ).one()
    assert tuple(privileges) == (False, True)


async def test_the_migration_round_trips() -> None:
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await connection.execute(text("SELECT set_config('app.bypass_rls', 'on', true)"))
                await connection.run_sync(_round_trip)
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.mark.parametrize("state", ["SENT_TO_HIRING_MANAGER", "IN_REVIEW", "FINALIZED", "DRAFT"])
def test_every_pre_publication_state_of_a_live_job_goes_forward(state: str) -> None:
    """The remap's first statement covers every state a published job could be
    left in, so none of them can reach the retired-state step that sends a row
    to DRAFT."""
    assert state in migration._PRE_PUBLICATION_STATES
