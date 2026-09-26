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
    # ── What must SURVIVE, and one row that must not ─────────────────────────
    # The billing fact and the consent record are the two things the C5
    # reconciliation refused to delete and the ruling upheld: one answers a
    # billing dispute and names no candidate content, and the other IS the
    # legitimacy of this very deletion. They are seeded on the SUBJECT job, so
    # they are inside the blast radius and have to survive it there rather
    # than surviving by being somewhere else.
    for link, job in (
        (w.subject_link, w.subject_job),
        (w.control_link, w.control_job),
    ):
        await session.execute(
            text(
                "INSERT INTO credit_ledger (id, tenant_id, event_type, "
                "subunits_delta, job_candidate_link_id, idempotency_key) "
                "VALUES (:i, :t, 'completed_assessment', -60, :l, :k)"
            ),
            {"i": str(uuid.uuid4()), "t": str(w.tenant), "l": str(link),
             "k": f"closure-test-{link}"},
        )
    await session.execute(
        text(
            "INSERT INTO assessment_consents (id, tenant_id, candidate_id, "
            "conversation_id, job_candidate_link_id, assessment_mode, "
            "consent_status, consented_at, consent_version, "
            "privacy_policy_version, terms_version) "
            "VALUES (:i, :t, :c, :conv, :l, 'conversational', 'granted', "
            "now(), 'v1', 'v1', 'v1')"
        ),
        {"i": str(uuid.uuid4()), "t": str(w.tenant), "c": str(w.candidate),
         "conv": str(w.subject_conversation), "l": str(w.subject_link)},
    )
    # The recording ROW, which cascades from the conversation. It is seeded
    # here to pin the foreign key that decides the purge ORDER: this row is
    # the only thing in the database naming the S3 object, so a caller that
    # deleted rows before objects would orphan the media permanently.
    #
    # Since migration 0126 the recording also has SEGMENT rows, one per S3
    # multipart upload, and they cascade from the recording: the same order
    # rule, one table further down.
    for conversation, link in (
        (w.subject_conversation, w.subject_link),
        (w.control_conversation, w.control_link),
    ):
        recording_id = uuid.uuid4()
        await session.execute(
            text(
                "INSERT INTO video_recordings (id, tenant_id, conversation_id, "
                "candidate_id, job_candidate_link_id, status, kind, "
                "s3_compressed_key, stored_at) "
                "VALUES (:i, :t, :conv, :c, :l, 'ready', 'proctored_session', "
                ":key, now())"
            ),
            {"i": str(recording_id), "t": str(w.tenant),
             "conv": str(conversation), "c": str(w.candidate),
             "l": str(link), "key": f"assessments/{link}.mp4"},
        )
        await session.execute(
            text(
                "INSERT INTO video_recording_segments (id, tenant_id, "
                "recording_id, ordinal, s3_key, multipart_upload_id, status) "
                "VALUES (:i, :t, :r, 0, :key, 'upload-1', 'completed')"
            ),
            {"i": str(uuid.uuid4()), "t": str(w.tenant), "r": str(recording_id),
             "key": f"assessment-raw/{conversation}/{recording_id}/seg-0000.webm"},
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
        "credit_ledger": await _pair(
            "SELECT COUNT(*) FROM credit_ledger "
            "WHERE job_candidate_link_id = :x",
            w.subject_link, w.control_link,
        ),
        "consents": await _pair(
            "SELECT COUNT(*) FROM assessment_consents "
            "WHERE job_candidate_link_id = :x",
            w.subject_link, w.control_link,
        ),
        "recordings": await _pair(
            "SELECT COUNT(*) FROM video_recordings "
            "WHERE job_candidate_link_id = :x",
            w.subject_link, w.control_link,
        ),
        "segments": await _pair(
            "SELECT COUNT(*) FROM video_recording_segments s "
            "JOIN video_recordings r ON r.id = s.recording_id "
            "WHERE r.job_candidate_link_id = :x",
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
    # And the two records the reconciliation refused to delete, on the SUBJECT
    # job, inside the blast radius. The billing fact answers a billing dispute
    # and names no candidate content; the consent record is the legitimacy of
    # the deletion itself, so destroying it would remove the evidence that the
    # deletion was authorised.
    assert after["credit_ledger"] == (1, 1)
    assert after["consents"][0] == 1
    # THE RECORDING ROW IS GONE, and that is not a defect, it is the foreign
    # key that decides the purge ORDER. `video_recordings.conversation_id` is
    # ON DELETE CASCADE, so this row, the ONLY thing in the database naming
    # the stored mp4, disappears with the conversation. That is why
    # `job_assessment_retention.purge_job` deletes the objects FIRST and calls
    # this function only once the store has confirmed them gone.
    # The CONTROL job's recording row survives, which is what makes the line
    # above a statement about the cascade's scope rather than about nothing.
    assert after["recordings"] == (0, 1)
    # The segment rows go with their recording, for the same reason and with
    # the same consequence: `assessment_media_retention.object_keys_for_job`
    # reads them BEFORE this function runs (tests/test_media_retention_d4.py).
    assert after["segments"] == (0, 1)
    # The receipt counts what happened, honestly.
    assert receipt.reports_deleted == 1
    assert receipt.conversations_deleted == 1
    assert receipt.chunks_deleted == 1
