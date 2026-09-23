"""THE per-answer ledger writer: idempotent, isolated, and the only one.

`assessment_pipeline.evidence.record_answer_evidence` runs twice for the same
answer by design: once during the conversation (so the interviewer can read a
ledger that exists while the candidate is still answering) and once as the
scoring backfill. Both must leave ONE evidence row and ONE attachment, because
two rows for one answer read as two pieces of corroboration to anything that
counts support.

Every database assertion reads from a SECOND connection after the writer
committed: a write that answered and then rolled back at commit is invisible
to the connection that made it.
"""
from __future__ import annotations

import ast
import asyncio
import logging
import pathlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.db import superadmin_scope
from app.services import functional_assessment as fa
from app.services.assessment_pipeline import evidence as answer_evidence
from app.services.evidence import ledger

_ANSWER = "I rebuilt the ingest pipeline on Kafka and cut the nightly batch to minutes."
_DENIAL = "I have not used Kafka in production, I only read about it."
_GIBBERISH = "ewidjverip"

APP = pathlib.Path(__file__).resolve().parents[1] / "app"


@dataclass
class _World:
    tenant: uuid.UUID = field(default_factory=uuid.uuid4)
    job: uuid.UUID = field(default_factory=uuid.uuid4)
    candidate: uuid.UUID = field(default_factory=uuid.uuid4)
    profile: uuid.UUID = field(default_factory=uuid.uuid4)
    link: uuid.UUID = field(default_factory=uuid.uuid4)
    conversation: uuid.UUID = field(default_factory=uuid.uuid4)
    skill: uuid.UUID = field(default_factory=uuid.uuid4)
    question: uuid.UUID = field(default_factory=uuid.uuid4)
    messages: dict[str, uuid.UUID] = field(default_factory=dict)


async def _factory():
    engine = create_async_engine(get_settings().database_url)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed(factory, answers: dict[str, str]) -> _World:
    w = _World()
    async with factory() as session:
        async with superadmin_scope(session):
            await session.execute(
                text(
                    "INSERT INTO tenants (id, name, domain, spf_dkim_status) "
                    "VALUES (:t, :n, :d, 'pending')"
                ),
                {"t": w.tenant, "n": f"evidence-{w.tenant}", "d": f"{w.tenant}.evidence.test"},
            )
            await session.execute(
                text(
                    "INSERT INTO jobs (id, tenant_id, title, jd_json, status, assessment_grade) "
                    "VALUES (:j, :t, 'Data Engineer', '{}'::jsonb, 'draft', 'managerial')"
                ),
                {"j": w.job, "t": w.tenant},
            )
            await session.execute(
                text(
                    "INSERT INTO job_competencies (id, tenant_id, job_id, category, name, "
                    "required_level, ordinal) VALUES (:i, :t, :j, 'must_have', 'Kafka', 82, 0)"
                ),
                {"i": w.skill, "t": w.tenant, "j": w.job},
            )
            await session.execute(
                text(
                    "INSERT INTO candidates (id, tenant_id, full_name, email, consent_databank) "
                    "VALUES (:c, :t, 'Evidence Subject', :e, false)"
                ),
                {"c": w.candidate, "t": w.tenant, "e": f"{w.candidate}@evidence.test"},
            )
            await session.execute(
                text(
                    "INSERT INTO profiles (id, candidate_id, source_tenant_id, resume_text) "
                    "VALUES (:p, :c, :t, 'Kafka')"
                ),
                {"p": w.profile, "c": w.candidate, "t": w.tenant},
            )
            await session.execute(
                text(
                    "INSERT INTO job_candidate_links "
                    "(id, tenant_id, job_id, candidate_id, profile_id, source) "
                    "VALUES (:l, :t, :j, :c, :p, 'manual')"
                ),
                {"l": w.link, "t": w.tenant, "j": w.job, "c": w.candidate, "p": w.profile},
            )
            await session.execute(
                text(
                    "INSERT INTO assessment_conversations "
                    "(id, tenant_id, job_id, job_candidate_link_id, grade, status, "
                    "next_question_index, reminders_sent, follow_ups_used, reasks_used, "
                    "mode, started_at) VALUES (:i, :t, :j, :l, 'managerial', 'active', "
                    "0, 0, 0, 0, 'conversational', now())"
                ),
                {"i": w.conversation, "t": w.tenant, "j": w.job, "l": w.link},
            )
            for ordinal, (label, content) in enumerate(answers.items(), start=1):
                message = uuid.uuid4()
                w.messages[label] = message
                await session.execute(
                    text(
                        "INSERT INTO assessment_messages (id, tenant_id, conversation_id, "
                        "ordinal, speaker, domain, question_key, content, evidence_gap) "
                        "VALUES (:i, :t, :c, :o, 'candidate', 'technical', :q, :content, false)"
                    ),
                    {"i": message, "t": w.tenant, "c": w.conversation, "o": ordinal,
                     "q": str(w.question), "content": content},
                )
            await session.commit()
    return w


async def _drop(factory, w: _World) -> None:
    async with factory() as session:
        async with superadmin_scope(session):
            await session.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": w.tenant})
            await session.commit()


def _kwargs(w: _World, label: str, content: str) -> dict:
    return dict(
        tenant_id=w.tenant,
        job_id=w.job,
        link_id=w.link,
        candidate_id=w.candidate,
        skill_id=w.skill,
        skill_name="Kafka",
        skill_bucket="must_have",
        question_id=w.question,
        message_id=w.messages[label],
        turn=1,
        text=content,
        answered_at=datetime.now(timezone.utc),
    )


async def _write(factory, w: _World, label: str, content: str) -> None:
    async with factory() as session:
        async with superadmin_scope(session):
            await answer_evidence.record_answer_evidence(session, **_kwargs(w, label, content))
            await session.commit()


async def _ledger(factory, w: _World) -> dict[str, list]:
    """What is committed, read on a connection that wrote none of it."""
    async with factory() as session:
        async with superadmin_scope(session):
            items = (
                await session.execute(
                    text(
                        "SELECT id, source_id, status, text_ref, provenance "
                        "FROM evidence_items WHERE link_id = :l ORDER BY created_at"
                    ),
                    {"l": w.link},
                )
            ).mappings().all()
            claims = (
                await session.execute(
                    text("SELECT id, dimension, claim FROM evidence_claims WHERE link_id = :l"),
                    {"l": w.link},
                )
            ).mappings().all()
            links = (
                await session.execute(
                    text(
                        "SELECT cl.evidence_id, cl.stance FROM evidence_claim_links cl "
                        "JOIN evidence_claims c ON c.id = cl.claim_id WHERE c.link_id = :l"
                    ),
                    {"l": w.link},
                )
            ).mappings().all()
    return {"items": list(items), "claims": list(claims), "links": list(links)}


# ── Idempotence ──────────────────────────────────────────────────────────────


async def test_calling_it_twice_for_one_message_writes_one_row_and_one_attachment(
    caplog,
) -> None:
    """And the repeat is a clean no-op, not a unique violation absorbed by the
    savepoint: the index alone would also leave one row, while logging a
    failure on every rescore."""
    engine, factory = await _factory()
    w = await _seed(factory, {"answer": _ANSWER})
    try:
        caplog.set_level(logging.INFO, logger=answer_evidence.__name__)
        await _write(factory, w, "answer", _ANSWER)
        first = await _ledger(factory, w)
        await _write(factory, w, "answer", _ANSWER)
        second = await _ledger(factory, w)

        assert len(second["items"]) == 1, second["items"]
        assert len(second["claims"]) == 1
        assert len(second["links"]) == 1
        assert second == first, "a repeat changed a row it should have left alone"
        assert not [r for r in caplog.records if "evidence_not_recorded" in r.getMessage()]
        item = second["items"][0]
        assert item["source_id"] == w.messages["answer"]
        assert item["status"] == "active"
        assert item["text_ref"] == ledger.text_ref(
            table="assessment_messages", row_id=w.messages["answer"]
        )
        assert second["links"][0]["stance"] == ledger.STANCE_SUPPORTS
        assert second["claims"][0]["claim"] == answer_evidence.claim_wording("Kafka")
    finally:
        await _drop(factory, w)
        await engine.dispose()


async def test_two_concurrent_writers_of_one_message_leave_one_row(caplog) -> None:
    """The read-then-insert alone would race; the partial unique index is the
    second line, and the loser reads the winner's row back."""
    engine, factory = await _factory()
    w = await _seed(factory, {"answer": _ANSWER})
    try:
        caplog.set_level(logging.INFO, logger=answer_evidence.__name__)
        await asyncio.gather(
            _write(factory, w, "answer", _ANSWER),
            _write(factory, w, "answer", _ANSWER),
            _write(factory, w, "answer", _ANSWER),
        )
        ledger_rows = await _ledger(factory, w)
        assert not [r for r in caplog.records if "evidence_not_recorded" in r.getMessage()]
        assert len(ledger_rows["items"]) == 1
        assert len(ledger_rows["claims"]) == 1
        assert len(ledger_rows["links"]) == 1
    finally:
        await _drop(factory, w)
        await engine.dispose()


async def test_the_scoring_backfill_after_a_per_answer_write_adds_nothing() -> None:
    """The conversation filed the answer; scoring then walks every located
    answer through the same writer. The second pass is a no-op, and the one
    answer the conversation did not file is added."""
    engine, factory = await _factory()
    w = await _seed(factory, {"answer": _ANSWER, "later": _DENIAL})
    try:
        await _write(factory, w, "answer", _ANSWER)
        async with factory() as session:
            async with superadmin_scope(session):
                located = await answer_evidence.answer_records(session, w.link)
                state = {
                    "session": session,
                    "job": SimpleNamespace(id=w.job, tenant_id=w.tenant),
                    "link": SimpleNamespace(id=w.link, candidate_id=w.candidate),
                }
                competency = SimpleNamespace(id=w.skill, name="Kafka", category="must_have")
                question = SimpleNamespace(id=w.question)
                await fa._record_answer_evidence(
                    state, competency, question, located[str(w.question)]
                )
                await fa._record_answer_evidence(
                    state, competency, question, located[str(w.question)]
                )
                await session.commit()
        rows = await _ledger(factory, w)
        assert [i["source_id"] for i in rows["items"]] == [
            w.messages["answer"], w.messages["later"],
        ]
        assert len(rows["claims"]) == 1
        stances = {r["evidence_id"]: r["stance"] for r in rows["links"]}
        by_message = {i["source_id"]: i["id"] for i in rows["items"]}
        assert stances[by_message[w.messages["answer"]]] == ledger.STANCE_SUPPORTS
        # "I have not used Kafka" is a real answer AND counter-evidence (W6.6).
        assert stances[by_message[w.messages["later"]]] == ledger.STANCE_CONTRADICTS
    finally:
        await _drop(factory, w)
        await engine.dispose()


async def test_a_non_answer_writes_nothing() -> None:
    engine, factory = await _factory()
    w = await _seed(factory, {"mash": _GIBBERISH})
    try:
        await _write(factory, w, "mash", _GIBBERISH)
        assert await _ledger(factory, w) == {"items": [], "claims": [], "links": []}
    finally:
        await _drop(factory, w)
        await engine.dispose()


async def test_the_ledger_never_holds_the_sentence() -> None:
    engine, factory = await _factory()
    w = await _seed(factory, {"answer": _ANSWER})
    try:
        await _write(factory, w, "answer", _ANSWER)
        rows = await _ledger(factory, w)
        flat = repr(rows)
        for fragment in ("ingest pipeline", "nightly batch", _ANSWER):
            assert fragment not in flat
        provenance = rows["items"][0]["provenance"]
        assert provenance["competency_id"] == str(w.skill)
        assert provenance["question_id"] == str(w.question)
        assert provenance["agent"] == answer_evidence.LEDGER_OWNER
    finally:
        await _drop(factory, w)
        await engine.dispose()


# ── Failure isolation ────────────────────────────────────────────────────────


async def test_a_database_failure_is_contained_in_its_savepoint() -> None:
    """An evidence write that Postgres refuses (here: an application id that
    does not exist) must not abort the caller's transaction. The caller's own
    work in the same transaction still commits."""
    engine, factory = await _factory()
    w = await _seed(factory, {"answer": _ANSWER})
    try:
        async with factory() as session:
            async with superadmin_scope(session):
                broken = _kwargs(w, "answer", _ANSWER)
                broken["link_id"] = uuid.uuid4()  # violates the foreign key
                await answer_evidence.record_answer_evidence(session, **broken)
                # The transaction is still usable: the caller's work commits.
                await session.execute(
                    text("UPDATE jobs SET title = 'Still here' WHERE id = :j"), {"j": w.job}
                )
                await session.commit()
        async with factory() as session:
            async with superadmin_scope(session):
                title = (
                    await session.execute(
                        text("SELECT title FROM jobs WHERE id = :j"), {"j": w.job}
                    )
                ).scalar_one()
        assert title == "Still here"
        assert (await _ledger(factory, w))["items"] == []
    finally:
        await _drop(factory, w)
        await engine.dispose()


async def test_a_programming_error_is_never_absorbed(monkeypatch) -> None:
    """A TypeError is not an operational failure (the 2026-09-22 rule). Only a
    database failure is absorbed; anything else propagates."""

    async def _broken(*args, **kwargs):
        raise TypeError("a bug, not an outage")

    monkeypatch.setattr(ledger, "record_evidence", _broken)
    session = SimpleNamespace()
    w = _World(messages={"answer": uuid.uuid4()})
    with pytest.raises(TypeError):
        await answer_evidence.record_answer_evidence(session, **_kwargs(w, "answer", _ANSWER))


# ── One writer ───────────────────────────────────────────────────────────────

_WRITES = {"record_evidence", "record_claim", "attach_evidence"}
_ALLOWED = {
    APP / "services" / "evidence" / "ledger.py",
    APP / "services" / "assessment_pipeline" / "evidence.py",
}


def test_record_answer_evidence_is_the_only_ledger_writer() -> None:
    """No module outside the ledger itself and the one writer calls a ledger
    write. A second writer is how two rows for one answer come back."""
    offenders: list[str] = []
    for path in APP.rglob("*.py"):
        if path in _ALLOWED or "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                if name in _WRITES:
                    offenders.append(f"{path.relative_to(APP)}:{node.lineno} {name}")
    assert offenders == [], offenders


def test_the_writer_runs_its_writes_inside_a_savepoint_and_catches_only_the_database() -> None:
    import inspect

    source = inspect.getsource(answer_evidence.record_answer_evidence)
    assert "async with _savepoint(session):" in source
    assert "except SQLAlchemyError" in source
    assert "except Exception" not in source
    assert "answer_quality.is_substantive" in source
    # Evidence first, then the claim, then the attachment.
    assert (
        source.index("record_evidence(")
        < source.index("record_claim(")
        < source.index("attach_evidence(")
    )
