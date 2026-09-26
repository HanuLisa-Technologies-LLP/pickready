"""The citation trail's read model: typed, scoped, and words-only at the edge.

`read_trail` is pure and reads both trail versions. `resolve_evidence` turns
durable locators into text at read time and is SCOPED to the report's own
application: the database test below plants a locator pointing at another
candidate's message in the same tenant and asserts it resolves to nothing.
`view` is the client shape and carries no ref, locator, id or position.
"""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.services.siddhi import inputs, trail
from app.services.siddhi import report as siddhi_report


def _v2_json(message_id: str, question_id: str) -> dict:
    composed = asyncio.run(
        siddhi_report.compose_prism(
            dimensions=[
                {
                    "category": "must_have",
                    "name": "Distributed Systems",
                    "grade": "Matching",
                    "remark": "They led the Kubernetes rollout for payments.",
                }
            ],
            evidence_by_item={
                "Distributed Systems": [
                    {
                        "question": "A migration you owned?",
                        "answer": "I moved the orders service and wrote the rollback runbook.",
                        "question_id": question_id,
                        "message_ids": [message_id],
                    }
                ]
            },
            embed=None,
        )
    )
    return {"groups": [], "siddhi": composed.siddhi_namespace()}


def test_a_report_with_no_trail_says_so_rather_than_reading_as_uncited() -> None:
    for stored in (None, {}, {"groups": []}, {"siddhi": {}}):
        assert trail.read_trail(stored).available is False


def test_a_version_one_trail_still_reads() -> None:
    """Every report written before the Vivekium release: no version, no item,
    no locators, no support. It reads, and nothing is invented for the gaps."""
    stored = {
        "siddhi": {
            "citations": {
                "evidence_nodes": [
                    {"ref": "answer:kafka:0", "kind": "answer", "item": "Kafka"}
                ],
                "statements": [
                    {
                        "section": "must_have",
                        "kind": "finding",
                        "text": "Named the partitions.",
                        "evidence_refs": ["answer:kafka:0"],
                    }
                ],
            }
        }
    }
    read = trail.read_trail(stored)
    assert read.available and read.version == 1
    [statement] = read.statements
    assert statement.item == ""
    assert statement.support_level is None
    assert statement.support_note is None
    assert read.nodes["answer:kafka:0"].locators == ()


def test_a_version_two_trail_carries_items_locators_and_the_support_marker() -> None:
    message_id, question_id = str(uuid.uuid4()), str(uuid.uuid4())
    read = trail.read_trail(_v2_json(message_id, question_id))
    assert read.version == siddhi_report.TRAIL_VERSION
    note = read.remark_support_note("must_have", "Distributed Systems")
    # "Kubernetes" is nowhere in the answer: the marker is the unsupported one.
    assert note == trail.support.SUPPORT_NOTES["unsupported"]
    answer = next(node for node in read.nodes.values() if node.kind == "answer")
    assert answer.locators == (f"assessment_messages:{message_id}",)


def test_a_malformed_stored_trail_raises_rather_than_rendering() -> None:
    with pytest.raises((KeyError, TypeError)):
        trail.read_trail({"siddhi": {"citations": {"statements": [{"kind": "finding"}]}}})


def test_the_client_view_carries_no_ref_locator_id_or_position() -> None:
    message_id, question_id = str(uuid.uuid4()), str(uuid.uuid4())
    read = trail.read_trail(_v2_json(message_id, question_id))
    ref = next(ref for ref, node in read.nodes.items() if node.kind == "answer")
    resolved = {
        ref: trail.ResolvedEvidence(kind="answer", excerpt="I moved the orders service.", turn=7),
    }
    shaped = trail.view(read, resolved)
    serialised = json.dumps(shaped)
    assert message_id not in serialised
    assert question_id not in serialised
    assert "assessment_messages" not in serialised
    assert "answer:" not in serialised and "searched:" not in serialised
    assert not re.search(r"\d", serialised)
    assert shaped["trail_available"] is True
    evidence = [entry for statement in shaped["statements"] for entry in statement["evidence"]]
    assert {"kind": "The candidate's answer", "question": None, "excerpt": "I moved the orders service."} in evidence


def test_inputs_carry_the_row_addresses_the_trail_needs() -> None:
    from types import SimpleNamespace

    competency = SimpleNamespace(id=uuid.uuid4(), name="Kafka")
    question = SimpleNamespace(id=uuid.uuid4(), competency_id=competency.id, prompt="Partitions?")
    records = {
        str(question.id): [
            SimpleNamespace(message_id=uuid.uuid4()),
            SimpleNamespace(message_id=uuid.uuid4()),
        ]
    }
    built = inputs.evidence_by_item(
        questions=[question],
        competencies=[competency],
        answers={str(question.id): ["First.", "Follow-up."]},
        answer_records=records,
    )
    [exchange] = built["Kafka"]
    assert exchange["answer"] == "First. Follow-up."
    assert exchange["question_id"] == str(question.id)
    assert exchange["message_ids"] == [str(r.message_id) for r in records[str(question.id)]]


def test_inputs_without_records_carry_no_addresses_and_never_invent_one() -> None:
    from types import SimpleNamespace

    competency = SimpleNamespace(id=uuid.uuid4(), name="Kafka")
    question = SimpleNamespace(id=uuid.uuid4(), competency_id=competency.id, prompt="Partitions?")
    built = inputs.evidence_by_item(
        questions=[question], competencies=[competency], answers={str(question.id): ["Yes."]}
    )
    assert built["Kafka"][0]["message_ids"] == []


# ── Resolution is scoped to the report's own application ─────────────────────


async def _factory_or_skip():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except OSError:
        await engine.dispose()
        pytest.skip("no database reachable")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_a_locator_can_never_resolve_to_another_applications_words() -> None:
    from app.core.db import superadmin_scope
    from app.models import Candidate, Job, JobStatus, LinkSource, Tenant
    from app.models.assessment import AssessmentConversation, AssessmentMessage
    from app.models.candidate import JobCandidateLink

    engine, factory = await _factory_or_skip()
    tenant_id, job_id = uuid.uuid4(), uuid.uuid4()
    links = {name: uuid.uuid4() for name in ("mine", "theirs")}
    candidates = {name: uuid.uuid4() for name in links}
    conversations = {name: uuid.uuid4() for name in links}
    messages = {name: uuid.uuid4() for name in links}
    now = datetime.now(timezone.utc)
    try:
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    s.add(Tenant(id=tenant_id, name=f"Trail {tenant_id.hex[:6]}",
                                 domain=f"{tenant_id}.trail.test"))
                    await s.flush()
                    s.add(Job(id=job_id, tenant_id=tenant_id, title="Engineer",
                              jd_json={}, status=JobStatus.ratified, ratified_at=now,
                              assessment_status="ready_for_candidates",
                              assessment_grade="non_managerial"))
                    for name in links:
                        s.add(Candidate(id=candidates[name],
                                        email=f"{name}{candidates[name].hex[:8]}@t.test",
                                        full_name=f"Candidate {name}", consent_databank=False))
                    await s.flush()
                    for name in links:
                        s.add(JobCandidateLink(id=links[name], tenant_id=tenant_id,
                                               job_id=job_id, candidate_id=candidates[name],
                                               source=LinkSource.fresh, status="applied"))
                    await s.flush()
                    for name in links:
                        s.add(AssessmentConversation(
                            id=conversations[name], tenant_id=tenant_id, job_id=job_id,
                            job_candidate_link_id=links[name], grade="non_managerial",
                            status="completed", next_question_index=0, started_at=now,
                        ))
                    await s.flush()
                    for name in links:
                        s.add(AssessmentMessage(
                            id=messages[name], tenant_id=tenant_id,
                            conversation_id=conversations[name], ordinal=2,
                            speaker="candidate", domain="technical", question_key="q",
                            content=f"{name} answer about the orders service",
                        ))

        stored = {
            "siddhi": {
                "citations": {
                    "version": 2,
                    "evidence_nodes": [
                        {"ref": "answer:x:0", "kind": "answer", "item": "X",
                         "locators": [f"assessment_messages:{messages['mine']}"]},
                        {"ref": "answer:x:1", "kind": "answer", "item": "X",
                         "locators": [f"assessment_messages:{messages['theirs']}"]},
                    ],
                    "statements": [],
                }
            }
        }
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    resolved = await trail.resolve_evidence(
                        s, trail.read_trail(stored), link_id=links["mine"]
                    )
        assert resolved["answer:x:0"].excerpt == "mine answer about the orders service"
        assert resolved["answer:x:0"].turn == 2
        assert resolved["answer:x:1"].excerpt is None, "another application's words leaked"
    finally:
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    await s.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": str(tenant_id)})
                    await s.execute(
                        text("DELETE FROM candidates WHERE id = ANY(:ids)"),
                        {"ids": [str(value) for value in candidates.values()]},
                    )
        await engine.dispose()
