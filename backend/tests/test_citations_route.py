"""The citations read: what each PRISM Report statement rests on (PLAN-p5 WP5-F).

`GET /api/v2/assessments/reports/links/{link_id}/citations` resolves the
stored trail's locators to the candidate's own words at READ time. It is the
route behind "click a remark to see the answer", so it carries the same
obligations as the transcript it quotes from:

* the tenant check answers 404 across tenants, never 403;
* the job-closure gate answers 410 once the job is closed, BEFORE anything is
  resolved;
* every read writes ONE audit row in the request's transaction, asserted from
  a SECOND connection after the route's transaction ended;
* the payload is words and the candidate's own text only: no id, no locator,
  no position, and no number (the trail's excerpt cap is asserted too);
* a report written before the trail existed says so (`trail_available`
  false), which is a different answer from an empty trail.

Reuses the retention-consent fixture for the tenant, job, application and
report, and replaces the seeded report with one carrying a trail the way
`test_prism_pdf_g4.py` does: a report is never UPDATEd.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from tests.test_prism_pdf_g4 import _audit_rows, _http_request
from tests.test_retention_consent import (
    _cleanup,
    _factory_or_skip,
    _Fixture,
    _seed,
    _staff_user,
)

ANSWER = (
    "I moved the orders service onto Kafka and wrote the rollback myself. "
    + "The partition plan was the hard part. " * 40
)
REMARK = "Described owning the orders migration end to end, rollback included."
SKILL = "Event streaming"


async def _seed_trail(factory, fx: _Fixture) -> uuid.UUID:
    """A completed conversation with one answer, and a report whose trail
    cites it. Returns the message id so the test can assert it never leaks."""
    from app.core.db import superadmin_scope
    from app.models.assessment import (
        AssessmentConversation,
        AssessmentMessage,
        FunctionalSkillsReport,
    )
    from app.services.siddhi import citations, evidence

    conversation_id, message_id = uuid.uuid4(), uuid.uuid4()
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
                s.add(
                    AssessmentMessage(
                        id=message_id, tenant_id=fx.tenant_id,
                        conversation_id=conversation_id, ordinal=2,
                        speaker="candidate", domain="technical", question_key="q",
                        content=ANSWER,
                    )
                )
                seeded = await s.get(FunctionalSkillsReport, fx.report_id)
                summary, written_at = seeded.overall_summary, seeded.synthesized_at
                await s.delete(seeded)
                await s.flush()
                s.add(
                    FunctionalSkillsReport(
                        id=fx.report_id, tenant_id=fx.tenant_id, job_id=fx.job_id,
                        job_candidate_link_id=fx.link_id, grade="non_managerial",
                        overall_summary=summary, validation_json={},
                        synthesized_at=written_at,
                        gap_analysis_json={
                            "groups": [],
                            "siddhi": {
                                "citations": {
                                    "version": 2,
                                    "evidence_nodes": [
                                        {
                                            "ref": "answer:streaming:0",
                                            "kind": evidence.KIND_ANSWER,
                                            "item": SKILL,
                                            "locators": [
                                                f"{evidence.LOCATOR_MESSAGE}:{message_id}"
                                            ],
                                        }
                                    ],
                                    "statements": [
                                        {
                                            "section": "must_have",
                                            "kind": citations.KIND_FINDING,
                                            "item": SKILL,
                                            "text": REMARK,
                                            "evidence_refs": ["answer:streaming:0"],
                                        }
                                    ],
                                }
                            },
                        },
                    )
                )
    return message_id


async def _read(factory, fx: _Fixture, user=None):
    from app.api import assessment_reports
    from app.core.db import superadmin_scope

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                return await assessment_reports.get_report_citations(
                    link_id=fx.link_id,
                    request=_http_request(
                        path=f"/api/v2/assessments/reports/links/{fx.link_id}/citations"
                    ),
                    user=user or _staff_user(fx),
                    session=s,
                )


@pytest.mark.asyncio
async def test_a_remark_resolves_to_the_answer_it_cites_and_the_read_is_audited() -> None:
    from app.services import audit
    from app.services.siddhi import trail

    engine, factory = await _factory_or_skip()
    fx = _Fixture()
    try:
        await _seed(factory, fx)
        message_id = await _seed_trail(factory, fx)
        out = await _read(factory, fx)

        assert out.trail_available is True
        [statement] = out.statements
        assert (statement.section, statement.item, statement.text) == (
            "must_have",
            SKILL,
            REMARK,
        )
        [cited] = statement.evidence
        assert cited.kind == trail.EVIDENCE_KIND_WORDS[trail.KIND_ANSWER]
        assert cited.excerpt is not None
        assert cited.excerpt.startswith("I moved the orders service onto Kafka")
        # Capped, so one long answer cannot turn a citation into a transcript.
        assert len(cited.excerpt) <= trail.EXCERPT_CHARS

        # Words and the candidate's own text only: no id, no locator.
        body = out.model_dump_json()
        assert str(message_id) not in body
        assert "assessment_messages" not in body
        assert "answer:streaming" not in body
        assert not re.search(r"\d", body), body

        rows = await _audit_rows(factory, fx, audit.PRISM_CITATIONS_VIEWED)
        assert len(rows) == 1
        assert rows[0].target_type == "functional_skills_report"
        assert str(rows[0].candidate_id) == str(fx.cand_id)
        assert rows[0].request_method == "GET"
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_report_written_before_the_trail_says_so() -> None:
    engine, factory = await _factory_or_skip()
    fx = _Fixture()
    try:
        await _seed(factory, fx)
        out = await _read(factory, fx)
        assert out.trail_available is False
        assert out.statements == []
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_another_tenants_report_is_404_and_nothing_is_audited() -> None:
    from app.api.deps import CurrentUser
    from app.core.security import AUDIENCE_ORG
    from app.models import Role
    from app.services import audit

    engine, factory = await _factory_or_skip()
    fx = _Fixture()
    try:
        await _seed(factory, fx)
        await _seed_trail(factory, fx)
        stranger = CurrentUser(
            user_id=fx.staff_user_id, tenant_id=uuid.uuid4(),
            role=Role.client, audience=AUDIENCE_ORG,
        )
        with pytest.raises(HTTPException) as caught:
            await _read(factory, fx, user=stranger)
        assert caught.value.status_code == 404
        assert await _audit_rows(factory, fx, audit.PRISM_CITATIONS_VIEWED) == []
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_closed_job_answers_410_before_anything_is_resolved() -> None:
    from app.core.db import superadmin_scope
    from app.models import Job
    from app.services import audit

    engine, factory = await _factory_or_skip()
    fx = _Fixture()
    try:
        await _seed(factory, fx)
        await _seed_trail(factory, fx)
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
        assert await _audit_rows(factory, fx, audit.PRISM_CITATIONS_VIEWED) == []
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()
