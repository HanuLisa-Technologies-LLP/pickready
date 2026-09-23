"""The assessment contract: one read API, a lock that happens once, and a
snapshot the database itself refuses to rewrite.

Every database assertion that matters reads from a SECOND connection after the
writer committed, because a write that answered and then rolled back at commit
is invisible to the connection that made it (the 2026-09-20 audit_log class).

No reachability skip, deliberately: this is the contract Vaada and Miti both
read, and a green run that never touched Postgres would say nothing about it.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.db import superadmin_scope
from app.services import assessment_contract as contract_api
from app.services.assessment_contract import (
    AssessmentContract,
    ContractIntegrityError,
    ContractNotBound,
    ContractNotReady,
    ContractSkill,
    SkillsLocked,
)


# ── The world ────────────────────────────────────────────────────────────────


@dataclass
class _World:
    tenant: uuid.UUID = field(default_factory=uuid.uuid4)
    job: uuid.UUID = field(default_factory=uuid.uuid4)
    candidates: list[uuid.UUID] = field(default_factory=list)
    links: list[uuid.UUID] = field(default_factory=list)
    conversations: list[uuid.UUID] = field(default_factory=list)
    skills: dict[str, uuid.UUID] = field(default_factory=dict)


#: (bucket, name, force_rank, ordinal, evidence, active)
_SKILLS = (
    ("must_have", "Python", 2, 0, "Has shipped Python services to production.", True),
    ("must_have", "SQL", 1, 1, "Has tuned a slow query with a plan.", True),
    ("must_have", "Kafka", None, 2, "", True),
    ("nice_to_have", "Terraform", 5, 0, "Has written a module used by others.", True),
    ("behavioural", "Ownership", 3, 0, "Took a brief to a shipped outcome.", True),
    ("behavioural", "Retired skill", 4, 1, "Removed by the reviewer.", False),
)


async def _factory():
    engine = create_async_engine(get_settings().database_url)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed(factory, *, saved: bool = True, conversations: int = 1,
                started: bool = False, grade: str = "managerial") -> _World:
    w = _World()
    async with factory() as session:
        async with superadmin_scope(session):
            await session.execute(
                text(
                    "INSERT INTO tenants (id, name, domain, spf_dkim_status) "
                    "VALUES (:t, :n, :d, 'pending')"
                ),
                {"t": w.tenant, "n": f"contract-{w.tenant}", "d": f"{w.tenant}.contract.test"},
            )
            await session.execute(
                text(
                    "INSERT INTO jobs (id, tenant_id, title, jd_json, status, "
                    "assessment_grade, framework_approved_at, assessment_context_json) "
                    "VALUES (:j, :t, 'Data Engineer', '{}'::jsonb, 'draft', :g, "
                    ":approved, CAST(:ctx AS jsonb))"
                ),
                {
                    "j": w.job, "t": w.tenant, "g": grade,
                    "approved": datetime.now(timezone.utc) if saved else None,
                    "ctx": (
                        '{"role_summary": "Owns the batch platform.", "generated_by": "sutra"}'
                        if saved else None
                    ),
                },
            )
            for bucket, name, rank, ordinal, evidence, active in _SKILLS:
                skill_id = uuid.uuid4()
                w.skills[name] = skill_id
                await session.execute(
                    text(
                        "INSERT INTO job_competencies (id, tenant_id, job_id, category, "
                        "name, required_level, ordinal, is_active, observable_evidence, "
                        "force_rank) VALUES (:i, :t, :j, :c, :n, 82, :o, :a, :e, :r)"
                    ),
                    {"i": skill_id, "t": w.tenant, "j": w.job, "c": bucket, "n": name,
                     "o": ordinal, "a": active, "e": evidence or None, "r": rank},
                )
            for _ in range(conversations):
                # One candidate per session: an application is unique per
                # (job, candidate), and two candidates starting at once is the
                # race the lock exists for.
                candidate, profile = uuid.uuid4(), uuid.uuid4()
                link, conversation = uuid.uuid4(), uuid.uuid4()
                w.candidates.append(candidate)
                w.links.append(link)
                w.conversations.append(conversation)
                await session.execute(
                    text(
                        "INSERT INTO candidates (id, tenant_id, full_name, email, "
                        "consent_databank) VALUES (:c, :t, 'Contract Subject', :e, false)"
                    ),
                    {"c": candidate, "t": w.tenant, "e": f"{candidate}@contract.test"},
                )
                await session.execute(
                    text(
                        "INSERT INTO profiles (id, candidate_id, source_tenant_id, resume_text) "
                        "VALUES (:p, :c, :t, 'Python and SQL')"
                    ),
                    {"p": profile, "c": candidate, "t": w.tenant},
                )
                await session.execute(
                    text(
                        "INSERT INTO job_candidate_links "
                        "(id, tenant_id, job_id, candidate_id, profile_id, source) "
                        "VALUES (:l, :t, :j, :c, :p, 'manual')"
                    ),
                    {"l": link, "t": w.tenant, "j": w.job, "c": candidate, "p": profile},
                )
                await session.execute(
                    text(
                        "INSERT INTO assessment_conversations "
                        "(id, tenant_id, job_id, job_candidate_link_id, grade, status, "
                        "next_question_index, reminders_sent, follow_ups_used, "
                        "reasks_used, mode, started_at) VALUES (:i, :t, :j, :l, :g, "
                        "'active', 0, 0, 0, 0, 'conversational', :s)"
                    ),
                    {"i": conversation, "t": w.tenant, "j": w.job, "l": link, "g": grade,
                     "s": datetime.now(timezone.utc) if started else None},
                )
            await session.commit()
    return w


async def _drop(factory, w: _World) -> None:
    async with factory() as session:
        async with superadmin_scope(session):
            # The tenant cascade is also the proof that a snapshot is deletable
            # by the cascade of its owner and by nothing else.
            await session.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": w.tenant})
            await session.commit()


async def _snapshot_rows(factory, job_id: uuid.UUID) -> list[dict]:
    async with factory() as session:
        async with superadmin_scope(session):
            rows = (
                await session.execute(
                    text(
                        "SELECT id, version, digest, source, grade, role_summary, "
                        "locked_by_conversation_id FROM job_skill_snapshots "
                        "WHERE job_id = :j ORDER BY version"
                    ),
                    {"j": job_id},
                )
            ).mappings().all()
            return [dict(row) for row in rows]


async def _lock(factory, w: _World, conversation: uuid.UUID) -> AssessmentContract:
    async with factory() as session:
        async with superadmin_scope(session):
            locked = await contract_api.lock_contract(session, w.job, conversation)
            await session.commit()
            return locked


# ── Pure halves ──────────────────────────────────────────────────────────────


def _skill(name: str, bucket: str, priority: int, evidence: str = "e") -> ContractSkill:
    return ContractSkill(
        id=uuid.UUID(int=abs(hash((name, bucket))) % (1 << 128)),
        name=name, bucket=bucket, priority=priority, evidence_line=evidence,
    )


def test_the_buckets_are_the_categories_the_matrix_stores() -> None:
    from app.services import ppi

    assert contract_api.BUCKETS == ppi.CATEGORIES


def test_the_digest_is_stable_across_input_order() -> None:
    skills = [
        _skill("Python", "must_have", 1),
        _skill("SQL", "must_have", 2),
        _skill("Ownership", "behavioural", 1),
        _skill("Terraform", "nice_to_have", 1),
    ]
    forward = contract_api.compute_digest(skills, "summary", "managerial")
    backward = contract_api.compute_digest(list(reversed(skills)), "summary", "managerial")
    assert forward == backward
    assert len(forward) == 64


@pytest.mark.parametrize(
    "change",
    ["name", "bucket", "priority", "evidence", "role_summary", "grade"],
)
def test_every_content_field_moves_the_digest(change: str) -> None:
    base = [_skill("Python", "must_have", 1, "shipped"), _skill("SQL", "must_have", 2)]
    before = contract_api.compute_digest(base, "summary", "managerial")
    skills, summary, grade = list(base), "summary", "managerial"
    first = skills[0]
    if change == "name":
        skills[0] = ContractSkill(first.id, "Go", first.bucket, first.priority, first.evidence_line)
    elif change == "bucket":
        skills[0] = ContractSkill(first.id, first.name, "nice_to_have", first.priority, first.evidence_line)
    elif change == "priority":
        skills[0] = ContractSkill(first.id, first.name, first.bucket, 3, first.evidence_line)
    elif change == "evidence":
        skills[0] = ContractSkill(first.id, first.name, first.bucket, first.priority, "tuned")
    elif change == "role_summary":
        summary = "different"
    else:
        grade = "cxo"
    assert contract_api.compute_digest(skills, summary, grade) != before


def test_the_digest_line_has_one_exact_format(caplog) -> None:
    """The one line a log query pairs Vaada and Miti on. Identifiers, a version
    and a hash, and nothing a candidate or a skill could be read out of."""
    contract = AssessmentContract(
        job_id=uuid.uuid4(), version=3, locked=True,
        skills=(ContractSkill(uuid.uuid4(), "Python", "must_have", 1, "shipped"),),
        role_summary="Owns the batch platform.",
        digest="a" * 64, grade="managerial", locked_at=None,
    )
    conversation = uuid.uuid4()
    caplog.set_level(logging.INFO, logger=contract_api.__name__)
    contract_api.log_digest(contract_api.STAGE_VAADA, conversation, contract)
    contract_api.log_digest(contract_api.STAGE_MITI, conversation, contract)
    lines = [r.getMessage() for r in caplog.records if r.name == contract_api.__name__]
    assert lines == [
        f"assessment_contract.digest stage=vaada conversation_id={conversation} "
        f"job_id={contract.job_id} version=3 contract_digest={'a' * 64}",
        f"assessment_contract.digest stage=miti conversation_id={conversation} "
        f"job_id={contract.job_id} version=3 contract_digest={'a' * 64}",
    ]
    for line in lines:
        assert "Python" not in line and "shipped" not in line and "batch" not in line
    with pytest.raises(ValueError):
        contract_api.log_digest("siddhi", conversation, contract)


def test_the_contract_carries_the_fields_every_reader_needs() -> None:
    assert set(AssessmentContract.__dataclass_fields__) == {
        "job_id", "version", "locked", "skills", "role_summary", "digest",
        "grade", "locked_at",
    }
    assert set(ContractSkill.__dataclass_fields__) == {
        "id", "name", "bucket", "priority", "evidence_line",
    }


# ── The live contract ────────────────────────────────────────────────────────


async def test_an_unlocked_job_reads_its_live_rows_with_dense_priorities() -> None:
    engine, factory = await _factory()
    w = await _seed(factory)
    try:
        async with factory() as session:
            async with superadmin_scope(session):
                live = await contract_api.load_contract(session, w.job)
                assert await contract_api.skills_saved(session, w.job) is True
                assert await contract_api.is_locked(session, w.job) is False
                await contract_api.require_unlocked(session, w.job)
        assert live.version == 0 and live.locked is False and live.locked_at is None
        assert live.grade == "managerial"
        assert live.role_summary == "Owns the batch platform."
        by_name = {skill.name: skill for skill in live.skills}
        assert "Retired skill" not in by_name, "an inactive row is not in the contract"
        # force_rank orders a bucket, NULL last, and the priority is dense.
        assert [(s.name, s.priority) for s in live.skills if s.bucket == "must_have"] == [
            ("SQL", 1), ("Python", 2), ("Kafka", 3),
        ]
        assert by_name["Terraform"].priority == 1
        assert by_name["Ownership"].priority == 1
        assert by_name["Kafka"].evidence_line == ""
        assert [s.bucket for s in live.skills] == [
            "must_have", "must_have", "must_have", "nice_to_have", "behavioural",
        ]
        assert live.digest == contract_api.compute_digest(
            live.skills, live.role_summary, live.grade
        )
    finally:
        await _drop(factory, w)
        await engine.dispose()


# ── The lock ─────────────────────────────────────────────────────────────────


async def test_the_lock_is_idempotent_and_the_digest_survives_it() -> None:
    engine, factory = await _factory()
    w = await _seed(factory, conversations=2)
    try:
        async with factory() as session:
            async with superadmin_scope(session):
                live = await contract_api.load_contract(session, w.job)
        first = await _lock(factory, w, w.conversations[0])
        again = await _lock(factory, w, w.conversations[0])
        other = await _lock(factory, w, w.conversations[1])

        assert first.locked and first.version == 1
        assert first.digest == live.digest, "a lock must not change the content digest"
        assert again == first
        assert other.digest == first.digest and other.version == 1

        rows = await _snapshot_rows(factory, w.job)
        assert len(rows) == 1, rows
        assert rows[0]["source"] == "lock"
        assert rows[0]["grade"] == "managerial"
        assert rows[0]["role_summary"] == "Owns the batch platform."
        assert rows[0]["locked_by_conversation_id"] == w.conversations[0]

        async with factory() as session:
            async with superadmin_scope(session):
                bound = (
                    await session.execute(
                        text(
                            "SELECT skill_snapshot_id, contract_digest "
                            "FROM assessment_conversations WHERE job_id = :j"
                        ),
                        {"j": w.job},
                    )
                ).all()
                audits = (
                    await session.execute(
                        text(
                            "SELECT actor_user_id, actor_role, agent_name, "
                            "application_id, metadata_json FROM audit_log "
                            "WHERE action = :a AND job_id = :j"
                        ),
                        {"a": contract_api.AUDIT_SKILLS_LOCKED, "j": w.job},
                    )
                ).mappings().all()
                assert await contract_api.is_locked(session, w.job) is True
                with pytest.raises(SkillsLocked):
                    await contract_api.require_unlocked(session, w.job)
        assert {tuple(row) for row in bound} == {(rows[0]["id"], first.digest)}
        assert len(audits) == 1, "one lock, one audit row, written in ONE insert"
        row = audits[0]
        assert row["actor_user_id"] is None and row["agent_name"] is None
        assert row["actor_role"] == "candidate"
        assert row["application_id"] == w.links[0]
        assert row["metadata_json"]["contract_digest"] == first.digest
        assert row["metadata_json"]["conversation_id"] == str(w.conversations[0])
    finally:
        await _drop(factory, w)
        await engine.dispose()


async def test_an_unsaved_job_cannot_be_locked_and_nothing_is_written() -> None:
    engine, factory = await _factory()
    w = await _seed(factory, saved=False)
    try:
        async with factory() as session:
            async with superadmin_scope(session):
                assert await contract_api.skills_saved(session, w.job) is False
                with pytest.raises(ContractNotReady) as refused:
                    await contract_api.lock_contract(session, w.job, w.conversations[0])
                await session.rollback()
        assert str(refused.value) == contract_api.CONTRACT_NOT_READY
        assert await _snapshot_rows(factory, w.job) == []
    finally:
        await _drop(factory, w)
        await engine.dispose()


async def test_two_candidates_starting_at_once_write_one_snapshot() -> None:
    """Two transactions, two conversations, one job. The advisory lock makes
    the second wait for the first's COMMIT and then find its snapshot."""
    engine, factory = await _factory()
    w = await _seed(factory, conversations=2)
    try:
        results = await asyncio.gather(
            _lock(factory, w, w.conversations[0]),
            _lock(factory, w, w.conversations[1]),
        )
        assert results[0].digest == results[1].digest
        assert len(await _snapshot_rows(factory, w.job)) == 1
    finally:
        await _drop(factory, w)
        await engine.dispose()


# ── Immutability, asserted from a second connection ─────────────────────────


async def test_the_app_role_holds_no_update_grant_on_snapshots() -> None:
    engine, factory = await _factory()
    try:
        async with factory() as session:
            granted = (
                await session.execute(
                    text(
                        "SELECT has_table_privilege('pickready_app', "
                        "'job_skill_snapshots', 'UPDATE'), "
                        "has_table_privilege('pickready_app', "
                        "'job_skill_snapshots', 'INSERT')"
                    )
                )
            ).one()
        assert tuple(granted) == (False, True)
    finally:
        await engine.dispose()


async def test_a_snapshot_cannot_be_updated_or_deleted_directly() -> None:
    engine, factory = await _factory()
    w = await _seed(factory)
    try:
        await _lock(factory, w, w.conversations[0])
        before = await _snapshot_rows(factory, w.job)
        # Plain connection, no SET ROLE: the owner, for whom only the trigger
        # stands in the way. That is the half a grant cannot provide.
        for statement in (
            "UPDATE job_skill_snapshots SET role_summary = 'rewritten' WHERE job_id = :j",
            "DELETE FROM job_skill_snapshots WHERE job_id = :j",
        ):
            async with factory() as session:
                await session.execute(text("SELECT set_config('app.bypass_rls', 'on', true)"))
                with pytest.raises(DBAPIError) as refused:
                    await session.execute(text(statement), {"j": w.job})
                assert "insert-only" in str(refused.value)
                await session.rollback()
        # And as the application role, the revoked grant refuses the UPDATE.
        async with factory() as session:
            async with superadmin_scope(session):
                with pytest.raises(DBAPIError) as refused:
                    await session.execute(
                        text("UPDATE job_skill_snapshots SET grade = 'cxo' WHERE job_id = :j"),
                        {"j": w.job},
                    )
                await session.rollback()
        assert "permission denied" in str(refused.value).lower() or "insert-only" in str(refused.value)
        assert await _snapshot_rows(factory, w.job) == before
    finally:
        await _drop(factory, w)
        await engine.dispose()
    # The cascade from the tenant went through the trigger and succeeded.
    engine, factory = await _factory()
    try:
        assert await _snapshot_rows(factory, w.job) == []
    finally:
        await engine.dispose()


async def test_the_refusal_is_the_trigger_and_nothing_else() -> None:
    """THE MUTATION CHECK, run on every pass rather than once by hand. With the
    trigger disabled inside one transaction the owner's UPDATE and DELETE go
    through, so the refusals asserted above are the trigger's and not an
    accident of grants, RLS or a missing row. Rolled back: nothing changes."""
    engine, factory = await _factory()
    w = await _seed(factory)
    try:
        await _lock(factory, w, w.conversations[0])
        before = await _snapshot_rows(factory, w.job)
        async with factory() as session:
            await session.execute(text("SELECT set_config('app.bypass_rls', 'on', true)"))
            await session.execute(
                text(
                    "ALTER TABLE job_skill_snapshots "
                    "DISABLE TRIGGER trg_job_skill_snapshots_immutable"
                )
            )
            updated = await session.execute(
                text(
                    "UPDATE job_skill_snapshots SET role_summary = 'rewritten' "
                    "WHERE job_id = :j"
                ),
                {"j": w.job},
            )
            deleted = await session.execute(
                text("DELETE FROM job_skill_snapshots WHERE job_id = :j"), {"j": w.job}
            )
            assert updated.rowcount == 1 and deleted.rowcount == 1
            await session.rollback()
        assert await _snapshot_rows(factory, w.job) == before
    finally:
        await _drop(factory, w)
        await engine.dispose()


# ── Reading a conversation's contract ────────────────────────────────────────


async def test_a_bound_conversation_keeps_its_snapshot_after_the_rows_change() -> None:
    engine, factory = await _factory()
    w = await _seed(factory)
    try:
        locked = await _lock(factory, w, w.conversations[0])
        async with factory() as session:
            async with superadmin_scope(session):
                await session.execute(
                    text("UPDATE job_competencies SET name = 'Rust' WHERE id = :i"),
                    {"i": w.skills["Python"]},
                )
                # The grade is locked WITH the skills (D5): a later change to
                # the job's grade does not move a started candidate's budget.
                await session.execute(
                    text(
                        "UPDATE jobs SET assessment_grade = 'cxo', "
                        "assessment_context_json = CAST(:ctx AS jsonb) WHERE id = :j"
                    ),
                    {"j": w.job, "ctx": '{"role_summary": "Rewritten later."}'},
                )
                await session.commit()
        async with factory() as session:
            async with superadmin_scope(session):
                read = await contract_api.load_contract_for_conversation(
                    session, w.conversations[0]
                )
                latest = await contract_api.load_contract(session, w.job)
        assert read == locked
        assert latest == locked, "a locked job answers from its snapshot"
        assert read.grade == "managerial"
        assert read.role_summary == "Owns the batch platform."
        assert "Python" in {s.name for s in read.skills}
        assert "Rust" not in {s.name for s in read.skills}
    finally:
        await _drop(factory, w)
        await engine.dispose()


async def test_an_unstarted_conversation_reads_the_current_contract() -> None:
    engine, factory = await _factory()
    w = await _seed(factory)
    try:
        async with factory() as session:
            async with superadmin_scope(session):
                read = await contract_api.load_contract_for_conversation(
                    session, w.conversations[0]
                )
                live = await contract_api.load_contract(session, w.job)
        assert read == live and read.locked is False
    finally:
        await _drop(factory, w)
        await engine.dispose()


async def test_a_started_conversation_with_no_binding_is_refused() -> None:
    engine, factory = await _factory()
    w = await _seed(factory, started=True)
    try:
        async with factory() as session:
            async with superadmin_scope(session):
                with pytest.raises(ContractNotBound):
                    await contract_api.load_contract_for_conversation(
                        session, w.conversations[0]
                    )
    finally:
        await _drop(factory, w)
        await engine.dispose()


async def test_vaada_and_miti_log_the_same_digest_for_one_conversation(caplog) -> None:
    """The start (Vaada) and the grade (Miti) read the SAME contract. Each logs
    its digest line; the two lines differ only in the stage."""
    engine, factory = await _factory()
    w = await _seed(factory)
    try:
        caplog.set_level(logging.INFO, logger=contract_api.__name__)
        conversation = w.conversations[0]
        at_start = await _lock(factory, w, conversation)
        contract_api.log_digest(contract_api.STAGE_VAADA, conversation, at_start)
        async with factory() as session:
            async with superadmin_scope(session):
                at_grading = await contract_api.load_contract_for_conversation(
                    session, conversation
                )
        contract_api.log_digest(contract_api.STAGE_MITI, conversation, at_grading)
        lines = [
            record.getMessage() for record in caplog.records
            if record.getMessage().startswith("assessment_contract.digest ")
        ]
        assert len(lines) == 2
        fields = [dict(part.split("=", 1) for part in line.split()[1:]) for line in lines]
        assert [f["stage"] for f in fields] == ["vaada", "miti"]
        assert {f["contract_digest"] for f in fields} == {at_start.digest}
        assert {f["conversation_id"] for f in fields} == {str(conversation)}
        assert {f["version"] for f in fields} == {"1"}
    finally:
        await _drop(factory, w)
        await engine.dispose()


async def test_a_lock_on_an_emptied_contract_is_refused() -> None:
    """Saved and then emptied is not a contract anybody can be assessed
    against; locking it would freeze nothing as the criteria."""
    engine, factory = await _factory()
    w = await _seed(factory)
    try:
        async with factory() as session:
            async with superadmin_scope(session):
                await session.execute(
                    text("UPDATE job_competencies SET is_active = false WHERE job_id = :j"),
                    {"j": w.job},
                )
                await session.commit()
        async with factory() as session:
            async with superadmin_scope(session):
                with pytest.raises(ContractNotReady):
                    await contract_api.lock_contract(session, w.job, w.conversations[0])
                await session.rollback()
        assert await _snapshot_rows(factory, w.job) == []
    finally:
        await _drop(factory, w)
        await engine.dispose()


async def test_a_conversation_cannot_lock_another_jobs_contract() -> None:
    engine, factory = await _factory()
    w = await _seed(factory)
    other = await _seed(factory)
    try:
        async with factory() as session:
            async with superadmin_scope(session):
                with pytest.raises(ValueError):
                    await contract_api.lock_contract(session, other.job, w.conversations[0])
                await session.rollback()
        assert await _snapshot_rows(factory, other.job) == []
    finally:
        await _drop(factory, w)
        await _drop(factory, other)
        await engine.dispose()


async def test_a_conversation_whose_recorded_digest_disagrees_is_refused() -> None:
    """The conversation's stored digest is the second record of what it was
    started against. If the two disagree, neither can prove anything, so the
    read raises rather than choosing one."""
    engine, factory = await _factory()
    w = await _seed(factory)
    try:
        await _lock(factory, w, w.conversations[0])
        async with factory() as session:
            async with superadmin_scope(session):
                await session.execute(
                    text(
                        "UPDATE assessment_conversations SET contract_digest = :d "
                        "WHERE id = :c"
                    ),
                    {"d": "0" * 64, "c": w.conversations[0]},
                )
                await session.commit()
        async with factory() as session:
            async with superadmin_scope(session):
                with pytest.raises(ContractIntegrityError):
                    await contract_api.load_contract_for_conversation(
                        session, w.conversations[0]
                    )
    finally:
        await _drop(factory, w)
        await engine.dispose()
