"""One seeded tenant with a resume and a transcript, for the Evidence RAG tests.

Shared by `test_evidence_retrieval_through_tools.py`,
`test_semantic_index_repair.py`, `test_index_document_stamps_provenance.py`
and `test_tool_extract_project_evidence.py`, so the four describe the same
world and a fifth does not grow a sixth copy of the seed. Every id is minted
up front, so cleanup is total and a failed assertion never leaves a row behind
for the next module to trip on.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

RESUME = """
EXPERIENCE

Senior Engineer, Northwind Data. Ran the streaming ingestion platform on Kafka
across three regions, owning the partition strategy and the consumer group
rebalance through a migration from a single cluster to a federated pair.

EDUCATION

B.Tech in Computer Science, with a final year project on consensus protocols
and a dissertation on partition tolerance in geographically split clusters.
"""

#: (question, answer) pairs, in order. The exchange INDEX is the chunk ordinal.
EXCHANGES: tuple[tuple[str, str], ...] = (
    (
        "How did you handle consumer group rebalance storms during the regional "
        "migration?",
        "We drained one region at a time and pinned the Kafka group instance id "
        "so a rolling deploy stopped triggering a full rebalance of the topic.",
    ),
    (
        "Tell me about a time you disagreed with a senior colleague.",
        "I disagreed with our architect about retiring the single cluster early. "
        "I wrote the replay numbers down, we reviewed them together, and agreed.",
    ),
    (
        "What did the Kafka cutover cost you in replay time?",
        "About forty minutes on the first Kafka cutover and under five on every "
        "one after it, once the consumer offsets were migrated ahead of time.",
    ),
)


class World:
    def __init__(self) -> None:
        self.tenant_id = uuid.uuid4()
        self.job_id = uuid.uuid4()
        self.candidate_id = uuid.uuid4()
        self.profile_id = uuid.uuid4()
        self.link_id = uuid.uuid4()
        self.conversation_id = uuid.uuid4()
        #: Answer message ids in exchange order, filled by `seed`.
        self.answer_ids: list[uuid.UUID] = []


def disable_prefixes(monkeypatch) -> None:
    """Index without the contextual prefix model call.

    With no model credential every prefix attempt runs the router to
    `LLMUnavailableError`, which costs tens of seconds per document and tests
    nothing these modules are about. `contextual.enabled` is the switch the
    module itself reads (`RETRIEVAL_CONTEXTUAL_PREFIX`), so this is the
    deployment-off state, not a fake of the generator.
    """
    from app.services.rag import contextual

    monkeypatch.setattr(contextual, "enabled", lambda: False)


async def factory_or_skip():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001 -- the suite's standard reachability probe
        await engine.dispose()
        pytest.skip("no database reachable")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def seed(factory, world: World) -> None:
    from app.core.db import superadmin_scope
    from app.models import Candidate, Job, JobStatus, LinkSource, Tenant
    from app.models.assessment import AssessmentConversation, AssessmentMessage
    from app.models.candidate import JobCandidateLink, Profile

    now = datetime.now(timezone.utc)
    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                s.add(Tenant(id=world.tenant_id,
                             name=f"Rag {world.tenant_id.hex[:6]}",
                             domain=f"{world.tenant_id}.rag.test"))
                await s.flush()
                s.add(Job(id=world.job_id, tenant_id=world.tenant_id,
                          title="Staff Data Engineer", jd_json={},
                          jd_markdown="Own the streaming platform.",
                          status=JobStatus.ratified, ratified_at=now,
                          assessment_grade="non_managerial"))
                s.add(Candidate(id=world.candidate_id,
                                email=f"c{world.candidate_id.hex[:8]}@rag.test",
                                full_name="Retrieved Candidate",
                                consent_databank=False))
                await s.flush()
                s.add(Profile(id=world.profile_id, candidate_id=world.candidate_id,
                              source_tenant_id=world.tenant_id, resume_text=RESUME))
                await s.flush()
                s.add(JobCandidateLink(id=world.link_id, tenant_id=world.tenant_id,
                                       job_id=world.job_id,
                                       candidate_id=world.candidate_id,
                                       source=LinkSource.fresh, status="applied"))
                await s.flush()
                s.add(AssessmentConversation(
                    id=world.conversation_id, tenant_id=world.tenant_id,
                    job_id=world.job_id, job_candidate_link_id=world.link_id,
                    grade="non_managerial", status="active",
                    next_question_index=0, started_at=now))
                await s.flush()
                ordinal = 0
                world.answer_ids = []
                for question, answer in EXCHANGES:
                    ordinal += 1
                    s.add(AssessmentMessage(
                        tenant_id=world.tenant_id,
                        conversation_id=world.conversation_id, ordinal=ordinal,
                        speaker="agent", domain="technical", question_key="k",
                        content=question))
                    ordinal += 1
                    answer_id = uuid.uuid4()
                    world.answer_ids.append(answer_id)
                    s.add(AssessmentMessage(
                        id=answer_id, tenant_id=world.tenant_id,
                        conversation_id=world.conversation_id, ordinal=ordinal,
                        speaker="candidate", domain="technical", question_key="k",
                        content=answer))


async def index_all(factory, world: World) -> None:
    """Index the resume and the transcript through the real loaders."""
    from app.core.db import superadmin_scope
    from app.services.rag import index, sources

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                for source_type, source_id in (
                    ("resume", world.profile_id),
                    ("assessment", world.link_id),
                ):
                    document = await sources.load(
                        s, source_type=source_type, source_id=source_id
                    )
                    assert document is not None, source_type
                    await index.index_document(
                        s,
                        tenant_id=document.tenant_id,
                        source_type=document.source_type,
                        source_id=document.source_id,
                        document=document.text,
                        chunks=document.chunks,
                    )


async def chunk_rows(factory, source_id: uuid.UUID) -> list:
    from app.core.db import superadmin_scope

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                return (
                    await s.execute(
                        text(
                            "SELECT id, ordinal, content_sha256, "
                            "       embedding IS NOT NULL AS has_vector, "
                            "       embedding_model, embedding_contract_version, "
                            "       embedding_generated_at "
                            "  FROM context_chunks WHERE source_id = :s "
                            " ORDER BY ordinal"
                        ),
                        {"s": str(source_id)},
                    )
                ).all()


async def cleanup(factory, world: World) -> None:
    from app.core.db import superadmin_scope

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                await s.execute(
                    text("DELETE FROM context_chunks "
                         "WHERE source_id = ANY(CAST(:s AS uuid[]))"),
                    {"s": [str(world.profile_id), str(world.link_id),
                           str(world.job_id)]},
                )
                await s.execute(text("DELETE FROM tenants WHERE id = :t"),
                                {"t": str(world.tenant_id)})
                await s.execute(text("DELETE FROM candidates WHERE id = :c"),
                                {"c": str(world.candidate_id)})
