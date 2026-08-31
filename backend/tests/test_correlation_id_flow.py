"""One correlation id, followed through every stage of one flow.

WHY THIS TEST IS THE DELIVERABLE AND NOT A NICETY
---------------------------------------------------
spec-doc6 4.1 names it: "A correlation ID issued at job creation must be
traceable through Bodha, Sutra, Yukti, Vaada, Miti and Siddhi, and must appear
in every audit row and log line for that flow. Write one test that follows a
single correlation ID through the whole flow and asserts it appears at every
stage."

The failure it prevents is a trace that looks complete. Before this, each agent
recorded itself under its own workflow id, so "what happened to this candidate"
was six unrelated queries whose answers could not be joined -- while every
individual log line looked perfectly well-formed. A per-agent id is not a weaker
version of a flow id; it is a different thing that reads like one.

WHAT "AT EVERY STAGE" IS CHECKED AGAINST
------------------------------------------
Four independent surfaces, because a flow that is traceable in one and not the
others is not traceable:

  1. the ENVELOPE each agent runs under,
  2. the ARTIFACT each agent publishes,
  3. the LEDGER record each stage leaves, which carries the artifact id and so
     is evidence of work rather than of time,
  4. the AUDIT ROW and the LOG LINE, which are the two durable surfaces an
     operator actually queries months later.

A TIMESTAMP IS NOT EVIDENCE THAT WORK HAPPENED. Every assertion below is
against a row, an artifact id or a recorded string. None is against an `at`.
"""
from __future__ import annotations

import logging
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models.tenant import AuditLog
from app.services import audit as audit_mod
from app.services.agents import artifacts as a2a
from app.services.agents import envelope as run_envelope
from app.services.agents import identity, provenance
from app.services.orchestration import enforcement

TENANT = uuid.uuid4()
JOB = uuid.uuid4()
CANDIDATE = uuid.uuid4()
HUMAN = uuid.uuid4()

PRINCIPAL = provenance.Principal(
    user_id=str(HUMAN), role="recruitment_manager", tenant_id=str(TENANT)
)

#: The six agent stages, in activation order, with the artifact each publishes
#: and a payload carrying exactly the fields that artifact type requires. The
#: payloads are minimal on purpose: this test is about the envelope the work
#: travels in, and a rich fixture would make a contract failure look like a
#: content failure.
FLOW: tuple[tuple[str, str, str, dict], ...] = (
    (
        provenance.STAGE_SWOT,
        identity.BODHA,
        "swot_evidence",
        {
            "strengths": ["ships without supervision"],
            "weaknesses": ["no one owns data quality"],
            "opportunities": ["new region launching"],
            "threats": ["two competitors hiring the same profile"],
            "sources": ["hiring_manager_session"],
        },
    ),
    (
        provenance.STAGE_MATRIX,
        identity.SUTRA,
        "tatva_matrix",
        {"must_have": [], "nice_to_have": [], "behavioural": []},
    ),
    (
        provenance.STAGE_PRESCREEN,
        identity.YUKTI,
        "ai_score",
        {"categories": []},
    ),
    (
        provenance.STAGE_CONVERSATION,
        identity.VAADA,
        "answer_event",
        {"question_key": "data_quality_ownership", "answer": "recorded"},
    ),
    (
        provenance.STAGE_SCORING,
        identity.MITI,
        "scoring_state",
        {"item_grades": []},
    ),
    (
        provenance.STAGE_REPORT,
        identity.SIDDHI,
        "prism_report",
        {
            "ai_score": {},
            "ppi_assessment": {},
            "validation": {},
            "gap_analysis": [],
        },
    ),
)


def _envelope(agent_id: str, correlation_id: str) -> run_envelope.Envelope:
    return run_envelope.Envelope.for_run(
        tenant_id=str(TENANT),
        agent_id=agent_id,
        task_type="scoring",
        interactive=False,
        job_id=str(JOB),
        candidate_id=str(CANDIDATE),
        principal=PRINCIPAL,
        correlation_id=correlation_id,
    )


def _publish(
    agent_id: str, artifact_type: str, payload: dict, envelope: run_envelope.Envelope
) -> a2a.Artifact:
    return a2a.publish(
        producer=agent_id,
        artifact_type=artifact_type,
        payload=payload,
        tenant_id=str(TENANT),
        job_id=str(JOB),
        candidate_id=str(CANDIDATE),
        source_refs=(f"jobs:{JOB}",),
        validated=True,
        correlation_id=envelope.correlation_id,
        task_id=envelope.task_id,
        principal=PRINCIPAL,
    )


async def _run_whole_flow(
    ledger: provenance.Ledger,
) -> tuple[list[run_envelope.Envelope], list[a2a.Artifact]]:
    """Bodha through Siddhi, every stage through the one enforcement door."""
    envelopes: list[run_envelope.Envelope] = []
    published: list[a2a.Artifact] = []
    for stage, agent_id, artifact_type, payload in FLOW:
        envelope = _envelope(agent_id, ledger.correlation_id)
        artifact = _publish(agent_id, artifact_type, payload, envelope)
        await enforcement.run_stage(stage, envelope, ledger, artifact=artifact)
        envelopes.append(envelope)
        published.append(artifact)
    return envelopes, published


# ── 1. The id is derived from the job, so it is reconstructible ──────────────


def test_the_correlation_id_is_derived_from_the_job_and_not_minted() -> None:
    """Two calls, one id. A minted id would give a re-run its own flow."""
    assert provenance.correlation_for_job(JOB) == provenance.correlation_for_job(JOB)
    assert provenance.correlation_for_job(JOB).startswith("job-")
    assert provenance.is_correlation_id(provenance.correlation_for_job(JOB))


def test_a_raw_job_id_is_not_a_correlation_id() -> None:
    """The failure this catches: a caller threading `str(job.id)` into the
    correlation slot. It is hex, it reads correctly in a log line, and it joins
    the stage to no audit row at all."""
    assert not provenance.is_correlation_id(str(JOB))
    assert not provenance.is_correlation_id(uuid.uuid4().hex)
    assert not provenance.is_correlation_id("workflow-" + uuid.uuid4().hex)


# ── 2. It survives every stage, on every surface ─────────────────────────────


@pytest.mark.asyncio
async def test_one_correlation_id_reaches_all_six_agents() -> None:
    correlation_id = provenance.correlation_for_job(JOB)
    ledger = provenance.Ledger(correlation_id)

    envelopes, published = await _run_whole_flow(ledger)

    # The six agents, named. Asserted as a set against the identity table
    # rather than against a literal list, so an agent added to the pipeline
    # without a stage here fails rather than being quietly untraced.
    assert {e.agent_id for e in envelopes} == set(identity.AGENTS)

    assert all(e.correlation_id == correlation_id for e in envelopes)
    assert all(a.correlation_id == correlation_id for a in published)
    assert all(r.correlation_id == correlation_id for r in ledger.records)

    assert ledger.stages() == tuple(stage for stage, _, _, _ in FLOW)


@pytest.mark.asyncio
async def test_every_stage_records_the_artifact_it_produced() -> None:
    """A stage with no artifact is a timestamp, and this project measured 19 of
    35 live jobs carrying a generation stamp with zero rows behind it."""
    ledger = provenance.Ledger(provenance.correlation_for_job(JOB))
    _, published = await _run_whole_flow(ledger)

    recorded = {r.artifact_id for r in ledger.records}
    assert recorded == {a.artifact_id for a in published}
    assert None not in recorded


@pytest.mark.asyncio
async def test_a_sub_task_stays_inside_its_parents_flow() -> None:
    """`Envelope.child` copies the correlation id. Re-minting would give a
    sub-task a flow of its own, which is the state the field exists to end."""
    correlation_id = provenance.correlation_for_job(JOB)
    parent = _envelope(identity.MITI, correlation_id)
    child = parent.child(identity.MITI)

    assert child.correlation_id == correlation_id
    assert child.principal == parent.principal
    # A distinct execution inside the same flow, which is the whole point of
    # having both ids.
    assert child.execution_id != parent.execution_id
    assert child.parent_task_id == parent.task_id


@pytest.mark.asyncio
async def test_a_stage_carrying_another_flows_id_is_refused() -> None:
    """The copy-paste failure: a second flow's id threaded into the first
    flow's stage merges two traces into one plausible-looking one."""
    ledger = provenance.Ledger(provenance.correlation_for_job(JOB))
    other = provenance.correlation_for_job(uuid.uuid4())
    envelope = _envelope(identity.SUTRA, other)
    artifact = _publish(
        identity.SUTRA,
        "tatva_matrix",
        {"must_have": [], "nice_to_have": [], "behavioural": []},
        envelope,
    )

    with pytest.raises(ValueError):
        await enforcement.run_stage(
            provenance.STAGE_MATRIX, envelope, ledger, artifact=artifact
        )
    assert len(ledger) == 0


@pytest.mark.asyncio
async def test_an_artifact_published_under_a_different_flow_is_refused() -> None:
    """The envelope and the artifact must agree. One flow, one id."""
    correlation_id = provenance.correlation_for_job(JOB)
    ledger = provenance.Ledger(correlation_id)
    envelope = _envelope(identity.SUTRA, correlation_id)
    stray = _publish(
        identity.SUTRA,
        "tatva_matrix",
        {"must_have": [], "nice_to_have": [], "behavioural": []},
        _envelope(identity.SUTRA, provenance.correlation_for_job(uuid.uuid4())),
    )

    with pytest.raises(enforcement.StageRefused):
        await enforcement.run_stage(
            provenance.STAGE_MATRIX, envelope, ledger, artifact=stray
        )
    assert len(ledger) == 0


# ── 3. The log line, which is one of the two durable surfaces ────────────────


@pytest.mark.asyncio
async def test_every_stage_writes_the_correlation_id_into_its_log_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    correlation_id = provenance.correlation_for_job(JOB)
    ledger = provenance.Ledger(correlation_id)

    with caplog.at_level(logging.INFO, logger="pickready.orchestration"):
        await _run_whole_flow(ledger)

    lines = [r.getMessage() for r in caplog.records]
    assert len(lines) == len(FLOW)
    assert all(f"correlation_id={correlation_id}" in line for line in lines)
    for stage, _, _, _ in FLOW:
        assert any(f"stage={stage}" in line for line in lines)


@pytest.mark.asyncio
async def test_the_log_line_carries_identifiers_and_never_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A trace carries identifiers, counts and timings, and NEVER content. The
    answer text below is a real payload field, and it must not reach the log."""
    correlation_id = provenance.correlation_for_job(JOB)
    ledger = provenance.Ledger(correlation_id)
    envelope = _envelope(identity.VAADA, correlation_id)
    artifact = _publish(
        identity.VAADA,
        "answer_event",
        {
            "question_key": "data_quality_ownership",
            "answer": "I rebuilt the ingestion pipeline over eleven weeks",
        },
        envelope,
    )

    with caplog.at_level(logging.INFO, logger="pickready.orchestration"):
        await enforcement.run_stage(
            provenance.STAGE_CONVERSATION, envelope, ledger, artifact=artifact
        )

    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "ingestion pipeline" not in joined
    assert "eleven weeks" not in joined
    assert correlation_id in joined


def test_log_fields_drops_a_key_that_is_not_on_the_allowlist() -> None:
    """The next person adding "the prompt we sent" for debugging finds it
    dropped, rather than finding it in a log aggregator a month later."""
    line = provenance.log_fields(
        correlation_id=provenance.correlation_for_job(JOB),
        stage=provenance.STAGE_SCORING,
        prompt="you are an expert evaluator",
        answer="I led the migration",
        job_id=str(JOB),
    )
    assert "expert evaluator" not in line
    assert "led the migration" not in line
    assert f"job_id={JOB}" in line


# ── 4. The audit row, which is the other durable surface ─────────────────────


async def _db_or_skip():
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except Exception:
        await engine.dispose()
        pytest.skip("no database reachable, skipping the audit half of the flow")
    return engine


@pytest.mark.asyncio
async def test_every_audit_row_for_the_flow_carries_the_same_correlation_id() -> None:
    """The join that makes a flow reconstructible months later.

    One query, filtered on the correlation id, returns every stage. That is the
    property spec-doc6 4.1 asks for, and it is the one a per-agent workflow id
    cannot provide however well-formed each individual row looks.
    """
    engine = await _db_or_skip()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    correlation_id = provenance.correlation_for_job(JOB)
    ledger = provenance.Ledger(correlation_id)

    try:
        await _run_whole_flow(ledger)
        async with factory() as session:
            for record in ledger.records:
                await audit_mod.record_agent_action(
                    session,
                    action=f"agent_{record.stage}",
                    agent_name=record.agent_id or "unknown",
                    principal_user_id=record.principal_user_id,
                    principal_role=record.principal_role,
                    tenant_id=record.tenant_id,
                    resource_type="artifact",
                    resource_id=record.artifact_id,
                    job_id=record.job_id,
                    candidate_id=record.candidate_id,
                    correlation_id=record.correlation_id,
                )
            await session.commit()

        async with factory() as session:
            rows = (
                await session.execute(
                    select(AuditLog).where(AuditLog.correlation_id == correlation_id)
                )
            ).scalars().all()

            assert len(rows) == len(FLOW)
            # RBAC 34: both principals on every row. The human is never
            # replaced by the agent name.
            assert all(str(r.actor_user_id) == str(HUMAN) for r in rows)
            assert {r.agent_name for r in rows} == set(identity.AGENTS)
            assert {r.action for r in rows} == {
                f"agent_{stage}" for stage, _, _, _ in FLOW
            }

            for row in rows:
                await session.delete(row)
            await session.commit()
    finally:
        await engine.dispose()
