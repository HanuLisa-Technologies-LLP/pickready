"""Miti's G1 and G4 against REAL rows (WP5-B).

* G1 reads the contract the conversation is BOUND to, through
  `load_contract_for_conversation`, and logs `stage=miti` with the same digest
  the conversation logged at its start (`stage=vaada`) and stored on its row.
* A conversation whose stored digest disagrees with its snapshot is REFUSED
  before a single model call and before a single ledger row is written.
* G4 takes the latest human disposition recorded on the application, read by
  the link, newest first.

The database half matters here because both refusals and the disposition read
are properties of rows another module writes. The world is seeded by the
contract suite's own helpers, so the two suites cannot disagree about what a
locked job looks like.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.core.db import superadmin_scope
from app.services import assessment_contract as contract_api
from app.services.hiring import gates
from app.services.miti import live
from tests.test_assessment_contract import _drop, _factory, _lock, _seed


def _digest_lines(caplog) -> list[dict[str, str]]:
    return [
        dict(part.split("=", 1) for part in record.getMessage().split()[1:])
        for record in caplog.records
        if record.getMessage().startswith("assessment_contract.digest ")
    ]


@pytest.mark.asyncio
async def test_miti_grades_the_bound_snapshot_and_logs_the_digest_vaada_logged(caplog) -> None:
    engine, factory = await _factory()
    w = await _seed(factory)
    try:
        caplog.set_level(logging.INFO, logger=contract_api.__name__)
        conversation = w.conversations[0]
        at_start = await _lock(factory, w, conversation)
        contract_api.log_digest(contract_api.STAGE_VAADA, conversation, at_start)

        async with factory() as session:
            async with superadmin_scope(session):
                graded = await live.load_contract(session, conversation)
                stored = (
                    await session.execute(
                        text("SELECT contract_digest FROM assessment_conversations WHERE id = :c"),
                        {"c": conversation},
                    )
                ).scalar_one()

        lines = _digest_lines(caplog)
        assert [line["stage"] for line in lines] == ["vaada", "miti"]
        assert {line["contract_digest"] for line in lines} == {at_start.digest}
        assert graded.digest == at_start.digest == stored
        assert graded.locked and graded.version == 1
    finally:
        await _drop(factory, w)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_digest_mismatch_is_refused_before_any_model_call_or_ledger_write() -> None:
    engine, factory = await _factory()
    w = await _seed(factory)
    try:
        # REAL questions and answers, so a refusal that came AFTER the item
        # stage would have both called the judge and filed ledger rows. With
        # nothing to grade, "no call and no row" would hold vacuously.
        from tests.test_miti_live_rows import _issue

        conversation = w.conversations[0]
        await _issue(factory, w)
        async with factory() as session:
            async with superadmin_scope(session):
                await session.execute(
                    text("UPDATE assessment_conversations SET contract_digest = :d WHERE id = :c"),
                    {"d": "0" * 64, "c": conversation},
                )
                await session.commit()

        calls: list[str] = []

        async def _judge(task, messages, **kwargs):
            calls.append(task)
            return '{"score": 90}'

        from app.services import ppi_interview
        from app.services.assessment_pipeline import evidence as answer_evidence

        async with factory() as session:
            async with superadmin_scope(session):
                questions = await ppi_interview.load_for_link(session, w.links[0])
                locators = await answer_evidence.answer_records(session, w.links[0])
                assert questions and locators, "the refusal must have work it could have done"
                with pytest.raises(live.ScorecardUnavailable):
                    await live.evaluate_application(
                        session,
                        job=SimpleNamespace(id=w.job, tenant_id=w.tenant, title="Data Engineer"),
                        link=SimpleNamespace(id=w.links[0], candidate_id=w.candidates[0]),
                        conversation_id=conversation,
                        questions=questions,
                        answers={
                            key: [record.text for record in records]
                            for key, records in locators.items()
                        },
                        locators=locators,
                        structured={},
                        invoke=_judge,
                        item_invoke=_judge,
                    )
                # Commit whatever the refused run left in the session: if the
                # refusal had come after a ledger write, the write would now be
                # durable and the second connection below would see it.
                await session.commit()
        async with factory() as reader:
            async with superadmin_scope(reader):
                written = (
                    await reader.execute(
                        text("SELECT count(*) FROM evidence_items WHERE link_id = :l"),
                        {"l": w.links[0]},
                    )
                ).scalar_one()
        assert calls == []
        assert written == 0
    finally:
        await _drop(factory, w)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_started_conversation_with_no_binding_is_refused_by_miti() -> None:
    engine, factory = await _factory()
    w = await _seed(factory, started=True)
    try:
        async with factory() as session:
            async with superadmin_scope(session):
                with pytest.raises(live.ScorecardUnavailable):
                    await live.load_contract(session, w.conversations[0])
    finally:
        await _drop(factory, w)
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_latest_recorded_disposition_reaches_g4() -> None:
    engine, factory = await _factory()
    w = await _seed(factory)
    reviewer = uuid.uuid4()
    try:
        async with factory() as session:
            async with superadmin_scope(session):
                assert await live.latest_disposition(session, w.links[0]) == (None, None)
                await session.execute(
                    text(
                        "INSERT INTO users (id, tenant_id, email, full_name, role, status) "
                        "VALUES (:u, :t, :e, 'Reviewing Person', 'client', 'active')"
                    ),
                    {"u": reviewer, "t": w.tenant, "e": f"{reviewer.hex[:10]}@miti.test"},
                )
                now = datetime.now(timezone.utc)
                for disposition, at in (
                    (gates.DISPOSITION_ESCALATED, now - timedelta(hours=1)),
                    (gates.DISPOSITION_CLEARED, now),
                ):
                    await session.execute(
                        text(
                            "INSERT INTO review_dispositions (id, tenant_id, job_id, link_id, "
                            "disposition, decided_by, flags_json, created_at) VALUES "
                            "(:i, :t, :j, :l, :d, :by, '[]'::jsonb, :at)"
                        ),
                        {"i": uuid.uuid4(), "t": w.tenant, "j": w.job, "l": w.links[0],
                         "d": disposition, "by": reviewer, "at": at},
                    )
                await session.commit()

        async with factory() as session:
            async with superadmin_scope(session):
                disposition, decided_by = await live.latest_disposition(session, w.links[0])
        assert (disposition, decided_by) == (gates.DISPOSITION_CLEARED, reviewer)
        assert gates.human_review_gate(
            needs_review=True, disposition=disposition, decided_by=decided_by
        ).passed
    finally:
        async with factory() as session:
            async with superadmin_scope(session):
                await session.execute(
                    text("DELETE FROM review_dispositions WHERE link_id = :l"), {"l": w.links[0]}
                )
                await session.commit()
        await _drop(factory, w)
        await engine.dispose()
