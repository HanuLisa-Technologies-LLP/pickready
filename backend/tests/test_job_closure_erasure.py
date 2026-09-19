"""What `erasure.job_closure_erasure` actually deletes, against a real schema.

Vivekium C5. Two jobs in one tenant: the SUBJECT is closed and erased, the
CONTROL is not, and the control's rows surviving is the assertion that
matters most, because a deletion that reached the wrong job would satisfy
every "subject is gone" check on its own.

Also pinned: what the function KEEPS. The link rows survive, because
deleting the application history was the half of C5 the reconciliation
refused and the ruling upheld.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import text

VECTOR = "[" + ",".join(["0.01"] * 1024) + "]"


def _run(coro_factory):
    async def _wrapped():
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.core.config import get_settings

        engine = create_async_engine(get_settings().database_url)
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            return await coro_factory(factory)
        finally:
            await engine.dispose()

    return asyncio.run(_wrapped())


def _skip_without_database() -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.config import get_settings

    async def _probe():
        engine = create_async_engine(get_settings().database_url)
        try:
            async with engine.connect():
                return True
        except Exception:  # noqa: BLE001
            return False
        finally:
            await engine.dispose()

    if not asyncio.run(_probe()):
        pytest.skip("no database reachable, skipping job-closure erasure test")


class _World:
    def __init__(self) -> None:
        self.tenant = uuid.uuid4()
        self.subject_job = uuid.uuid4()
        self.control_job = uuid.uuid4()
        self.candidate = uuid.uuid4()
        self.profile = uuid.uuid4()
        self.subject_link = uuid.uuid4()
        self.control_link = uuid.uuid4()
        self.subject_conversation = uuid.uuid4()
        self.control_conversation = uuid.uuid4()


async def _seed(session, w: _World) -> None:
    await session.execute(
        text(
            "INSERT INTO tenants (id, name, domain, spf_dkim_status) "
            "VALUES (:t, :n, :d, 'pending')"
        ),
        {"t": str(w.tenant), "n": f"closure-{w.tenant}",
         "d": f"{w.tenant}.closure.test"},
    )
    for job in (w.subject_job, w.control_job):
        await session.execute(
            text(
                "INSERT INTO jobs (id, tenant_id, title, jd_json, status) "
                "VALUES (:j, :t, 'Backend Engineer', '{}'::jsonb, 'draft')"
            ),
            {"j": str(job), "t": str(w.tenant)},
        )
    await session.execute(
        text(
            "INSERT INTO candidates (id, tenant_id, full_name, email, "
            "consent_databank) VALUES (:c, :t, 'Closure Subject', :e, false)"
        ),
        {"c": str(w.candidate), "t": str(w.tenant),
         "e": f"{w.candidate}@closure.test"},
    )
    await session.execute(
        text(
            "INSERT INTO profiles (id, candidate_id, source_tenant_id, "
            "resume_text) VALUES (:p, :c, :t, 'Kafka and Postgres')"
        ),
        {"p": str(w.profile), "c": str(w.candidate), "t": str(w.tenant)},
    )
    for link, job in (
        (w.subject_link, w.subject_job),
        (w.control_link, w.control_job),
    ):
        await session.execute(
            text(
                "INSERT INTO job_candidate_links "
                "(id, tenant_id, job_id, candidate_id, profile_id, source) "
                "VALUES (:l, :t, :j, :c, :p, 'manual')"
            ),
            {"l": str(link), "t": str(w.tenant), "j": str(job),
             "c": str(w.candidate), "p": str(w.profile)},
        )
    for conversation, link in (
        (w.subject_conversation, w.subject_link),
        (w.control_conversation, w.control_link),
    ):
        job = w.subject_job if link == w.subject_link else w.control_job
        await session.execute(
            text(
                "INSERT INTO assessment_conversations "
                "(id, tenant_id, job_id, job_candidate_link_id, grade, status, "
                "next_question_index, reminders_sent, follow_ups_used, "
                "reasks_used, mode) VALUES (:i, :t, :j, :l, 'non_managerial', "
                "'active', 0, 0, 0, 0, 'conversational')"
            ),
            {"i": str(conversation), "t": str(w.tenant), "j": str(job),
             "l": str(link)},
        )
        await session.execute(
            text(
                "INSERT INTO assessment_messages "
                "(id, tenant_id, conversation_id, ordinal, speaker, domain, "
                "content, evidence_gap) VALUES (:i, :t, :c, 0, 'candidate', "
                "'technical', 'I built the pipeline.', false)"
            ),
            {"i": str(uuid.uuid4()), "t": str(w.tenant),
             "c": str(conversation)},
        )
    for link, job in (
        (w.subject_link, w.subject_job),
        (w.control_link, w.control_job),
    ):
        await session.execute(
            text(
                "INSERT INTO functional_skills_reports "
                "(id, tenant_id, job_id, job_candidate_link_id, grade, "
                "overall_summary, synthesized_at) "
                "VALUES (:i, :t, :j, :l, 'non_managerial', "
                "'A summary of the closed assessment.', now())"
            ),
            {"i": str(uuid.uuid4()), "t": str(w.tenant), "j": str(job),
             "l": str(link)},
        )
        await session.execute(
            text(
                "INSERT INTO context_chunks (id, tenant_id, source_type, "
                "source_id, source_version, section_type, ordinal, content, "
                "content_sha256, embedding) "
                "VALUES (:i, :t, 'assessment', :sid, 'v1', 'experience', 0, "
                "'I built the pipeline.', :h, CAST(:v AS vector))"
            ),
            {"i": str(uuid.uuid4()), "t": str(w.tenant), "sid": str(link),
             "h": uuid.uuid4().hex + uuid.uuid4().hex, "v": VECTOR},
        )


async def _counts(session, w: _World) -> dict[str, tuple[int, int]]:
    async def _pair(sql: str, subject, control) -> tuple[int, int]:
        s = (
            await session.execute(text(sql), {"x": str(subject)})
        ).scalar_one()
        c = (
            await session.execute(text(sql), {"x": str(control)})
        ).scalar_one()
        return int(s), int(c)

    return {
        "reports": await _pair(
            "SELECT COUNT(*) FROM functional_skills_reports WHERE job_id = :x",
            w.subject_job, w.control_job,
        ),
        "conversations": await _pair(
            "SELECT COUNT(*) FROM assessment_conversations "
            "WHERE job_candidate_link_id = :x",
            w.subject_link, w.control_link,
        ),
        "messages": await _pair(
            "SELECT COUNT(*) FROM assessment_messages WHERE conversation_id = :x",
            w.subject_conversation, w.control_conversation,
        ),
        "chunks": await _pair(
            "SELECT COUNT(*) FROM context_chunks "
            "WHERE source_type = 'assessment' AND source_id = :x",
            w.subject_link, w.control_link,
        ),
        "links": await _pair(
            "SELECT COUNT(*) FROM job_candidate_links WHERE id = :x",
            w.subject_link, w.control_link,
        ),
    }


def test_closure_erasure_deletes_the_subject_and_only_the_subject() -> None:
    from app.core.db import superadmin_scope
    from app.services import erasure

    _skip_without_database()
    w = _World()

    async def _flow(factory):
        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await _seed(session, w)
        try:
            async with factory() as session:
                async with session.begin():
                    async with superadmin_scope(session):
                        receipt = await erasure.job_closure_erasure(
                            session, job_id=w.subject_job
                        )
            async with factory() as session:
                async with superadmin_scope(session):
                    after = await _counts(session, w)
            return receipt, after
        finally:
            async with factory() as session:
                async with session.begin():
                    async with superadmin_scope(session):
                        await session.execute(
                            text("DELETE FROM candidates WHERE id = :c"),
                            {"c": str(w.candidate)},
                        )
                        for job in (w.subject_job, w.control_job):
                            await session.execute(
                                text("DELETE FROM jobs WHERE id = :j"),
                                {"j": str(job)},
                            )
                        await session.execute(
                            text("DELETE FROM tenants WHERE id = :t"),
                            {"t": str(w.tenant)},
                        )

    receipt, after = _run(_flow)

    # The subject job's assessment artifacts are GONE, cascades included.
    assert after["reports"][0] == 0
    assert after["conversations"][0] == 0
    assert after["messages"][0] == 0
    assert after["chunks"][0] == 0
    # The control job, same tenant, same candidate, is UNTOUCHED.
    assert after["reports"][1] == 1
    assert after["conversations"][1] == 1
    assert after["messages"][1] == 1
    assert after["chunks"][1] == 1
    # What the ruling keeps: both application rows survive.
    assert after["links"] == (1, 1)
    # The receipt counts what happened, honestly.
    assert receipt.reports_deleted == 1
    assert receipt.conversations_deleted == 1
    assert receipt.chunks_deleted == 1
