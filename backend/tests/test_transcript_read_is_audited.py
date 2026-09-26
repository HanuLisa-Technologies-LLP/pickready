"""Reading a candidate's raw answers leaves a record (PLAN-p5 WP5-F).

The transcript route returns every question a candidate was asked and every
answer they gave, verbatim. It is the most personal read in the recruiter's
product, and until this release it left no trace. It now writes ONE audit row
in the request's transaction, after the tenant and closure gates (a refused
request read nothing) and before the answers are loaded.

Asserted from a SECOND connection after the route's transaction committed: an
audit row that answered and then rolled back is invisible to an assertion on
the response, which is the failure `test_audit_single_insert_api.py` exists
for.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from tests.test_prism_pdf_g4 import _http_request
from tests.test_retention_consent import (
    _cleanup,
    _factory_or_skip,
    _Fixture,
    _seed,
    _staff_user,
)


async def _seed_conversation(factory, fx: _Fixture) -> None:
    from app.core.db import superadmin_scope
    from app.models.assessment import AssessmentConversation, AssessmentMessage

    conversation_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                s.add(
                    AssessmentConversation(
                        id=conversation_id, tenant_id=fx.tenant_id, job_id=fx.job_id,
                        job_candidate_link_id=fx.link_id, grade="non_managerial",
                        status="completed", next_question_index=0, started_at=now,
                    )
                )
                await s.flush()
                for ordinal, (speaker, content) in enumerate(
                    (
                        ("agent", "How did you run the orders migration?"),
                        ("candidate", "I planned the partitions and wrote the rollback."),
                    ),
                    1,
                ):
                    s.add(
                        AssessmentMessage(
                            tenant_id=fx.tenant_id, conversation_id=conversation_id,
                            ordinal=ordinal, speaker=speaker, domain="technical",
                            question_key="q", content=content,
                        )
                    )


async def _read(factory, fx: _Fixture, *, limit: int = 50, offset: int = 0):
    from app.api import assessment_reports
    from app.core.db import superadmin_scope

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                return await assessment_reports.get_transcript(
                    fx.link_id,
                    request=_http_request(
                        path=f"/api/v2/assessments/transcripts/links/{fx.link_id}"
                    ),
                    limit=limit,
                    offset=offset,
                    user=_staff_user(fx),
                    session=s,
                )


async def _rows(factory, fx: _Fixture) -> list:
    from app.core.db import superadmin_scope
    from app.services import audit

    async with factory() as s:
        async with superadmin_scope(s):
            return (
                await s.execute(
                    text(
                        "SELECT action, target_type, target_id, application_id, "
                        "candidate_id, actor_user_id, request_method, request_path "
                        "FROM audit_log "
                        "WHERE application_id = :a AND action = :action"
                    ),
                    {"a": str(fx.link_id), "action": audit.ASSESSMENT_TRANSCRIPT_VIEWED},
                )
            ).all()


@pytest.mark.asyncio
async def test_one_committed_row_per_read_naming_who_read_whose_answers() -> None:
    engine, factory = await _factory_or_skip()
    fx = _Fixture()
    try:
        await _seed(factory, fx)
        await _seed_conversation(factory, fx)
        page = await _read(factory, fx, limit=50, offset=0)
        assert page.exchanges[0].answer == "I planned the partitions and wrote the rollback."

        [row] = await _rows(factory, fx)
        assert row.target_type == "job_candidate_link"
        assert str(row.target_id) == str(fx.link_id)
        assert str(row.candidate_id) == str(fx.cand_id)
        assert str(row.actor_user_id) == str(fx.staff_user_id)
        assert row.request_method == "GET"
        assert row.request_path.endswith(f"/transcripts/links/{fx.link_id}")

        # A second page is a second read of the candidate's words.
        await _read(factory, fx, limit=1, offset=1)
        assert len(await _rows(factory, fx)) == 2
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_refused_read_leaves_no_row() -> None:
    """410 after closure: the gate runs before the audit write, so a refusal
    that read nothing records nothing."""
    from app.core.db import superadmin_scope
    from app.models import Job

    engine, factory = await _factory_or_skip()
    fx = _Fixture()
    try:
        await _seed(factory, fx)
        await _seed_conversation(factory, fx)
        now = datetime.now(timezone.utc)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    job = await s.get(Job, fx.job_id)
                    job.closed_at = now
                    job.assessment_purge_due_at = now + timedelta(days=30)
        with pytest.raises(HTTPException) as caught:
            await _read(factory, fx)
        assert caught.value.status_code == 410
        assert await _rows(factory, fx) == []
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()
